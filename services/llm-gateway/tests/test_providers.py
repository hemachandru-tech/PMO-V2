"""Provider adapters approved under DEC-01, checked on the wire (mock transports, no network)."""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx
import httpx2
import openai
import pytest
from fastapi.testclient import TestClient
from google import genai
from google.genai import types

from tela_common import service_auth
from tela_common.errors import TelaError
from tela_common.secrets import FileSecretsStore
from tela_gateway.ledger import MemoryLedger
from tela_gateway.main import SERVICE_AUDIENCE, create_app
from tela_gateway.providers import Credentials, build_provider
from tela_gateway.providers.anthropic import AnthropicProvider
from tela_gateway.providers.base import closed_schema
from tela_gateway.providers.gemini import GeminiProvider
from tela_gateway.providers.openai import OpenAIProvider
from tela_gateway.settings import GatewaySettings
from tela_gateway.tenant_settings import MemoryTenantSettings

KEY = "k3y-for-tests-0123456789abcdefghijklmnopqrstuvwxyz"
TENANT = "0192f3b2-0000-7000-8000-000000000001"
SCHEMA = {"type": "object", "properties": {"intent": {"type": "string"}}, "required": ["intent"]}
MESSAGES = [{"role": "system", "content": "You classify questions."}, {"role": "user", "content": "overdue by region"}]


class Wire:
    """Records requests and answers each with the next canned (status, json) pair."""

    def __init__(self, *answers: tuple[int, dict[str, Any]]) -> None:
        self.answers = list(answers)
        self.requests: list[Any] = []

    def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        status, body = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return (httpx if isinstance(request, httpx.Request) else httpx2).Response(status, json=body)

    def body(self, i: int = -1) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.requests[i].content)
        return data


def _anthropic(wire: Wire) -> AnthropicProvider:
    http = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(wire))
    return AnthropicProvider("sk-test", client=anthropic.AsyncAnthropic(api_key="sk-test", http_client=http,
                                                                        max_retries=0))


def _openai(wire: Wire, *, azure: bool = False) -> OpenAIProvider:
    http = openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(wire))
    client: Any = (openai.AsyncAzureOpenAI(api_key="k", azure_endpoint="https://acme.openai.azure.com",
                                           api_version="2024-10-21", http_client=http, max_retries=0)
                   if azure else openai.AsyncOpenAI(api_key="k", http_client=http, max_retries=0))
    return OpenAIProvider("k", client=client)


def _gemini(wire: Wire) -> GeminiProvider:
    opts = types.HttpOptions(httpx_async_client=httpx.AsyncClient(transport=httpx.MockTransport(wire)),
                             retry_options=types.HttpRetryOptions(attempts=1))
    return GeminiProvider("k", client=genai.Client(api_key="k", http_options=opts))


ANTHROPIC_OK = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                "content": [{"type": "thinking", "thinking": "", "signature": "s"},
                            {"type": "text", "text": '{"intent": "ask"}'}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 40, "output_tokens": 9}}


async def test_anthropic_structured_output_with_fallback_and_system() -> None:
    wire = Wire((200, ANTHROPIC_OK))
    out = await _anthropic(wire).generate(model="claude-opus-5-5", messages=MESSAGES, json_schema=SCHEMA,
                                          max_tokens=8)
    assert (out.text, out.prompt_tokens, out.completion_tokens) == ('{"intent": "ask"}', 40, 9)
    req = wire.requests[-1]
    body = wire.body()
    assert req.headers["authorization"] == "Bearer sk-test" and "server-side-fallback-2026-07-01" in req.headers["anthropic-beta"]
    assert body["system"] == "You classify questions." and [m["role"] for m in body["messages"]] == ["user"]
    assert body["fallbacks"] == "default" and body["max_tokens"] >= 2048  # thinking needs headroom
    assert body["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert "temperature" not in body and "thinking" not in body


async def test_anthropic_other_models_refusals_and_errors() -> None:
    wire = Wire((200, {**ANTHROPIC_OK, "model": "claude-haiku-5-5"}))
    await _anthropic(wire).generate(model="claude-haiku-5-5", messages=MESSAGES, json_schema=None, max_tokens=4000)
    assert "fallbacks" not in wire.body() and "format" not in wire.body()["output_config"]
    assert wire.body()["max_tokens"] == 4000

    refused = Wire((200, {**ANTHROPIC_OK, "stop_reason": "refusal", "content": []}))
    with pytest.raises(TelaError) as exc:
        await _anthropic(refused).generate(model="claude-haiku-5-5", messages=MESSAGES, json_schema=None,
                                           max_tokens=10)
    assert exc.value.code == "invalid_output"

    bad_key = Wire((401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}))
    with pytest.raises(TelaError) as exc:
        await _anthropic(bad_key).generate(model="claude-haiku-5-5", messages=MESSAGES, json_schema=None,
                                           max_tokens=10)
    assert exc.value.code == "planner_unavailable" and exc.value.details == {"status": 401}
    with pytest.raises(TelaError) as exc:
        await _anthropic(wire).embed(model="x", texts=["a"])
    assert exc.value.code == "provider_not_allowed"


