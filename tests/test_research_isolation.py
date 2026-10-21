"""C10 — 조사 작업 하나가 실패(예외·멈춤)해도 나머지 결과로 답변이 나오는지 검증한다."""
import asyncio

import agent.nodes.research_nodes as research_nodes
from agent.deps import SupervisorDeps
from agent.graph import build_graph
from stubs import StubFast, StubMCP, StubQuality

KNOWN = {"auth-service", "web-01", "payments-service"}
QUERY = "web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘"


def _run(mcp):
    deps = SupervisorDeps(fast_llm=StubFast(), quality_llm=StubQuality(), mcp=mcp,
                          session_store=None, version="v2", known_systems=KNOWN)
    state = {"query": QUERY, "thread_id": "t", "history": [], "events": [], "cost_usd": 0.0}
    return asyncio.run(build_graph(deps).ainvoke(state))


def test_one_failed_system_does_not_break_the_request():
    out = _run(StubMCP(fail_systems={"web-01"}))
    # Before: ConnectionError가 그대로 전파되어 ainvoke 자체가 예외로 끝났다.
    failed = [e for e in out["events"] if e["type"] == "TaskFailed"]
    assert [e["target"] for e in failed] == ["web-01"]
    assert any(e["type"] == "TaskCompleted" and e["target"] == "auth-service"
               for e in out["events"])                       # 성공한 시스템은 그대로 처리
    assert "일부 조회 실패" in out["final"] and "web-01(ConnectionError)" in out["final"]


def test_failed_task_is_not_retried_in_a_loop():
    out = _run(StubMCP(fail_systems={"web-01"}))
    statuses = {t["target"]: t["status"] for t in out["tasks"] if t["kind"] == "monitoring"}
    assert statuses == {"web-01": "failed", "auth-service": "done"}


def test_hung_mcp_call_times_out_and_is_isolated(monkeypatch):
    monkeypatch.setattr(research_nodes, "MCP_CALL_TIMEOUT", 0.05)   # 멈춘 호출을 빨리 끊는다
    out = _run(StubMCP(hang_systems={"auth-service"}))
    failed = [e for e in out["events"] if e["type"] == "TaskFailed"]
    assert [e["target"] for e in failed] == ["auth-service"]
    assert failed[0]["error"] in ("TimeoutError",)
