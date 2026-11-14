"""Operator CLI (``tela-admin``).

Runs with the schema-owner connection (TELA_MIGRATE_DATABASE_URL[_FILE]),
because the application role is deliberately unable to create tenants.

  tela-admin bootstrap-tenant --slug acme --name "Acme PMO" \\
      --issuer https://tela.example.com/realms/acme \\
      --admin-group <entra-group-object-id> --admin-group-name "TELA-Admins" \\
      [--group creator=<id>:TELA-Creators] [--group viewer=<id>:TELA-Viewers] \\
      [--profile customer_hosted]

  tela-admin reindex-knowledge [--tenant acme]   # backfill the knowledge index (US-100)
  tela-admin outbox-requeue [--topic t] [--dry-run]   # retry dead-lettered events (US-009)
  tela-admin gold-run --tenant acme [--tag paraphrase] [--plan-only]   # queue a gold-set evaluation (US-190)
  tela-admin gold-report --tenant acme [--run <id>] [--grid]   # a run's scores per set, path and answer shape
  tela-admin gold-run --tenant acme --plan-only --review off|on   # the LLM review gate's two runs (US-246)
  tela-admin gold-run --tenant acme --mode certified|fields|hybrid|direct_sql   # one Ask mode's gold score (US-259)
  tela-admin gold-gate --tenant acme --off <run> --on <run>   # keep review on by default? (DEC-28 point 3)

The first Admin mapping is created pre-approved (break-glass bootstrap) and
audited; every later Admin mapping needs a second Admin's approval.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg

from tela_common.ids import new_id


def _owner_dsn() -> str:
    path = os.environ.get("TELA_MIGRATE_DATABASE_URL_FILE")
    dsn = Path(path).read_text().strip() if path else os.environ.get("TELA_MIGRATE_DATABASE_URL", "")
    if not dsn:
        sys.exit("Set TELA_MIGRATE_DATABASE_URL or TELA_MIGRATE_DATABASE_URL_FILE")
    return dsn.replace("postgresql+psycopg://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")


def _parse_group(spec: str) -> tuple[str, str, str]:
    try:
        role, rest = spec.split("=", 1)
        ext, name = rest.split(":", 1)
    except ValueError:
        sys.exit(f"--group must look like role=<group-id>:<display name>, got {spec!r}")
    return role.strip(), ext.strip(), name.strip()


def bootstrap_tenant(args: argparse.Namespace) -> None:
    groups = [("admin", args.admin_group, args.admin_group_name), *map(_parse_group, args.group or [])]
    with psycopg.connect(_owner_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug = %s", (args.slug,))
        row = cur.fetchone()
        if row:
            tenant_id = str(row[0])
            cur.execute("UPDATE iam.tenants SET oidc_issuer = %s, name = %s WHERE id = %s",
                        (args.issuer, args.name, tenant_id))
        else:
            tenant_id = new_id()
            cur.execute(
                "INSERT INTO iam.tenants (id, slug, name, deployment_profile, oidc_issuer, settings) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (tenant_id, args.slug, args.name, args.profile, args.issuer,
                 json.dumps({"time_zone": args.time_zone, "locale": "en-GB"})),
            )
        for role_key, external_id, display in groups:
            cur.execute("SELECT id FROM iam.roles WHERE tenant_id IS NULL AND key = %s", (role_key,))
            role = cur.fetchone()
            if role is None:
                sys.exit(f"unknown role {role_key!r}")
            cur.execute(
                "INSERT INTO iam.idp_groups (id, tenant_id, external_group_id, display_name, source) "
                "VALUES (%s, %s, %s, %s, 'admin') ON CONFLICT (tenant_id, external_group_id) "
                "DO UPDATE SET display_name = EXCLUDED.display_name RETURNING id",
                (new_id(), tenant_id, external_id, display),
            )
            group_id = cur.fetchone()[0]  # type: ignore[index]
            cur.execute(
                "INSERT INTO iam.group_role_mappings (id, tenant_id, group_id, role_id, approved_at) "
                "VALUES (%s, %s, %s, %s, now()) ON CONFLICT (group_id, role_id) DO NOTHING",
                (new_id(), tenant_id, group_id, role[0]),
            )
        cur.execute(
            "INSERT INTO ops.audit_events (tenant_id, action, object_type, object_id, outcome, severity, "
            "correlation_id, details) VALUES (%s, 'tenant.bootstrapped', 'tenant', %s, 'success', 'high', "
            "'cli', %s)",
            (tenant_id, tenant_id, json.dumps({"groups": [[r, e] for r, e, _ in groups]})),
        )
        conn.commit()
    print(json.dumps({"tenant_id": tenant_id, "slug": args.slug, "issuer": args.issuer}))


def ensure_partitions(_: argparse.Namespace) -> None:
    with psycopg.connect(_owner_dsn()) as conn, conn.cursor() as cur:
        created = 0
        for table in ("ops.audit_events", "ops.query_executions"):
            cur.execute("SELECT util.ensure_month_partitions(%s::regclass, 2)", (table,))
            created += cur.fetchone()[0]  # type: ignore[index]
        conn.commit()
    print(json.dumps({"partitions_created": created}))


def load_packs(args: argparse.Namespace) -> None:
    """Load starter pack manifests into the global catalogue (US-037) with their knowledge files as pack documents
    (US-230). Idempotent; released versions are frozen: a manifest or a document whose content changed under the
    same version is refused (bump pack_version). Documents missing from a loaded version are added."""
    from tela_api.packs.manifest import discover, load_manifest
    from tela_api.packs.source import FilePackSource, document_sha

    results = []
    with psycopg.connect(_owner_dsn()) as conn, conn.cursor() as cur:
        for path in discover(Path(args.dir) if args.dir else None):
            manifest, raw, sha = load_manifest(path)
            docs = FilePackSource(path.parent).documents()
            cur.execute("SELECT id, manifest_sha256 FROM model.starter_packs WHERE tenant_id IS NULL AND "
                        "pack_key = %s AND pack_version = %s", (manifest.pack_key, manifest.pack_version))
            row = cur.fetchone()
            if row and row[1] != sha:
                sys.exit(f"{manifest.pack_key} {manifest.pack_version} is already released with different content; "
                         "bump pack_version")
            pack_id = row[0] if row else new_id()
            if row is None:
                cur.execute(
                    "INSERT INTO model.starter_packs (id, pack_key, pack_version, title, detection_signature, "
                    "manifest, manifest_sha256, released_at, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, "
                    "'released')",
                    (pack_id, manifest.pack_key, manifest.pack_version, manifest.title,
                     json.dumps(manifest.signature), json.dumps(raw, default=str), sha, manifest.released_at),
                )
            cur.execute("SELECT kind, sha256 FROM model.pack_documents WHERE pack_id = %s", (pack_id,))
            stored: dict[str, str] = dict(cur.fetchall())
            changed = sorted(k for k, body in docs.items() if k in stored and stored[k] != document_sha(body))
            if changed:
                sys.exit(f"{manifest.pack_key} {manifest.pack_version} is already released with different "
                         f"{', '.join(changed)}; bump pack_version")
            added = [k for k in docs if k not in stored]
            for kind in added:
                cur.execute("INSERT INTO model.pack_documents (id, pack_id, tenant_id, kind, body, sha256) VALUES "
                            "(%s, %s, NULL, %s, %s, %s)",
                            (new_id(), pack_id, kind, json.dumps(docs[kind], default=str), document_sha(docs[kind])))
            status = "loaded" if row is None else "documents_added" if added else "unchanged"
            results.append({"pack": manifest.pack_key, "version": manifest.pack_version, "status": status,
                            **({"documents": sorted(added)} if added else {})})
        conn.commit()
    print(json.dumps({"packs": results}))


def reindex_knowledge(args: argparse.Namespace) -> None:
    """Queue knowledge indexing for every current approved model version (US-100) — the backfill after an upgrade,
    or a rebuild after changing the embedding model. The worker picks the events up through the outbox."""
    with psycopg.connect(_owner_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT m.tenant_id, m.current_version_id, m.connection_id FROM model.data_models m JOIN "
                    "conn.connections c ON c.id = m.connection_id WHERE m.current_version_id IS NOT NULL AND "
                    "c.deleted_at IS NULL AND (%s::text IS NULL OR m.tenant_id IN (SELECT id FROM iam.tenants "
                    "WHERE slug = %s))", (args.tenant, args.tenant))
        rows = cur.fetchall()
        for tenant_id, version_id, connection_id in rows:
            cur.execute("INSERT INTO ops.outbox_events (tenant_id, topic, aggregate_id, payload) VALUES "
                        "(%s, 'model.version_approved', %s, %s)",
                        (tenant_id, version_id, json.dumps({"connection_id": str(connection_id), "reindex": True})))
        conn.commit()
    print(json.dumps({"queued": len(rows)}))


def outbox_requeue(args: argparse.Namespace) -> None:
    """Give dead-lettered outbox events a fresh set of attempts (US-009), for example after a worker release adds
    the missing handler. Handlers are idempotent, so a replay is safe; --dry-run only counts."""
    where = ("dead_at IS NOT NULL AND published_at IS NULL AND (%(topics)s::text[] IS NULL OR topic = ANY(%(topics)s)) "
             "AND (%(tenant)s::text IS NULL OR tenant_id IN (SELECT id FROM iam.tenants WHERE slug = %(tenant)s))")
    params = {"topics": args.topic or None, "tenant": args.tenant}
    if args.dry_run:
        sql = f"SELECT topic, count(*) FROM ops.outbox_events WHERE {where} GROUP BY topic"  # noqa: S608 - fixed text
    else:
        sql = (f"WITH r AS (UPDATE ops.outbox_events SET dead_at = NULL, attempts = 0, last_error = NULL "  # noqa: S608
               f"WHERE {where} RETURNING topic) SELECT topic, count(*) FROM r GROUP BY topic")
    with psycopg.connect(_owner_dsn()) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        counts = dict(sorted(cur.fetchall()))
        conn.commit()
    print(json.dumps({"dry_run": args.dry_run, "requeued": counts}))


def _tenant_id(cur: psycopg.Cursor[Any], slug: str) -> str:
    cur.execute("SELECT id FROM iam.tenants WHERE slug = %s", (slug,))
    row = cur.fetchone()
    if row is None:
        sys.exit(f"No tenant with slug {slug!r}")
    return str(row[0])


def gold_run(args: argparse.Namespace) -> None:
    """Queue a gold-set evaluation (US-190), as Admin → Knowledge → Gold's Evaluate does: the worker hands it to the
    API, which plans every approved example (with --tag, only those tagged) as the person it runs as. --plan-only
    fetches no result rows, for a live source; examples then pass on spec match alone. --review on|off plans with LLM
    review forced either way, whatever the tenant's switch says (US-246). --mode answers every example in one Ask mode,
    whatever each source is set to, and the run becomes that mode's score on the Connections > Ask screen (US-259)."""
    from tela_api.knowledge.gold import run_mode_problem

    with psycopg.connect(_owner_dsn()) as conn, conn.cursor() as cur:
        tenant_id = _tenant_id(cur, args.tenant)
        if args.run_as:
            cur.execute("SELECT id FROM iam.users WHERE tenant_id = %s AND (id::text = %s OR lower(email::text) = "
                        "lower(%s)) AND status = 'active'", (tenant_id, args.run_as, args.run_as))
        else:  # the Admin who last started an evaluation, as the nightly run does
            cur.execute("SELECT run_as FROM rag.eval_runs WHERE tenant_id = %s AND trigger = 'manual' ORDER BY "
                        "created_at DESC LIMIT 1", (tenant_id,))
        row = cur.fetchone()
        if row is None:
            sys.exit("Name the person the run plans as: --run-as <email or user id> (an active user with "
                     "knowledge.manage)")
        run_as = str(row[0])
        cur.execute("SELECT count(*) FROM rag.gold_examples WHERE tenant_id = %s AND status = 'approved' AND "
                    "(%s::uuid IS NULL OR connection_id = %s::uuid) AND (cardinality(%s::text[]) = 0 OR "
                    "tags && %s::text[])", (tenant_id, args.connection, args.connection, args.tag or [],
                                            args.tag or []))
        count = int(cur.fetchone()[0])  # type: ignore[index]
        if count == 0:
            sys.exit("There are no approved gold examples to evaluate (approve the pack's paraphrases first).")
        cur.execute("SELECT eval_threshold FROM rag.policies WHERE tenant_id = %s", (tenant_id,))
        th = cur.fetchone()
        run_id = new_id()
        review = None if args.review is None else args.review == "on"
        if args.mode and (why := run_mode_problem(args.mode, args.plan_only)):
            sys.exit(why)
        cur.execute("INSERT INTO rag.eval_runs (id, tenant_id, connection_id, trigger, tags, threshold, run_as, "
                    "plan_only, review, ask_mode) VALUES (%s, %s, %s, 'manual', %s, %s, %s, %s, %s, %s)",
                    (run_id, tenant_id, args.connection, args.tag or [], th[0] if th else 0.80, run_as,
                     args.plan_only, review, args.mode))
        cur.execute("INSERT INTO ops.outbox_events (tenant_id, topic, aggregate_id, payload) VALUES "
                    "(%s, 'knowledge.evaluation_requested', %s, %s)", (tenant_id, run_id,
                                                                        json.dumps({"run_id": run_id})))
        conn.commit()
    print(json.dumps({"run_id": run_id, "examples": count, "plan_only": args.plan_only, "review": review,
                      "ask_mode": args.mode}))