OPENAI_OK = {"id": "c1", "object": "chat.completion", "created": 1, "model": "gpt-5",
             "choices": [{"index": 0, "finish_reason": "stop",
                          "message": {"role": "assistant", "content": '{"intent": "ask"}'}}],
             "usage": {"prompt_tokens": 30, "completion_tokens": 7, "total_tokens": 37}}


@pytest.mark.parametrize("azure", [False, True])
async def test_openai_and_azure_chat_with_schema(azure: bool) -> None:
    wire = Wire((200, OPENAI_OK))
    out = await _openai(wire, azure=azure).generate(model="planner-deploy", messages=MESSAGES, json_schema=SCHEMA,
                                                    max_tokens=500)
    assert (out.text, out.prompt_tokens, out.completion_tokens) == ('{"intent": "ask"}', 30, 7)
    req = wire.requests[-1]
    body = wire.body()
    if azure:
        assert req.url.host == "acme.openai.azure.com" and "planner-deploy" in req.url.path
        assert req.headers["api-key"] == "k"
    else:
        assert req.url.host == "api.openai.com" and req.headers["authorization"] == "Bearer k"
    assert body["messages"][0]["role"] == "system" and body["max_completion_tokens"] == 500
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA


async def test_openai_embeddings_filter_and_errors() -> None:
    wire = Wire((200, {"object": "list", "model": "text-embedding-3-large", "usage": {"prompt_tokens": 2,
                                                                                       "total_tokens": 2},
                       "data": [{"object": "embedding", "index": 1, "embedding": [0.2, 0.3]},
                                {"object": "embedding", "index": 0, "embedding": [0.0, 0.1]}]}))
    vectors = await _openai(wire).embed(model="text-embedding-3-large", texts=["a", "b"], dim=2)
    assert vectors == [[0.0, 0.1], [0.2, 0.3]] and wire.body()["dimensions"] == 2

    filtered = Wire((200, {**OPENAI_OK, "choices": [{**OPENAI_OK["choices"][0], "finish_reason": "content_filter"}]}))
    with pytest.raises(TelaError) as exc:
        await _openai(filtered).generate(model="m", messages=MESSAGES, json_schema=None, max_tokens=10)
    assert exc.value.code == "invalid_output"
    limited = Wire((429, {"error": {"message": "slow down", "type": "rate_limit"}}))
    with pytest.raises(TelaError) as exc:
        await _openai(limited).generate(model="m", messages=MESSAGES, json_schema=None, max_tokens=10)
    assert exc.value.code == "planner_unavailable"


async def test_gemini_generate_and_embed() -> None:
    wire = Wire((200, {"candidates": [{"content": {"role": "model", "parts": [{"text": '{"intent": "ask"}'}]},
                                       "finishReason": "STOP"}],
                       "usageMetadata": {"promptTokenCount": 21, "candidatesTokenCount": 6}}))
    out = await _gemini(wire).generate(model="gemini-3-flash", messages=MESSAGES, json_schema=SCHEMA, max_tokens=300)
    assert (out.text, out.prompt_tokens, out.completion_tokens) == ('{"intent": "ask"}', 21, 6)
    body = wire.body()
    assert "gemini-3-flash:generateContent" in str(wire.requests[-1].url)
    assert body["systemInstruction"]["parts"][0]["text"] == "You classify questions."
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseJsonSchema"] == SCHEMA

    emb = Wire((200, {"embeddings": [{"values": [0.1, 0.2]}, {"values": [0.3, 0.4]}]}))
    assert await _gemini(emb).embed(model="gemini-embedding-001", texts=["a", "b"], dim=2) == [[0.1, 0.2],
                                                                                                 [0.3, 0.4]]
    assert "outputDimensionality" in json.dumps(emb.body())

    denied = Wire((403, {"error": {"code": 403, "message": "API key not valid", "status": "PERMISSION_DENIED"}}))
    with pytest.raises(TelaError) as exc:
        await _gemini(denied).generate(model="g", messages=MESSAGES, json_schema=None, max_tokens=10)
    assert exc.value.details == {"status": 403}


