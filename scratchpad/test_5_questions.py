"""Test all 5 questions against live PMO V2 API and compare with MySQL Ground Truth."""

import json
import time
import subprocess
import pymysql
import httpx

EDGE = "http://localhost:8088"
CONN_ID = "01a1da60-f79e-7093-8592-dc5ee47fb344"

# 1. Connect to MySQL for Ground Truth
my_conn = pymysql.connect(host="127.0.0.1", port=3306, user="root", password="1234", database="tpg")
my_cursor = my_conn.cursor()

def run_ground_truth(sql: str):
    my_cursor.execute(sql)
    return my_cursor.fetchall()

# 2. Authenticate to PMO V2
import sys
sys.path.insert(0, '/Users/prodian/Desktop/PMO V2/tests/smoke')
from test_compose_smoke import sign_in, EDGE

with open("/Users/prodian/Desktop/PMO V2/deploy/compose/secrets/dev_admin_password") as f:
    pw = f.read().strip()

tok = sign_in("admin", pw)["access_token"]
headers = {"Host": "tela.localhost:8088", "Authorization": f"Bearer {tok}"}

QUESTIONS = [
    {
        "id": "Q1",
        "question": "How many active demand requests currently require resource allocation?",
        "gt_sql": "SELECT COUNT(*) FROM tpg_projectrequests WHERE tpg_requeststatename = 'Active';",
    },
    {
        "id": "Q2",
        "question": "Can you show the detailed register of projects for Reasons and comments in On-Hold Projects Aging?",
        "gt_sql": "SELECT COALESCE(tpgmea_projectnamesearchwithno, tpg_projectname, 'Unnamed Project') as project_name, tpg_projectstatusname, tpgmea_evwronholdreasonname, tpgmea_projectputonholdreason FROM tpg_project WHERE tpg_projectstatusname = 'On Hold';",
    },
    {
        "id": "Q3",
        "question": "What is the total count of active projects tracked in Regional Portfolio Dashboard?",
        "gt_sql": "SELECT COUNT(*) FROM tpg_project WHERE tpg_projectstatusname = 'Active';",
    },
    {
        "id": "Q4",
        "question": "How are projects distributed across each CIO portfolio in Reporting cadence?",
        "gt_sql": "SELECT COALESCE(tpgmea_customcioname, 'Unassigned') as cio, COUNT(*) as project_count FROM tpg_project GROUP BY tpgmea_customcioname ORDER BY project_count DESC;",
    },
    {
        "id": "Q5",
        "question": "Show project distribution by overall RAG health status.",
        "gt_sql": "SELECT COALESCE(tpg_overallkpiname, 'Unreported') as rag_status, COUNT(*) as project_count FROM tpg_project GROUP BY tpg_overallkpiname ORDER BY project_count DESC;",
    }
]

results = []

for item in QUESTIONS:
    qid = item["id"]
    q = item["question"]
    gt_sql = item["gt_sql"]
    print(f"\n==========================================")
    print(f"Testing {qid}: {q}")
    
    # Ground Truth
    gt_rows = [list(r) for r in run_ground_truth(gt_sql)]
    print(f"Ground Truth SQL: {gt_sql}")
    print(f"Ground Truth Rows ({len(gt_rows)}): {gt_rows[:3]}")
    
    # API Call
    post_res = httpx.post(f"{EDGE}/api/v1/ask/turns", json={
        "question": q,
        "connection_id": CONN_ID,
        "context": {"connection_id": CONN_ID}
    }, headers=headers, timeout=30)
    
    turn_id = post_res.json()["turn_id"]
    
    # Poll for completion
    pmo_status = "running"
    pmo_headline = None
    for _ in range(30):
        time.sleep(1)
        res = httpx.get(f"{EDGE}/api/v1/ask/turns/{turn_id}", headers=headers)
        t = res.json()
        pmo_status = t.get("status")
        if pmo_status in ("complete", "refused", "failed", "error", "awaiting_clarification"):
            pmo_headline = t.get("narrative", {}).get("headline") if t.get("narrative") else None
            break
            
    # Fetch SQL & rows from turn_events
    cmd = [
        "docker", "exec", "tela-postgres-1", "psql", "-U", "tela_owner", "-d", "tela", "-t", "-A", "-c",
        f"SELECT data FROM chat.turn_events WHERE turn_id = '{turn_id}' AND event = 'complete';"
    ]
    db_res = subprocess.run(cmd, capture_output=True, text=True)
    pmo_sql = None
    pmo_rows = []
    if db_res.stdout.strip():
        data = json.loads(db_res.stdout.strip())
        pmo_sql = data.get("sql")
        pmo_rows = data.get("rows", [])
        
    print(f"PMO Dashboard Status: {pmo_status}")
    print(f"PMO Dashboard SQL: {pmo_sql}")
    print(f"PMO Dashboard Headline: {pmo_headline}")
    print(f"PMO Dashboard Rows ({len(pmo_rows)}): {pmo_rows[:3]}")
    
    results.append({
        "id": qid,
        "question": q,
        "gt_sql": gt_sql,
        "gt_rows": gt_rows,
        "pmo_status": pmo_status,
        "pmo_sql": pmo_sql,
        "pmo_rows": pmo_rows,
        "pmo_headline": pmo_headline
    })

with open("/tmp/pmo_5_questions_validated.json", "w") as f:
    json.dump(results, f, indent=2, default=str)

print("\nAll 5 questions tested and saved to /tmp/pmo_5_questions_validated.json")
