"""End-to-end smoke test against a running Compose stack (TEST-001 on real Keycloak; TEST-096 baseline).

Signs in through Keycloak's real login form with Authorization Code + PKCE (no password grant),
then calls the API through the edge proxy. Run after `docker compose ... --profile core up -d`:

    TELA_SMOKE=1 uv run pytest -q tests/smoke

Hostnames (tela.localhost, auth.localhost) are sent as Host headers to 127.0.0.1:8088 so the
test does not depend on local DNS for *.localhost.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TELA_SMOKE"), reason="set TELA_SMOKE=1 with the stack running")

EDGE = os.environ.get("TELA_SMOKE_EDGE", "http://127.0.0.1:8088")
WEB_ORIGIN = "http://tela.localhost:8088"
REALM = "http://auth.localhost:8088/realms/tela"
SECRETS = Path(__file__).resolve().parents[2] / "deploy" / "compose" / "secrets"


def _cookie_header(client: httpx.Client) -> dict[str, str]:
    """Keycloak marks its cookies Secure. Browsers still send them to http://*.localhost (a secure
    context), but httpx does not over plain HTTP, so forward them explicitly."""
    jar = "; ".join(f"{c.name}={c.value}" for c in client.cookies.jar)
    return {"Cookie": jar} if jar else {}


def _via_edge(url: str) -> tuple[str, dict[str, str]]:
    parts = urlsplit(url)
    target = f"{EDGE}{parts.path}" + (f"?{parts.query}" if parts.query else "")
    return target, {"Host": parts.netloc}


def sign_in(username: str, password: str) -> dict[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    redirect_uri = f"{WEB_ORIGIN}/auth/callback"
    auth_url = f"{REALM}/protocol/openid-connect/auth?" + urlencode({
        "client_id": "tela-web", "response_type": "code", "scope": "openid profile email",
        "redirect_uri": redirect_uri, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    with httpx.Client(timeout=15) as client:
        url, headers = _via_edge(auth_url)
        page = client.get(url, headers=headers)
        assert page.status_code == 200, page.text[:300]
        action = re.search(r'<form[^>]+id="kc-form-login"[^>]+action="([^"]+)"', page.text)
        assert action, "Keycloak login form not found"
        url, headers = _via_edge(html.unescape(action.group(1)))
        login = client.post(url, headers={**headers, **_cookie_header(client)},
                            data={"username": username, "password": password})
        assert login.status_code == 302, f"login failed: {login.status_code}"
        location = login.headers["location"]
        assert location.startswith(redirect_uri)
        query = parse_qs(urlsplit(location).query)
        assert query["state"] == [state]

        url, headers = _via_edge(f"{REALM}/protocol/openid-connect/token")
        token = client.post(url, headers={**headers, "Origin": WEB_ORIGIN}, data={
            "grant_type": "authorization_code", "client_id": "tela-web", "code": query["code"][0],
            "redirect_uri": redirect_uri, "code_verifier": verifier,
        })
        assert token.status_code == 200, token.text
        return token.json()


def api_get(path: str, access_token: str | None) -> httpx.Response:
    headers = {"Host": "tela.localhost"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return httpx.get(f"{EDGE}{path}", headers=headers, timeout=15)


@pytest.mark.parametrize(
    "user,level,audience",
    [("admin", "admin", []), ("nomsa", "creator", ["cio"]), ("viewer", "viewer", [])],
)
def test_sign_in_and_me(user: str, level: str, audience: list[str]) -> None:
    password = (SECRETS / f"dev_{ {'admin': 'admin', 'nomsa': 'creator', 'viewer': 'viewer'}[user] }_password").read_text().strip()
    tokens = sign_in(user, password)
    assert "refresh_token" in tokens
    me = api_get("/api/v1/me", tokens["access_token"])
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["level"] == level and body["audience_roles"] == audience
    admin_call = api_get("/api/v1/admin/roles", tokens["access_token"])
    assert admin_call.status_code == (200 if level == "admin" else 403)


def test_wrong_password_is_refused() -> None:
    with pytest.raises(AssertionError, match="login failed"):
        sign_in("admin", "not-the-password")


def test_id_token_is_not_accepted_as_access_token() -> None:
    password = (SECRETS / "dev_viewer_password").read_text().strip()
    tokens = sign_in("viewer", password)
    assert api_get("/api/v1/me", tokens["id_token"]).status_code == 401


def test_edge_hides_internal_endpoints() -> None:
    for path in ("/readyz", "/metrics"):
        assert api_get(path, None).status_code == 404
    assert api_get("/api/v1/me", None).status_code == 401