def test_build_provider_needs_keys_and_azure_endpoint() -> None:
    with pytest.raises(TelaError):
        build_provider("anthropic", ollama_url="http://o", timeout=5)
    with pytest.raises(TelaError):
        build_provider("azure_openai", ollama_url="http://o", timeout=5, credentials=Credentials("k"))
    azure = build_provider("azure_openai", ollama_url="http://o", timeout=5,
                           credentials=Credentials("k", "https://acme.openai.azure.com"), azure_api_version="2024-10-21")
    assert azure.name == "azure_openai"
    assert {build_provider(n, ollama_url="http://o", timeout=5, credentials=Credentials("k")).name
            for n in ("anthropic", "openai", "gemini")} == {"anthropic", "openai", "gemini"}
    with pytest.raises(ValueError):
        build_provider("bedrock", ollama_url="http://o", timeout=5)


def test_closed_schema_is_a_copy() -> None:
    nested = {"type": "object", "properties": {"s": {"type": "array", "items": {"type": "object", "properties": {}}}}}
    closed = closed_schema(nested)
    assert closed["additionalProperties"] is False
    assert closed["properties"]["s"]["items"]["additionalProperties"] is False
    assert "additionalProperties" not in nested


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer " + service_auth.issue(KEY.encode(), sub="tela-api", aud=SERVICE_AUDIENCE)}


async def test_gateway_reads_the_tenant_key_from_the_secrets_store(tmp_path: Any, monkeypatch: Any) -> None:
    """End to end through /generate: tenant key from the store, provider built once, key never in the answer."""
    store = FileSecretsStore(tmp_path)
    ref = f"tenants/{TENANT}/llm/anthropic"
    await store.put(ref, {"api_key": "sk-ant-tenant"})
    seen: list[Credentials] = []

    class Recorder:
        name = "anthropic"

        async def generate(self, **_: Any) -> Any:
            from tela_gateway.providers.base import ProviderResult

            return ProviderResult('{"intent": "ask"}', 3, 2, "claude-haiku-5-5")

        async def embed(self, **_: Any) -> list[list[float]]:
            return [[0.0] * 1024]

    def fake_build(name: str, **kw: Any) -> Any:
        seen.append(kw["credentials"])
        return Recorder()

    monkeypatch.setattr("tela_gateway.main.build_provider", fake_build)
    settings = GatewaySettings(service_signing_key=KEY, llm_allowed_providers=["ollama", "anthropic"])
    tenants = MemoryTenantSettings({TENANT: {"provider": "anthropic", "models": {"planner": "claude-haiku-5-5"},
                                             "secret_ref": ref, "allow_aggregates": False}})
    client = TestClient(create_app(settings, ledger=MemoryLedger(), tenant_settings=tenants, secrets=store))
    body = {"tenant_id": TENANT, "user_id": "u1", "agent": "AG-01", "task": "intent",
            "messages": [{"role": "user", "content": "hi"}]}
    for _ in range(2):
        resp = client.post("/generate", json=body, headers=_auth())
        assert resp.status_code == 200, resp.text
        assert "sk-ant-tenant" not in resp.text and resp.json()["provider"] == "anthropic"
    assert seen == [Credentials("sk-ant-tenant", None)]  # one client per key

    # The tenant turned aggregates off: narration is refused before any provider call.
    narr = client.post("/generate", json={**body, "agent": "AG-08", "task": "narrate"}, headers=_auth())
    assert narr.status_code == 403 and narr.json()["error"]["code"] == "forbidden"

    no_key = MemoryTenantSettings({TENANT: {"provider": "anthropic", "models": {"planner": "m"}}})
    client2 = TestClient(create_app(settings, ledger=MemoryLedger(), tenant_settings=no_key, secrets=store))
    resp = client2.post("/generate", json=body, headers=_auth())
    assert resp.status_code == 503 and "API key" in resp.json()["error"]["message"]

    providers = client.get("/providers", headers=_auth()).json()
    assert providers["allow_aggregates"] is True and "azure_openai" in providers["keyed"]
    assert "anthropic" not in providers["embedders"]


class Embedder:
    name = "openai"

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim
        self.calls: list[dict[str, Any]] = []

    async def generate(self, **_: Any) -> Any:
        from tela_gateway.providers.base import ProviderResult

        return ProviderResult("ok", 1, 1, "m")

    async def embed(self, *, model: str, texts: list[str], dim: int | None = None) -> list[list[float]]:
        self.calls.append({"model": model, "dim": dim})
        return [[0.0] * self.dim for _ in texts]


def test_deployment_switch_turns_aggregates_off() -> None:
    settings = GatewaySettings(service_signing_key=KEY, llm_provider="ollama", llm_allowed_providers=["ollama"],
                               llm_model_default="m", llm_allow_aggregates=False)
    client = TestClient(create_app(settings, provider=Embedder(), ledger=MemoryLedger()))
    body = {"tenant_id": TENANT, "agent": "AG-08", "task": "narrate", "messages": [{"role": "user", "content": "x"}]}
    assert client.post("/generate", json=body, headers=_auth()).json()["error"]["code"] == "forbidden"
    assert client.post("/generate", json={**body, "task": "explain"}, headers=_auth()).status_code == 200


