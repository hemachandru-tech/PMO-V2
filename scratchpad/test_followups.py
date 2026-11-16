import json
import subprocess
import sys
import time

import httpx

sys.path.insert(0, "/Users/prodian/Desktop/PMO V2/tests/smoke")
from test_compose_smoke import EDGE, sign_in

pw = open("/Users/prodian/Desktop/PMO V2/deploy/compose/secrets/dev_admin_password").read().strip()
H = {"Host": "tela.localhost:8088", "Authorization": f"Bearer {sign_in('admin', pw)['access_token']}"}
home = httpx.get(f"{EDGE}/api/v1/home", headers=H, timeout=60).json()
CONN = {s["connection_name"]: s["connection_id"] for s in home["greeting"]["scopes"]}["ball-report"]

def events(tid):
    r = subprocess.run(["docker", "exec", "tela-postgres-1", "psql", "-U", "tela_owner", "-d", "tela", "-t", "-A", "-F", "\t",
                        "-c", f"SELECT event, data FROM chat.turn_events WHERE turn_id = '{tid}' ORDER BY 1"], capture_output=True, text=True)
    out = {}
    for line in r.stdout.splitlines():
        if "\t" in line:
            ev, d = line.split("\t", 1)
            try: out.setdefault(ev, []).append(json.loads(d))
            except Exception: pass
    return out

def ask(q, conv):
    r = httpx.post(f"{EDGE}/api/v1/ask/turns", json={"question": q, "conversation_id": conv, "connection_id": CONN}, headers=H, timeout=60).json()
    tid = r["turn_id"]
    for _ in range(90):
        ev = events(tid)
        if "complete" in ev or "error" in ev: break
        time.sleep(2)
    c = (ev.get("complete") or [{}])[-1]
    res = c.get("result") or {}
    rows = res.get("rows") or []
    ctx = c.get("context") or {}
    print(f"\nQ: {q}\n  SQL: {c.get('sql')}\n  rows={res.get('row_count')} first={rows[:14]}\n  memory: earlier_turns={ctx.get('earlier_turns')} samples={ctx.get('samples')}")
    if "error" in ev: print("  ERROR", ev["error"][-1])
    return r["conversation_id"]

conv = ask("how many students are there", None)
ask("give the list", conv)
ask("give the student list", conv)
print("\n--- New chat ---")
ask("give the list", None)
print("\n--- extra follow-ups ---")
c2 = ask("how many coaches are there", None)
ask("which ones are head coach", c2)
ask("how many deliveries are there", c2)
