"""C13 — 서버 입력 검증, SSE 오류 전달, 노드별 시간 기록 검증 (가짜 모델·MCP, lifespan 없이 실행)."""
import json

import pytest
from fastapi.testclient import TestClient

import server
from agent.deps import SupervisorDeps
from agent.graph import build_graph
from stubs import StubFast, StubMCP, StubQuality

KNOWN = {"auth-service", "web-01", "payments-service"}


class MemStore:
    def __init__(self):
        self.rows = []

    def load(self, thread_id):
        return []

    def append(self, thread_id, role, content):
        self.rows.append((thread_id, role, content))


class BrokenFast(StubFast):
    def route(self, q):
        raise RuntimeError("model exploded")     # 스트림 도중 예외 상황


def _client(fast=None):
    deps = SupervisorDeps(fast_llm=fast or StubFast(), quality_llm=StubQuality(),
                          mcp=StubMCP(), session_store=MemStore(), version="v2",
                          known_systems=KNOWN)
    server.app.state.deps = deps
    server.app.state.graph = build_graph(deps)
    server.app.state.llm_backend = "stub"
    server.app.state.instance_id = 1
    return TestClient(server.app, raise_server_exceptions=False), deps


def _sse(resp):
    return [line[6:] for line in resp.text.split("\n\n") if line.startswith("data: ")]


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_post_chat_rejects_blank_message(bad):
    client, _ = _client()
    assert client.post("/chat", json={"message": bad}).status_code == 422   # Before: 200


def test_post_chat_rejects_too_long_message():
    client, _ = _client()
    r = client.post("/chat", json={"message": "a" * (server.MAX_MESSAGE_LEN + 1)})
    assert r.status_code == 422


def test_stream_rejects_blank_message():
    client, _ = _client()
    assert client.get("/chat/stream", params={"message": "   "}).status_code == 422


def test_stream_error_is_sent_as_error_event_then_done():
    client, deps = _client(BrokenFast())
    r = client.get("/chat/stream", params={"message": "web-01 상태 진단해줘"})
    chunks = _sse(r)
    err = json.loads(chunks[-2])
    # Before: 예외가 나면 연결이 끊겨 화면이 조용히 멈췄다. After: Error 이벤트 + [DONE]
    assert err["type"] == "Error" and err["error"] == "RuntimeError"
    assert chunks[-1] == "[DONE]"
    assert deps.session_store.rows == []        # 실패한 대화는 이력에 저장하지 않는다


def test_stream_success_has_final_answer_and_proxy_safe_headers():
    client, _ = _client()
    r = client.get("/chat/stream", params={"message": "web-01 상태 진단해줘"})
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-accel-buffering"] == "no"
    chunks = _sse(r)
    assert chunks[-1] == "[DONE]"
    assert any(json.loads(c).get("type") == "FinalAnswer" for c in chunks[:-1])


def test_chat_metadata_contains_node_timings():
    client, _ = _client()
    meta = client.post("/chat", json={"message": "web-01 상태 진단해줘"}).json()["metadata"]
    timings = meta["node_timings"]
    assert {"route", "plan", "research", "write", "validate", "finalize"} <= set(timings)
    assert all(isinstance(v, float) and v >= 0 for v in timings.values())
