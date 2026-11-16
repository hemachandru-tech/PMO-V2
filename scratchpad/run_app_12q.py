"""Ask the 12 questions through the Tela Ask API and save SQL, rows and headline per question."""
import json, subprocess, sys, time
import httpx

sys.path.insert(0, "/Users/prodian/Desktop/PMO V2/tests/smoke")
from test_compose_smoke import sign_in, EDGE  # noqa: E402

CONN = "01a1da60-f79e-7093-8592-dc5ee47fb344"
OUT = sys.argv[1]
QS = [
    "How many active demand requests currently require resource allocation?",
    "Can you show the detailed register of projects for Reasons and comments in On-Hold Projects Aging?",
    "What is the total count of active projects tracked in Regional Portfolio Dashboard?",
    "How are projects distributed across each CIO portfolio in Reporting cadence?",
    "Can you show the detailed register of projects with their RAG health status (overall, schedule, cost, and issue KPI)?",
    "Which projects have the highest volume of change requests awaiting approval?",
    "How are projects distributed across each the designated dimension in Days since the project was last edited?",
    "Can you show the detailed register of projects for Journey legs in Demand-to-Project Conversion?",
    "Can you show the detailed register of demand requests for Top 20 requests in Top 20 Strategic Initiatives?",
    "What is the total count of active projects tracked in Projects Due in Next 30/60/90 Days?",
    "What is the total count of active projects tracked in Critical Path Projects?",
    "How many open demand requests have exceeded their planned gate sign-off dates?",
]

pw = open("/Users/prodian/Desktop/PMO V2/deploy/compose/secrets/dev_admin_password").read().strip()


def headers():
    return {"Host": "tela.localhost:8088", "Authorization": f"Bearer {sign_in('admin', pw)['access_token']}"}


def events(turn_id):
    sql = f"SELECT event, data FROM chat.turn_events WHERE turn_id = '{turn_id}' ORDER BY 1;"
    r = subprocess.run(["docker", "exec", "tela-postgres-1", "psql", "-U", "tela_owner", "-d", "tela", "-t", "-A",
                        "-F", "\t", "-c", sql], capture_output=True, text=True)
    out = {}
    for line in r.stdout.splitlines():
        if "\t" in line:
            ev, data = line.split("\t", 1)
            try:
                out.setdefault(ev, []).append(json.loads(data))
            except Exception:
                out.setdefault(ev, []).append(data)
    return out


results = []
for i, q in enumerate(QS, 1):
    h = headers()
    t0 = time.time()
    r = httpx.post(f"{EDGE}/api/v1/ask/turns", json={"question": q, "connection_id": CONN,
                   "context": {"connection_id": CONN}}, headers=h, timeout=60)
    rec = {"q": i, "question": q, "post_status": r.status_code}
    try:
        turn_id = r.json()["turn_id"]
    except Exception:
        rec["error"] = r.text[:500]
        results.append(rec)
        continue
    t = {}
    for _ in range(180):
        time.sleep(2)
        t = httpx.get(f"{EDGE}/api/v1/ask/turns/{turn_id}", headers=h, timeout=30).json()
        if t.get("status") in ("complete", "refused", "failed", "error", "awaiting_clarification"):
            break
    ev = events(turn_id)
    comp = (ev.get("complete") or [{}])[-1]
    rec.update({
        "turn_id": turn_id, "status": t.get("status"), "secs": round(time.time() - t0, 1),
        "headline": (t.get("narrative") or {}).get("headline") if isinstance(t.get("narrative"), dict) else None,
        "sql": comp.get("sql") if isinstance(comp, dict) else None,
        "columns": comp.get("columns") if isinstance(comp, dict) else None,
        "rows": (comp.get("rows") if isinstance(comp, dict) else None),
        "event_names": list(ev.keys()),
        "turn": t,
    })
    print(f"Q{i} {rec['status']} {rec['secs']}s rows={len(rec['rows'] or [])} :: {rec['headline']}", flush=True)
    results.append(rec)
    json.dump(results, open(OUT, "w"), indent=1, default=str)
print("done")
