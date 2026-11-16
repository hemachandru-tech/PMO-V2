"""Tenant branding (ENH-006, US-149 to US-152): names, logos, palettes, logo sizes, history and roll back."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import ISSUER, bootstrap, needs_db
from test_brand_images_unit import SVG, png
from test_exports_api import MemoryStore

pytestmark = [needs_db, pytest.mark.integration]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(api: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str]) -> dict[str, Any]:
    tenant = bootstrap(owner_conn, "acme", ISSUER, {"TELA-Admins": "admin", "TELA-Viewers": "viewer"})
    store = MemoryStore()
    api.app.state.object_store = store  # type: ignore[attr-defined]
    return {"tenant": tenant, "store": store,
            "admin": make_token("admin-1", groups=["TELA-Admins"], name="Ada Admin"),
            "viewer": make_token("viewer-1", groups=["TELA-Viewers"], name="Vera Viewer")}


async def test_defaults_then_rename_shows_for_everyone_and_is_audited(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, world: dict[str, Any]
) -> None:
    viewer, admin = _h(world["viewer"]), _h(world["admin"])
    first = (await api.get("/api/v1/branding", headers=viewer)).json()
    assert (first["app_name"], first["tagline"], first["is_default"], first["version"]) == \
        ("Tela", "PMO analytics", True, 0)
    assert "updated_by" not in first

    saved = await api.put("/api/v1/admin/branding", headers=admin,
                          json={"app_name": "  Insights   Intelligence ", "tagline": "Portfolio insights",
                                "expected_version": 0})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert (body["app_name"], body["tagline"], body["version"], body["is_default"]) == \
        ("Insights Intelligence", "Portfolio insights", 1, False)
    assert body["updated_by"]["name"] == "Ada Admin"

    seen = (await api.get("/api/v1/branding", headers=viewer)).json()
    assert seen["app_name"] == "Insights Intelligence"
    public = (await api.get("/api/v1/branding/public", params={"issuer": ISSUER})).json()
    assert public["app_name"] == "Insights Intelligence"
    unknown = (await api.get("/api/v1/branding/public", params={"issuer": "https://other.test/realms/x"})).json()
    assert unknown["app_name"] == "Tela"

    stale = await api.put("/api/v1/admin/branding", headers=admin, json={"app_name": "Other", "expected_version": 0})
    assert stale.status_code == 409
    row = owner_conn.execute("SELECT details FROM ops.audit_events WHERE action = 'branding.updated'").fetchone()
    assert row is not None
    assert row[0]["change"] == "names"
    assert row[0]["before"]["app_name"] == "Tela"
    assert row[0]["after"]["app_name"] == "Insights Intelligence"


@pytest.mark.parametrize(("payload", "words"), [
    ({"app_name": "x"}, "2 to 40"),
    ({"app_name": "   "}, "2 to 40"),
    ({"app_name": "A" * 41}, "2 to 40"),
    ({"app_name": "Tela", "tagline": "t" * 61}, "at most 60"),
])
async def test_names_are_checked(api: httpx.AsyncClient, world: dict[str, Any], payload: dict[str, Any],
                                 words: str) -> None:
    resp = await api.put("/api/v1/admin/branding", headers=_h(world["admin"]), json=payload)
    assert resp.status_code == 400
    assert words in resp.text


async def test_only_admins_change_branding(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    viewer = _h(world["viewer"])
    assert (await api.put("/api/v1/admin/branding", headers=viewer, json={"app_name": "Nope"})).status_code == 403
    files = {"file": ("logo.png", png(), "image/png")}
    assert (await api.post("/api/v1/admin/branding/assets/logo_light", headers=viewer, files=files)).status_code == 403
    assert (await api.post("/api/v1/admin/branding/reset", headers=viewer)).status_code == 403
    assert (await api.get("/api/v1/branding")).status_code == 401
    me = (await api.get("/api/v1/me", headers=_h(world["admin"]))).json()
    assert "branding.manage" in me["permissions"]


async def test_logo_upload_serves_the_file_replaces_and_resets(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, world: dict[str, Any]
) -> None:
    admin, store = _h(world["admin"]), world["store"]
    up = await api.post("/api/v1/admin/branding/assets/logo_light", headers=admin,
                        files={"file": ("logo.png", png(240, 80), "image/png")})
    assert up.status_code == 200, up.text
    logo = up.json()["assets"]["logo_light"]
    assert (logo["content_type"], logo["width"], logo["height"]) == ("image/png", 240, 80)
    assert logo["url"].startswith(f"/api/v1/branding/assets/{world['tenant']}/logo_light?v=1")

    served = await api.get(logo["url"])  # no token: <img> can't send one
    assert served.status_code == 200
    assert served.content == png(240, 80)
    assert served.headers["content-type"] == "image/png"
    assert "immutable" in served.headers["cache-control"]
    assert served.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in served.headers["content-security-policy"]
    again = await api.get(logo["url"], headers={"If-None-Match": served.headers["etag"]})
    assert again.status_code == 304
    old_version = await api.get(logo["url"].replace("v=1", "v=0"))
    assert "immutable" not in old_version.headers["cache-control"]

    # Replacing keeps the old file for roll back (US-151); an SVG dark logo and a favicon sit beside it.
    svg = await api.post("/api/v1/admin/branding/assets/logo_light", headers=admin,
                         files={"file": ("logo.svg", SVG, "image/svg+xml")})
    assert svg.status_code == 200, svg.text
    assert svg.json()["assets"]["logo_light"]["content_type"] == "image/svg+xml"
    assert len([k for k in store.files if "/branding/logo_light" in k]) == 2
    fav = await api.post("/api/v1/admin/branding/assets/favicon", headers=admin,
                         files={"file": ("f.png", png(32, 32), "image/png")})
    assert fav.json()["version"] == 3

    removed = await api.delete("/api/v1/admin/branding/assets/favicon", headers=admin)
    assert removed.json()["assets"]["favicon"] is None
    assert [k for k in store.files if "/branding/favicon" in k]  # an older version still shows it

    reset = (await api.post("/api/v1/admin/branding/reset", headers=admin)).json()
    assert reset["is_default"] is True
    assert reset["assets"] == {"logo_light": None, "logo_dark": None, "favicon": None}
    assert len(store.files) == 3  # kept while the history holds versions that use them
    assert (await api.get(logo["url"])).status_code == 404
    changes = [r[0] for r in owner_conn.execute(
        "SELECT details->>'change' FROM ops.audit_events WHERE action = 'branding.updated' ORDER BY id").fetchall()]
    assert changes == ["logo_light_uploaded", "logo_light_uploaded", "favicon_uploaded", "favicon_removed", "reset"]


@pytest.mark.parametrize(("name", "data", "status", "words"), [
    ("evil.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', 400, "<script>"),
    ("logo.png", b"not an image", 400, "PNG, JPEG or SVG"),
    ("tiny.png", png(8, 8), 400, "between 16"),
    ("big.png", png() + b"\0" * (1024 * 1024), 422, "larger than 1024 KB"),
])
async def test_bad_uploads_are_refused(api: httpx.AsyncClient, world: dict[str, Any], name: str, data: bytes,
                                       status: int, words: str) -> None:
    resp = await api.post("/api/v1/admin/branding/assets/logo_dark", headers=_h(world["admin"]),
                          files={"file": (name, data, "application/octet-stream")})
    assert resp.status_code == status, resp.text
    assert words in resp.text
    assert not world["store"].files


async def test_favicon_limit_and_unknown_slot(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    admin = _h(world["admin"])
    big = await api.post("/api/v1/admin/branding/assets/favicon", headers=admin,
                         files={"file": ("f.png", png() + b"\0" * (256 * 1024), "image/png")})
    assert big.status_code == 422
    assert "256 KB" in big.text
    other = await api.post("/api/v1/admin/branding/assets/banner", headers=admin,
                           files={"file": ("f.png", png(), "image/png")})
    assert other.status_code == 400
    assert (await api.get(f"/api/v1/branding/assets/{world['tenant']}/logo_light")).status_code == 404
    assert (await api.get("/api/v1/branding/assets/not-a-tenant/logo_light")).status_code == 404


# --------------------------------------------------------------------------- palettes (US-150)

LOGO = ["#287a9a", "#215174", "#297739", "#e46e22", "#f7bd3c"]


async def test_palette_candidates_pick_save_and_reset(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, world: dict[str, Any]
) -> None:
    admin, viewer = _h(world["admin"]), _h(world["viewer"])
    out = (await api.post("/api/v1/admin/branding/palettes", headers=admin, json={"seeds": LOGO})).json()
    assert 3 <= len(out["candidates"]) <= 5 and out["greys_only"] is False
    pick = out["candidates"][0]
    assert pick["ok"] and pick["tokens"]["light"]["accent"] and pick["checks"]

    tampered = {**pick["spec"], "accent": pick["spec"]["accent"]}
    saved = await api.put("/api/v1/admin/branding/palette", headers=admin,
                          json={"spec": tampered, "expected_version": 0})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["palette"]["tokens"] == pick["tokens"]  # rebuilt on the server from the spec alone
    assert body["is_default"] is False and body["version"] == 1

    seen = (await api.get("/api/v1/branding", headers=viewer)).json()
    assert seen["palette"]["tokens"]["dark"]["accent"] == pick["tokens"]["dark"]["accent"]
    assert "checks" not in seen["palette"]  # only Admins see the checks
    public = (await api.get("/api/v1/branding/public", params={"issuer": ISSUER})).json()
    assert public["palette"]["name"] == pick["name"]

    stale = await api.put("/api/v1/admin/branding/palette", headers=admin, json={"spec": None, "expected_version": 0})
    assert stale.status_code == 409
    removed = (await api.put("/api/v1/admin/branding/palette", headers=admin, json={"spec": None})).json()
    assert removed["palette"] is None and removed["is_default"] is True
    await api.put("/api/v1/admin/branding/palette", headers=admin, json={"spec": pick["spec"]})
    reset = (await api.post("/api/v1/admin/branding/reset", headers=admin)).json()
    assert reset["palette"] is None

    rows = owner_conn.execute(
        "SELECT details FROM ops.audit_events WHERE action = 'branding.updated' ORDER BY id").fetchall()
    assert [r[0]["change"] for r in rows] == ["palette", "palette_removed", "palette", "reset"]
    assert rows[0][0]["after"]["palette"] == {"name": pick["name"], **pick["spec"]}
    assert rows[0][0]["before"]["palette"] is None


async def test_custom_accent_is_adjusted_or_refused(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    admin = _h(world["admin"])
    out = (await api.post("/api/v1/admin/branding/palettes", headers=admin,
                          json={"accent": "#FFCC00", "seeds": LOGO})).json()
    [p] = out["candidates"]
    assert p["spec"]["source"] == "custom" and p["spec"]["accent"] == "#ffcc00"
    assert p["tokens"]["light"]["accent"] != "#ffcc00" and any("adjusted" in n for n in p["notes"])

    for path, body in (("/api/v1/admin/branding/palettes", {"accent": "#d62828"}),
                       ("/api/v1/admin/branding/palette", {"spec": {"accent": "#2a9d48", "source": "custom"}})):
        resp = await api.request("POST" if path.endswith("palettes") else "PUT", path, headers=admin, json=body)
        assert resp.status_code == 400, resp.text
        assert "can't be the accent" in resp.text

    # A red logo colour is moved to orange rather than refused.
    resp = await api.put("/api/v1/admin/branding/palette", headers=admin, json={"spec": {"accent": "#d62828"}})
    assert resp.status_code == 200
    assert resp.json()["palette"]["name"] == "Orange"


@pytest.mark.parametrize("body", [{"seeds": ["red"]}, {"seeds": ["#000000"] * 13}, {"accent": "#12345"},
                                  {"spec": {"accent": "#287a9a", "variant": "loud"}}])
async def test_palette_input_is_checked(api: httpx.AsyncClient, world: dict[str, Any], body: dict[str, Any]) -> None:
    path, method = ("/api/v1/admin/branding/palette", "PUT") if "spec" in body else \
        ("/api/v1/admin/branding/palettes", "POST")
    resp = await api.request(method, path, headers=_h(world["admin"]), json=body)
    assert resp.status_code == 400


async def test_only_admins_get_or_save_palettes(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    viewer = _h(world["viewer"])
    assert (await api.post("/api/v1/admin/branding/palettes", headers=viewer, json={"seeds": LOGO})).status_code == 403
    assert (await api.put("/api/v1/admin/branding/palette", headers=viewer,
                          json={"spec": {"accent": "#287a9a"}})).status_code == 403


# --------------------------------------------------------------------------- logo sizes (US-152)


async def test_logo_sizes_save_check_and_reset(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, world: dict[str, Any]
) -> None:
    admin, viewer = _h(world["admin"]), _h(world["viewer"])
    first = (await api.get("/api/v1/branding", headers=viewer)).json()
    assert first["logo_sizes"] == {"sidebar": 28, "sign_in": 48, "export": 28}
    assert first["defaults"]["logo_size_limits"]["sign_in"] == [32, 128]

    saved = await api.put("/api/v1/admin/branding/logo-size", headers=admin,
                          json={"sidebar": 44, "sign_in": 96, "export": 40, "expected_version": 0})
    assert saved.status_code == 200, saved.text
    assert saved.json()["logo_sizes"] == {"sidebar": 44, "sign_in": 96, "export": 40}
    assert saved.json()["is_default"] is False
    assert (await api.get("/api/v1/branding", headers=viewer)).json()["logo_sizes"]["sidebar"] == 44
    public = (await api.get("/api/v1/branding/public", params={"issuer": ISSUER})).json()
    assert public["logo_sizes"]["sign_in"] == 96

    for bad in ({"sidebar": 19, "sign_in": 48, "export": 28}, {"sidebar": 28, "sign_in": 129, "export": 28},
                {"sidebar": 28, "sign_in": 48}):
        assert (await api.put("/api/v1/admin/branding/logo-size", headers=admin, json=bad)).status_code == 400
    stale = await api.put("/api/v1/admin/branding/logo-size", headers=admin,
                          json={"sidebar": 30, "sign_in": 48, "export": 28, "expected_version": 0})
    assert stale.status_code == 409
    same = (await api.put("/api/v1/admin/branding/logo-size", headers=admin,
                          json={"sidebar": 44, "sign_in": 96, "export": 40})).json()
    assert same["version"] == 1  # nothing changed, nothing saved
    assert (await api.put("/api/v1/admin/branding/logo-size", headers=viewer,
                          json={"sidebar": 30, "sign_in": 48, "export": 28})).status_code == 403

    defaults = (await api.put("/api/v1/admin/branding/logo-size", headers=admin,
                              json={"sidebar": 28, "sign_in": 48, "export": 28})).json()
    assert defaults["is_default"] is True
    await api.put("/api/v1/admin/branding/logo-size", headers=admin, json={"sidebar": 50, "sign_in": 48, "export": 28})
    reset = (await api.post("/api/v1/admin/branding/reset", headers=admin)).json()
    assert reset["logo_sizes"]["sidebar"] == 28
    row = owner_conn.execute("SELECT details FROM ops.audit_events WHERE action = 'branding.updated' "
                             "AND details->>'change' = 'logo_size' ORDER BY id LIMIT 1").fetchone()
    assert row is not None
    assert row[0]["before"]["logo_sizes"]["sidebar"] == 28 and row[0]["after"]["logo_sizes"]["sidebar"] == 44


# --------------------------------------------------------------------------- chart library (ENH-018, US-206)


async def test_chart_library_save_check_history_and_export(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, world: dict[str, Any]
) -> None:
    from types import SimpleNamespace

    from tela_api.platform.branding import export_branding

    admin, viewer = _h(world["admin"]), _h(world["viewer"])
    first = (await api.get("/api/v1/branding", headers=viewer)).json()
    assert first["chart_library"] == "echarts" and first["defaults"]["chart_library"] == "echarts"

    saved = await api.put("/api/v1/admin/branding/chart-library", headers=admin,
                          json={"library": "recharts", "expected_version": 0})
    assert saved.status_code == 200, saved.text
    assert (saved.json()["chart_library"], saved.json()["is_default"], saved.json()["version"]) == ("recharts", False, 1)
    assert (await api.get("/api/v1/branding", headers=viewer)).json()["chart_library"] == "recharts"
    assert (await api.get("/api/v1/branding/public", params={"issuer": ISSUER})).json()["chart_library"] == "recharts"

    assert (await api.put("/api/v1/admin/branding/chart-library", headers=admin,
                          json={"library": "highcharts"})).status_code == 400
    assert (await api.put("/api/v1/admin/branding/chart-library", headers=admin,
                          json={"library": "plot", "expected_version": 0})).status_code == 409
    assert (await api.put("/api/v1/admin/branding/chart-library", headers=viewer,
                          json={"library": "plot"})).status_code == 403
    same = (await api.put("/api/v1/admin/branding/chart-library", headers=admin, json={"library": "recharts"})).json()
    assert same["version"] == 1  # nothing changed, nothing saved

    await api.put("/api/v1/admin/branding/chart-library", headers=admin, json={"library": "plot"})
    request: Any = SimpleNamespace(app=api.app)  # type: ignore[attr-defined]
    assert (await export_branding(request, world["tenant"]))["chart_library"] == "plot"
    hist = (await api.get("/api/v1/admin/branding/history", headers=admin)).json()
    assert [(h["change"], h["chart_library"]) for h in hist["items"]] == \
        [("chart_library", "plot"), ("chart_library", "recharts")]
    back = (await api.post("/api/v1/admin/branding/rollback", headers=admin, json={"version": 1})).json()
    assert (back["version"], back["chart_library"]) == (3, "recharts")
    reset = (await api.post("/api/v1/admin/branding/reset", headers=admin)).json()
    assert (reset["chart_library"], reset["is_default"]) == ("echarts", True)
    row = owner_conn.execute("SELECT details FROM ops.audit_events WHERE action = 'branding.updated' "
                             "AND details->>'change' = 'chart_library' ORDER BY id LIMIT 1").fetchone()
    assert row is not None
    assert row[0]["before"]["chart_library"] == "echarts" and row[0]["after"]["chart_library"] == "recharts"


# --------------------------------------------------------------------------- history and roll back (US-151)


async def test_history_and_roll_back_restore_names_logo_and_palette(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, world: dict[str, Any]
) -> None:
    admin, viewer = _h(world["admin"]), _h(world["viewer"])
    await api.put("/api/v1/admin/branding", headers=admin, json={"app_name": "Insights Intelligence"})
    logo = (await api.post("/api/v1/admin/branding/assets/logo_light", headers=admin,
                           files={"file": ("logo.png", png(240, 80), "image/png")})).json()["assets"]["logo_light"]
    pick = (await api.post("/api/v1/admin/branding/palettes", headers=admin, json={"seeds": LOGO})).json()
    await api.put("/api/v1/admin/branding/palette", headers=admin, json={"spec": pick["candidates"][0]["spec"]})
    await api.put("/api/v1/admin/branding/logo-size", headers=admin, json={"sidebar": 40, "sign_in": 64, "export": 32})
    await api.post("/api/v1/admin/branding/reset", headers=admin)

    hist = (await api.get("/api/v1/admin/branding/history", headers=admin)).json()
    assert [h["change"] for h in hist["items"]] == ["reset", "logo_size", "palette", "logo_light_uploaded", "names"]
    assert hist["version"] == 5 and hist["items"][0]["current"] is True
    v4 = hist["items"][1]
    assert (v4["version"], v4["app_name"], v4["assets"]["logo_light"], v4["logo_sizes"]["sidebar"]) == \
        (4, "Insights Intelligence", True, 40)
    assert v4["palette"]["name"] == pick["candidates"][0]["name"]
    assert v4["changed_by"]["name"] == "Ada Admin"

    back = await api.post("/api/v1/admin/branding/rollback", headers=admin, json={"version": 4, "expected_version": 5})
    assert back.status_code == 200, back.text
    body = back.json()
    assert (body["version"], body["app_name"], body["logo_sizes"]["sidebar"]) == (6, "Insights Intelligence", 40)
    assert body["palette"]["tokens"] == pick["candidates"][0]["tokens"]
    served = await api.get(body["assets"]["logo_light"]["url"])  # the file was kept for this
    assert served.status_code == 200 and served.content == png(240, 80)
    assert body["assets"]["logo_light"]["url"] != logo["url"]  # new version, new cache key
    assert (await api.get("/api/v1/branding", headers=viewer)).json()["app_name"] == "Insights Intelligence"

    assert (await api.post("/api/v1/admin/branding/rollback", headers=admin,
                           json={"version": 1, "expected_version": 5})).status_code == 409
    assert (await api.post("/api/v1/admin/branding/rollback", headers=admin,
                           json={"version": 99})).status_code == 404
    assert (await api.get("/api/v1/admin/branding/history", headers=viewer)).status_code == 403
    assert (await api.post("/api/v1/admin/branding/rollback", headers=viewer, json={"version": 1})).status_code == 403
    row = owner_conn.execute("SELECT details FROM ops.audit_events WHERE details->>'change' = 'rollback'").fetchone()
    assert row is not None
    assert row[0]["rolled_back_to"] == 4 and row[0]["before"]["app_name"] == "Tela"


async def test_old_versions_are_pruned_with_files_no_version_uses(
    api: httpx.AsyncClient, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tela_api.platform import branding

    monkeypatch.setattr(branding, "HISTORY_KEEP", 2)
    admin, store = _h(world["admin"]), world["store"]
    for w in (100, 120, 140):
        await api.post("/api/v1/admin/branding/assets/logo_light", headers=admin,
                       files={"file": ("logo.png", png(w, 40), "image/png")})
    assert len(store.files) == 2  # the first logo's only version was pruned, and its file with it
    hist = (await api.get("/api/v1/admin/branding/history", headers=admin)).json()
    assert [h["version"] for h in hist["items"]] == [3, 2]
    await api.put("/api/v1/admin/branding", headers=admin, json={"app_name": "Renamed"})
    await api.put("/api/v1/admin/branding", headers=admin, json={"app_name": "Renamed again"})
    assert len(store.files) == 1  # the current logo stays


async def test_export_branding_carries_name_logo_and_palette(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    from types import SimpleNamespace

    from tela_api.platform.branding import export_branding

    admin = _h(world["admin"])
    request: Any = SimpleNamespace(app=api.app)  # type: ignore[attr-defined]
    plain = await export_branding(request, world["tenant"])
    assert plain == {"app_name": "Tela", "logo": None, "logo_height": 28, "palette": None, "chart_library": "echarts"}
    await api.put("/api/v1/admin/branding", headers=admin, json={"app_name": "Insights Intelligence"})
    await api.post("/api/v1/admin/branding/assets/logo_light", headers=admin,
                   files={"file": ("logo.svg", SVG, "image/svg+xml")})
    pick = (await api.post("/api/v1/admin/branding/palettes", headers=admin, json={"seeds": LOGO})).json()
    await api.put("/api/v1/admin/branding/palette", headers=admin, json={"spec": pick["candidates"][0]["spec"]})
    await api.put("/api/v1/admin/branding/logo-size", headers=admin, json={"sidebar": 28, "sign_in": 48, "export": 36})
    out = await export_branding(request, world["tenant"])
    assert out["app_name"] == "Insights Intelligence" and out["logo_height"] == 36
    assert out["logo"].startswith("data:image/svg+xml;base64,")
    assert out["palette"]["accent"] == pick["candidates"][0]["tokens"]["light"]["accent"]


# --------------------------------------------------------------------------- navigation colours (ENH-019, US-209)


async def test_nav_colors_save_check_history_and_reset(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, world: dict[str, Any]
) -> None:
    admin, viewer = _h(world["admin"]), _h(world["viewer"])
    first = (await api.get("/api/v1/branding", headers=viewer)).json()
    assert first["nav_colors"] == first["defaults"]["nav_colors"] == {"sidebar": "standard", "topbar": "standard"}

    saved = await api.put("/api/v1/admin/branding/nav-colors", headers=admin,
                          json={"sidebar": "brand", "topbar": "tinted", "expected_version": 0})
    assert saved.status_code == 200, saved.text
    assert (saved.json()["nav_colors"], saved.json()["is_default"], saved.json()["version"]) == \
        ({"sidebar": "brand", "topbar": "tinted"}, False, 1)
    assert (await api.get("/api/v1/branding", headers=viewer)).json()["nav_colors"]["sidebar"] == "brand"
    assert (await api.get("/api/v1/branding/public", params={"issuer": ISSUER})).json()["nav_colors"]["topbar"] == "tinted"

    assert (await api.put("/api/v1/admin/branding/nav-colors", headers=admin,
                          json={"sidebar": "neon", "topbar": "standard"})).status_code == 400
    assert (await api.put("/api/v1/admin/branding/nav-colors", headers=admin,
                          json={"sidebar": "dark", "topbar": "dark", "expected_version": 0})).status_code == 409
    assert (await api.put("/api/v1/admin/branding/nav-colors", headers=viewer,
                          json={"sidebar": "dark", "topbar": "dark"})).status_code == 403
    same = (await api.put("/api/v1/admin/branding/nav-colors", headers=admin,
                          json={"sidebar": "brand", "topbar": "tinted"})).json()
    assert same["version"] == 1  # nothing changed, nothing saved

    await api.put("/api/v1/admin/branding/nav-colors", headers=admin, json={"sidebar": "dark", "topbar": "standard"})
    hist = (await api.get("/api/v1/admin/branding/history", headers=admin)).json()
    assert [(h["change"], h["nav_colors"]["sidebar"]) for h in hist["items"]] == \
        [("nav_colors", "dark"), ("nav_colors", "brand")]
    back = (await api.post("/api/v1/admin/branding/rollback", headers=admin, json={"version": 1})).json()
    assert (back["version"], back["nav_colors"]) == (3, {"sidebar": "brand", "topbar": "tinted"})
    row = owner_conn.execute("SELECT details FROM ops.audit_events WHERE action = 'branding.updated' "
                             "AND details->>'change' = 'nav_colors' ORDER BY id LIMIT 1").fetchone()
    assert row[0]["before"]["nav_colors"]["sidebar"] == "standard" and row[0]["after"]["nav_colors"]["sidebar"] == "brand"
    reset = (await api.post("/api/v1/admin/branding/reset", headers=admin)).json()
    assert reset["nav_colors"] == {"sidebar": "standard", "topbar": "standard"} and reset["is_default"]


async def test_layout_default_and_personal_choice(
    api: httpx.AsyncClient, owner_conn: psycopg.Connection, world: dict[str, Any]
) -> None:
    """ENH-021: the company's default layout, kept in the history; each person may pick their own."""
    admin, viewer = _h(world["admin"]), _h(world["viewer"])
    first = (await api.get("/api/v1/branding", headers=viewer)).json()
    assert first["layout"] == first["defaults"]["layout"] == "classic"
    me = (await api.get("/api/v1/me", headers=viewer)).json()
    assert me["preferences"]["layout"] == "default"

    saved = await api.put("/api/v1/admin/branding/layout", headers=admin, json={"layout": "executive", "expected_version": 0})
    assert saved.status_code == 200, saved.text
    assert (saved.json()["layout"], saved.json()["is_default"], saved.json()["version"]) == ("executive", False, 1)
    assert (await api.get("/api/v1/branding", headers=viewer)).json()["layout"] == "executive"
    assert (await api.get("/api/v1/branding/public", params={"issuer": ISSUER})).json()["layout"] == "executive"
    assert (await api.put("/api/v1/admin/branding/layout", headers=admin, json={"layout": "bento"})).status_code == 400
    assert (await api.put("/api/v1/admin/branding/layout", headers=admin,
                          json={"layout": "command", "expected_version": 0})).status_code == 409
    assert (await api.put("/api/v1/admin/branding/layout", headers=viewer, json={"layout": "command"})).status_code == 403
    assert (await api.put("/api/v1/admin/branding/layout", headers=admin, json={"layout": "executive"})).json()["version"] == 1

    await api.put("/api/v1/admin/branding/layout", headers=admin, json={"layout": "command"})
    hist = (await api.get("/api/v1/admin/branding/history", headers=admin)).json()
    assert [(h["change"], h["layout"]) for h in hist["items"]] == [("layout", "command"), ("layout", "executive")]
    back = (await api.post("/api/v1/admin/branding/rollback", headers=admin, json={"version": 1})).json()
    assert (back["version"], back["layout"]) == (3, "executive")
    row = owner_conn.execute("SELECT details FROM ops.audit_events WHERE action = 'branding.updated' "
                             "AND details->>'change' = 'layout' ORDER BY id LIMIT 1").fetchone()
    assert (row[0]["before"]["layout"], row[0]["after"]["layout"]) == ("classic", "executive")

    # A person's own pick needs no permission, keeps their theme, and "default" follows the company again.
    mine = await api.put("/api/v1/me/preferences", headers=viewer, json={"layout": "command"})
    assert mine.status_code == 200 and mine.json()["layout"] == "command"
    assert (await api.put("/api/v1/me/preferences", headers=viewer, json={"layout": "tiles"})).status_code == 400
    assert (await api.get("/api/v1/me", headers=viewer)).json()["preferences"]["layout"] == "command"
    assert (await api.put("/api/v1/me/preferences", headers=viewer, json={"layout": "default"})).json()["layout"] == "default"
    reset = (await api.post("/api/v1/admin/branding/reset", headers=admin)).json()
    assert reset["layout"] == "classic" and reset["is_default"]


async def test_aurora_layout(api: httpx.AsyncClient, owner_conn: psycopg.Connection, world: dict[str, Any]) -> None:
    """ENH-027 (US-248): Aurora is a fourth layout, as a company default and as a person's own pick."""
    admin, viewer = _h(world["admin"]), _h(world["viewer"])
    saved = await api.put("/api/v1/admin/branding/layout", headers=admin, json={"layout": "aurora"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["layout"] == "aurora"
    assert (await api.get("/api/v1/branding", headers=viewer)).json()["layout"] == "aurora"
    stored = owner_conn.execute("SELECT layout FROM ops.tenant_branding").fetchall()
    assert ("aurora",) in [tuple(r) for r in stored]
    mine = await api.put("/api/v1/me/preferences", headers=viewer, json={"layout": "aurora"})
    assert mine.status_code == 200 and mine.json()["layout"] == "aurora"
    assert (await api.put("/api/v1/me/preferences", headers=viewer, json={"layout": "default"})).json()["layout"] == "default"
    assert (await api.post("/api/v1/admin/branding/reset", headers=admin)).json()["layout"] == "classic"