def gold_report(args: argparse.Namespace) -> None:
    """A run's scores (default: the tenant's latest): totals, the original set beside the paraphrases, per path, and
    right / confident-wrong / clarified / refused per answer shape and shape by tag (US-250; ``--grid`` prints that
    grid as a table instead of the JSON)."""
    from tela_api.knowledge import scoring
    from tela_api.knowledge.gold import result_shape

    with psycopg.connect(_owner_dsn()) as conn, conn.cursor() as cur:
        tenant_id = _tenant_id(cur, args.tenant)
        cur.execute("SELECT id, status, ask_mode, plan_only, review, tags, total, passed, pass_rate, spec_match_rate, "
                    "by_error, by_set, by_path, error, created_at, finished_at FROM rag.eval_runs WHERE tenant_id = %s "
                    "AND (%s::uuid IS NULL OR id = %s::uuid) ORDER BY created_at DESC LIMIT 1",
                    (tenant_id, args.run, args.run))
        row = cur.fetchone()
        if row is None:
            sys.exit("No evaluation run found.")
        names = [d.name for d in cur.description or []]
        cur.execute("SELECT e.tags, e.passed, e.status, e.confident_wrong, e.diff, g.expected_spec FROM "
                    "rag.eval_results e LEFT JOIN rag.gold_examples g ON g.id = e.gold_example_id WHERE e.run_id = %s",
                    (row[0],))
        cols = [d.name for d in cur.description or []]
        results = [result_shape(dict(zip(cols, r, strict=True))) for r in cur.fetchall()]
    grid = scoring.shape_grid(results)
    if args.grid:
        print(scoring.grid_text(grid))
        return
    print(json.dumps({**dict(zip(names, row, strict=True)), "shape_grid": grid}, default=str, indent=2))