def test_embeddings_follow_the_tenant_provider_when_it_has_one() -> None:
    settings = GatewaySettings(service_signing_key=KEY, llm_allowed_providers=["ollama", "openai", "anthropic"])
    body = {"tenant_id": TENANT, "texts": ["overdue go-lives"]}
    cases = [
        ({"provider": "openai", "models": {"planner": "p", "embeddings": "text-embedding-3-large"}},
         "openai", "text-embedding-3-large"),
        ({"provider": "anthropic", "models": {"planner": "p", "embeddings": "x"}}, "ollama", "mxbai-embed-large"),
        ({"provider": "openai", "models": {"planner": "p"}}, "ollama", "mxbai-embed-large"),
        (None, "ollama", "mxbai-embed-large"),
    ]
    for row, provider, model in cases:
        embedder = Embedder()
        tenants = MemoryTenantSettings({TENANT: row} if row else {})
        client = TestClient(create_app(settings, provider=embedder, ledger=MemoryLedger(), tenant_settings=tenants))
        out = client.post("/embed", json=body, headers=_auth()).json()
        assert (out["provider"], out["model"]) == (provider, model)
        assert embedder.calls == [{"model": model, "dim": 1024}]
    wrong = TestClient(create_app(settings, provider=Embedder(dim=768), ledger=MemoryLedger()))
    assert wrong.post("/embed", json=body, headers=_auth()).json()["error"]["code"] == "invalid_output"


def test_rerank_scores_with_the_local_cross_encoder_or_says_it_is_off() -> None:
    """US-174: /rerank calls TEI's /rerank and returns one score per text in order; no reranker deployed → null."""
    seen: list[dict[str, Any]] = []

    def tei(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        assert request.url.path == "/rerank"
        # TEI answers sorted by score, not in input order.
        return httpx.Response(200, json=[{"index": 1, "score": 0.91}, {"index": 0, "score": 0.004}])

    from tela_gateway.providers.rerank import TeiReranker

    settings = GatewaySettings(service_signing_key=KEY, llm_allowed_providers=["ollama"])
    reranker = TeiReranker("http://reranker", http=httpx.AsyncClient(base_url="http://reranker",
                                                                     transport=httpx.MockTransport(tei)))
    client = TestClient(create_app(settings, ledger=MemoryLedger(), reranker=reranker))
    body = {"tenant_id": TENANT, "query": "heatmap of region", "texts": ["Regional portfolio", "Delivery heatmap"]}
    out = client.post("/rerank", json=body, headers=_auth()).json()
    assert out == {"model": "BAAI/bge-reranker-v2-m3", "scores": [0.004, 0.91]}
    assert seen == [{"query": "heatmap of region", "texts": ["Regional portfolio", "Delivery heatmap"],
                     "raw_scores": False, "truncate": True}]
    assert client.post("/rerank", json=body).status_code == 401

    off = TestClient(create_app(GatewaySettings(service_signing_key=KEY, rerank_url=""), ledger=MemoryLedger()))
    assert off.post("/rerank", json=body, headers=_auth()).json() == {"model": None, "scores": None}

    down = TeiReranker("http://reranker", http=httpx.AsyncClient(
        base_url="http://reranker", transport=httpx.MockTransport(lambda _: httpx.Response(503))))
    failing = TestClient(create_app(settings, ledger=MemoryLedger(), reranker=down))
    assert failing.post("/rerank", json=body, headers=_auth()).json()["error"]["code"] == "planner_unavailable"
    short = TeiReranker("http://reranker", http=httpx.AsyncClient(
        base_url="http://reranker",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[{"index": 0, "score": 0.5}]))))
    partial = TestClient(create_app(settings, ledger=MemoryLedger(), reranker=short))
    assert partial.post("/rerank", json=body, headers=_auth()).json()["error"]["code"] == "invalid_output"


async def test_ollama_holds_output_to_the_schema_and_thinks_only_when_asked() -> None:
    """US-191: the schema is Ollama's output format; a thinking model (qwen3) reasons first only when asked."""
    from tela_gateway.providers.ollama import OllamaProvider

    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": '{"a": 1}'}, "prompt_eval_count": 3, "eval_count": 2})

    http = httpx.AsyncClient(base_url="http://o", transport=httpx.MockTransport(handler))
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    out = await OllamaProvider("http://o", http=http).generate(model="qwen3:8b", messages=[], json_schema=schema,
                                                               max_tokens=10)
    assert out.text == '{"a": 1}' and seen[0]["format"] == schema and seen[0]["think"] is False
    await OllamaProvider("http://o", http=http).generate(model="qwen3:8b", messages=[], json_schema=None,
                                                         max_tokens=10, think=True)
    assert "think" not in seen[1] and "format" not in seen[1]
