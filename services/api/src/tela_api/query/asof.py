"""Data as-of resolution (US-021, US-041; RULE-018).

* **live**: the as-of time is the query time;
* **cached**: the time the cache entry was filled;
* **excel / csv**: the file version's upload time;
* **source_asof**: a declared time, or the newest value of a timestamp column
  (read through the same scoped path), overrides the above.

Date-relative pack rules use the reporting date: the model's configured
``reporting_date`` (S13 ``PMO_AS_OF``) or today in the tenant time zone. A
malformed configured date blocks the query with a configuration error.

**today** (US-163) is always the calendar date in the tenant time zone, even
when a reporting date is configured. ``pin_dates`` fixes it once per run: the
compilers see the reporting date shifted by the gap. Literal dates in an
ad-hoc rule become shifts of the reporting date the same way.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from tela_common.errors import ErrorCode, TelaError
from tela_query_spec.expr import AsOf, Compare, pin_today, split_ref, uses_today, walk
from tela_query_spec.model import TEMPORAL_TYPES

if TYPE_CHECKING:
    from tela_api.query.validator import ValidatedSpec

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")

FRESHNESS_MODES = ("live", "cached", "source_asof")


def reporting_date(settings: dict[str, Any], tz: str | None) -> date:
    configured = settings.get("reporting_date")
    if configured:
        try:
            return date.fromisoformat(str(configured))
        except ValueError as exc:
            raise TelaError(
                ErrorCode.CONFIGURATION_ERROR,
                "The reporting date for this model is not a valid date. Ask your admin to fix it.",
                {"admin_detail": {"reporting_date": str(configured)}},
            ) from exc
    return today_in(tz)


def today_in(tz: str | None) -> date:
    try:
        zone = ZoneInfo(tz) if tz else UTC
    except (KeyError, ValueError):
        zone = UTC
    return datetime.now(zone).date()


def pin_dates(vs: ValidatedSpec, *, as_of: date, today: date) -> ValidatedSpec:
    """The validated spec with ``today`` fixed for this run (measures and computed dimensions) and an ad-hoc rule's
    literal dates turned into reporting-date shifts. ``vs.today`` is set when the spec used today."""
    shift = (today - as_of).days
    used = any(uses_today(m.expression) for m in vs.measures) or any(
        uses_today(f.dimension.of) for f in [*(b.field for b in vs.breakdowns), *(f for f, _ in vs.filters)]
        if f.dimension is not None)
    adhoc = any(m.status == "adhoc" for m in vs.measures)
    if not used and not adhoc:
        return vs

    def measure(m: Any) -> Any:
        expr = pin_today(m.expression, shift)
        if m.status == "adhoc":
            expr = _literal_dates(expr, m.entity, vs, as_of)
        return m.model_copy(update={"expression": expr})

    def fix(fref: Any) -> Any:
        d = fref.dimension
        if d is None or not uses_today(d.of):
            return fref
        return replace(fref, dimension=d.model_copy(update={"of": pin_today(d.of, shift)}))

    return replace(vs, measures=[measure(m) for m in vs.measures], today=today if used else None,
                   breakdowns=[replace(b, field=fix(b.field)) for b in vs.breakdowns],
                   filters=[(fix(f), flt) for f, flt in vs.filters])


def _literal_dates(expr: Any, entity: str, vs: ValidatedSpec, as_of: date) -> Any:
    def to_shift(n: Any) -> Any:
        if not isinstance(n, Compare) or not isinstance(n.value, str) or not _ISO_DATE.match(n.value):
            return n
        ent_key, field_key = split_ref(entity, n.field)
        ent = vs.snapshot.entity(ent_key)
        f = ent.field(field_key) if ent else None
        if f is None or f.data_type not in TEMPORAL_TYPES:
            return n
        days = (date.fromisoformat(n.value[:10]) - as_of).days
        if abs(days) > 36_600:
            raise TelaError(ErrorCode.SPEC_INVALID, f"'{n.value}' is too far from today.")
        return n.model_copy(update={"value": AsOf(asof_offset_days=days)})

    return walk(expr, to_shift)


def reporting_date_or_none(settings: dict[str, Any], tz: str | None) -> str | None:
    """The reporting date as ISO text for "Data as of" (US-158), or None when the configured date is malformed (the
    widgets then say so themselves)."""
    try:
        return reporting_date(settings, tz).isoformat()
    except TelaError:
        return None


def cache_ttl_seconds(policy: dict[str, Any], default: int = 900) -> int:
    """Seconds a result may be served from cache under the connection's freshness policy (DEC-02)."""
    mode = policy.get("mode", "live")
    if mode == "cached":
        return int(policy.get("cache_ttl_minutes", 15)) * 60
    return int(policy.get("cache_ttl_s", default))


def declared_as_of(policy: dict[str, Any]) -> datetime | None:
    if policy.get("mode") != "source_asof" or not policy.get("declared_at"):
        return None
    value = datetime.fromisoformat(str(policy["declared_at"]))
    return value if value.tzinfo else value.replace(tzinfo=UTC)