def gold_gate(args: argparse.Namespace) -> None:
    """Compare a review-off and a review-on run of the same examples (US-246): right, confident-wrong,
    over-clarification, refused, planning time and model calls, and whether the default may stay on (DEC-28)."""
    from tela_api.knowledge import scoring

    runs: dict[str, dict[str, Any]] = {}
    with psycopg.connect(_owner_dsn()) as conn, conn.cursor() as cur:
        tenant_id = _tenant_id(cur, args.tenant)
        for side, run_id in (("off", args.off), ("on", args.on)):
            cur.execute("SELECT id, status, review, tags, total, by_set FROM rag.eval_runs WHERE tenant_id = %s AND "
                        "id = %s::uuid", (tenant_id, run_id))
            row = cur.fetchone()
            if row is None:
                sys.exit(f"No evaluation run {run_id}.")
            names = [d.name for d in cur.description or []]
            runs[side] = dict(zip(names, row, strict=True))
            if runs[side]["status"] != "done":
                sys.exit(f"Run {run_id} is {runs[side]['status']}; wait for it to finish.")
            if runs[side]["review"] is not (side == "on"):
                sys.exit(f"Run {run_id} was not queued with --review {side}.")
    out = scoring.gate(runs["off"], runs["on"])
    print(json.dumps({"off_run": args.off, "on_run": args.on, **out}, default=str, indent=2))


