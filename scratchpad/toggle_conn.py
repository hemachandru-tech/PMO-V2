import sys, httpx
sys.path.insert(0, "/Users/prodian/Desktop/PMO V2/tests/smoke")
from test_compose_smoke import sign_in, EDGE  # noqa: E402
pw = open("/Users/prodian/Desktop/PMO V2/deploy/compose/secrets/dev_admin_password").read().strip()
H = {"Host": "tela.localhost:8088", "Authorization": f"Bearer {sign_in('admin', pw)['access_token']}"}
r = httpx.post(f"{EDGE}/api/v1/connections/{sys.argv[1]}/{sys.argv[2]}", headers=H, timeout=60)
print(r.status_code, r.text[:200])
