"""Plan (and optionally build) a dashboard through the AI dashboard API. Usage: python test_dashboard_ai.py <request-file> [connection-name] [build]"""
import json
import sys
import time

import httpx

sys.path.insert(0, "/Users/prodian/Desktop/PMO V2/tests/smoke")
from test_compose_smoke import EDGE, sign_in

pw = open("/Users/prodian/Desktop/PMO V2/deploy/compose/secrets/dev_admin_password").read().strip()
H = {"Host": "tela.localhost:8088", "Authorization": f"Bearer {sign_in('admin', pw)['access_token']}"}
req = open(sys.argv[1]).read()
name = sys.argv[2] if len(sys.argv) > 2 else None
home = httpx.get(f"{EDGE}/api/v1/home", headers=H, timeout=60).json()
conns = {s["connection_name"]: s["connection_id"] for s in home["greeting"]["scopes"]}
print("sources:", list(conns))
body = {"request": req, "min_widgets": 6}
if name:
    body["connection_id"] = conns[name]
r = httpx.post(f"{EDGE}/api/v1/dashboards/ai/plan", json=body, headers=H, timeout=60)
print(r.status_code, r.text[:300]); r.raise_for_status()
job = r.json()["job_id"]; t0 = time.time()
while True:
    s = httpx.get(f"{EDGE}/api/v1/dashboards/ai/jobs/{job}", headers=H, timeout=60).json()
    if s["status"] != "planning":
        break
    time.sleep(2)
print(f"took {time.time()-t0:.1f}s; steps:"); [print("  -", x) for x in s["steps"]]
if s["status"] == "failed":
    print("FAILED", s.get("error")); sys.exit(1)
p = s["plan"]
print("TITLE", p["title"], "| global filters", [(f["key"], f["shared"], f["reaches_all"]) for f in p["global_filters"]])
for i, pg in enumerate(p["reports"]):
    print(f"\n== Page {i+1}: {pg['title']}  ({pg['kpi_count']} KPIs, {pg['widget_count']} widgets, filled {pg['filled_in']})")
    for k in pg["kpis"]:
        print("   KPI", k["title"], "|", k["measure"], "|", k["filters"])
    for w in pg["widgets"]:
        print("   W", w["visual"], "|", w["title"], "|", w["measure"], "| by", w["group_by"], "| time", w["time"], "| f", w["filters"], "| top", w["top"])
print("\nNOT POSSIBLE:", p["not_possible"])
print("DROPPED:"); [print("  -", d) for d in p["dropped"]]
json.dump(s, open(sys.argv[1] + ".plan.json", "w"), indent=1)
if len(sys.argv) > 3 and sys.argv[3] == "build":
    b = httpx.post(f"{EDGE}/api/v1/dashboards/ai/jobs/{job}/build", headers=H, timeout=600)
    print("BUILD", b.status_code); out = b.json()
    if b.status_code >= 300:
        print(out); sys.exit(1)
    from collections import Counter
    print("dashboard", out["dashboard_id"], Counter(r["status"] for r in out["results"]))
    for r in out["results"]:
        if r["status"] != "ok":
            print("  ", r["report"], "|", r["title"], "|", r["status"], r.get("message"))
    json.dump(out, open(sys.argv[1] + ".build.json", "w"), indent=1)