def storage_lifecycle(args: argparse.Namespace) -> None:
    """Show, or with --apply set, the bucket lifecycle rules (US-008; db_scheme §11).

    Run with an operator identity that may change bucket configuration (the API's own identity should not):
    TELA_OBJECT_STORE_ENDPOINT, _BUCKET, _ACCESS_KEY and _SECRET_KEY(_FILE). The rules are read back after
    applying; a store that keeps something else gets them removed again and the command exits non-zero.
    """
    import asyncio

    from tela_common.objectstore import LifecycleNotSupported, ObjectStore, lifecycle_rules

    secret = os.environ.get("TELA_OBJECT_STORE_SECRET_KEY")
    if not secret and os.environ.get("TELA_OBJECT_STORE_SECRET_KEY_FILE"):
        secret = Path(os.environ["TELA_OBJECT_STORE_SECRET_KEY_FILE"]).read_text().strip()
    store = ObjectStore(os.environ.get("TELA_OBJECT_STORE_ENDPOINT") or None,
                        os.environ.get("TELA_OBJECT_STORE_BUCKET", "tela"),
                        os.environ.get("TELA_OBJECT_STORE_ACCESS_KEY"), secret)
    config = lifecycle_rules(export_days=args.export_days)
    if not args.apply:
        current = asyncio.run(store.lifecycle())
        print(json.dumps({"bucket": store.bucket, "wanted": config["Rules"], "current": current}, indent=2))
        return
    try:
        asyncio.run(store.apply_lifecycle(config))
    except LifecycleNotSupported as exc:
        raise SystemExit(f"storage-lifecycle: {exc}") from exc
    print(json.dumps({"bucket": store.bucket, "applied": [r["ID"] for r in config["Rules"]]}))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tela-admin")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("bootstrap-tenant", help="create or update a tenant and its first group mappings")
    b.add_argument("--slug", required=True)
    b.add_argument("--name", required=True)
    b.add_argument("--issuer", required=True, help="trusted token issuer (Keycloak realm URL)")
    b.add_argument("--admin-group", required=True, help="Entra group object id (or dev group name)")
    b.add_argument("--admin-group-name", default="TELA-Admins")
    b.add_argument("--group", action="append", help="role=<group-id>:<display name>; repeatable")
    b.add_argument("--profile", default="customer_hosted", choices=["saas", "customer_hosted"])
    b.add_argument("--time-zone", default="Asia/Dubai")
    b.set_defaults(func=bootstrap_tenant)

    p = sub.add_parser("ensure-partitions", help="create monthly partitions ahead of time")
    p.set_defaults(func=ensure_partitions)

    lp = sub.add_parser("load-packs", help="load starter pack manifests (packs/*/manifest.yaml)")
    lp.add_argument("--dir", default=os.environ.get("TELA_PACKS_DIR"))
    lp.set_defaults(func=load_packs)

    rk = sub.add_parser("reindex-knowledge", help="queue knowledge indexing for every current approved model")
    rk.add_argument("--tenant", help="tenant slug (default: all tenants)")
    rk.set_defaults(func=reindex_knowledge)

    oq = sub.add_parser("outbox-requeue", help="retry dead-lettered outbox events")
    oq.add_argument("--topic", action="append", help="only this topic; repeatable (default: every topic)")
    oq.add_argument("--tenant", help="tenant slug (default: all tenants)")
    oq.add_argument("--dry-run", action="store_true", help="count the events without changing them")
    oq.set_defaults(func=outbox_requeue)

    gr = sub.add_parser("gold-run", help="queue a gold-set evaluation (US-190)")
    gr.add_argument("--tenant", required=True, help="tenant slug")
    gr.add_argument("--connection", help="only this connection's examples (id)")
    gr.add_argument("--tag", action="append", help="only examples with this tag; repeatable (e.g. paraphrase)")
    gr.add_argument("--plan-only", action="store_true", help="plan and compare specs; fetch no result rows")
    gr.add_argument("--run-as", help="email or user id the run plans as (default: who last ran one by hand)")
    gr.add_argument("--review", choices=["on", "off"],
                    help="plan with LLM review forced on or off (default: the tenant's AI settings)")
    gr.add_argument("--mode", choices=["certified", "fields", "hybrid", "direct_sql"],
                    help="answer every example in this Ask mode (US-259; default: each source's own mode)")
    gr.set_defaults(func=gold_run)

    rp = sub.add_parser("gold-report", help="show an evaluation run's scores per set and planner path")
    rp.add_argument("--tenant", required=True, help="tenant slug")
    rp.add_argument("--run", help="run id (default: the latest)")
    rp.add_argument("--grid", action="store_true", help="print the answer shape by tag grid as a table (US-250)")
    rp.set_defaults(func=gold_report)

    gg = sub.add_parser("gold-gate", help="compare a review-off and a review-on run (US-246)")
    gg.add_argument("--tenant", required=True, help="tenant slug")
    gg.add_argument("--off", required=True, help="run id queued with --review off")
    gg.add_argument("--on", required=True, help="run id queued with --review on")
    gg.set_defaults(func=gold_gate)

    sl = sub.add_parser("storage-lifecycle", help="show or apply the object store bucket lifecycle rules")
    sl.add_argument("--apply", action="store_true", help="put the rules (default: show wanted and current)")
    sl.add_argument("--export-days", type=int, default=2, help="delete export files left after this many days")
    sl.set_defaults(func=storage_lifecycle)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
