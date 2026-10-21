"""plan/clarify/finalize 노드와 그래프 분기 테스트 — 가짜 모델·가짜 MCP를 주입해 실행한다.
(SupervisorDeps 의존성 주입 덕분에 Ollama·MCP 서버 없이 그래프 전체를 검증)"""
import asyncio

from agent.deps import SupervisorDeps
from agent.graph import build_graph
from stubs import StubFast, StubMCP, StubQuality

KNOWN = {"auth-service", "web-01", "payments-service"}


def _run(query, history=None, alerts_by_system=None):
    fast, mcp = StubFast(), StubMCP(alerts_by_system)
    deps = SupervisorDeps(fast_llm=fast, quality_llm=StubQuality(), mcp=mcp,
                          session_store=None, version="v2", known_systems=KNOWN)
    state = {"query": query, "thread_id": "t", "history": history or [],
             "events": [], "cost_usd": 0.0}
    return asyncio.run(build_graph(deps).ainvoke(state)), fast, mcp


def test_unregistered_system_asks_back_without_calling_model_or_mcp():   # B2
    out, fast, mcp = _run("billing-service 응답 지연을 진단해주세요")
    assert "조회할 수 없습니다" in out["final"] and "billing-service" in out["final"]
    assert fast.plan_calls == 0 and mcp.calls == []        # 모델·MCP 호출 없음 → 빠르고 비용 0
    assert out["events"][-1]["pipeline"] == "clarify"


def test_no_system_asks_back():                                           # B3
    out, _, mcp = _run("로그인이 너무 느려요")
    assert "어느 시스템" in out["final"] and mcp.monitored == []


def test_all_mentioned_systems_are_investigated():                        # B6
    out, _, mcp = _run("web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘")
    assert sorted(mcp.monitored) == ["auth-service", "web-01"]            # 모델이 하나만 세워도 보정


def test_follow_up_investigates_previous_system_and_says_so():            # B7
    history = [{"role": "user", "content": "payments-service 결제 응답 지연을 진단해주세요"}]
    out, _, mcp = _run("그 시스템 메모리는 어때?", history)
    assert mcp.monitored == ["payments-service"]
    assert "이전 대화의 payments-service" in out["final"]


def test_mixed_question_investigates_registered_and_notes_unregistered():
    out, _, mcp = _run("billing-service랑 web-01 상태 확인해줘")
    assert mcp.monitored == ["web-01"]
    assert "미등록 시스템(조회 불가): billing-service" in out["final"]


def test_healthy_system_gets_no_runbook():                                # B5 / 개선 후 B1
    out, _, mcp = _run("web-01 상태 진단해줘")
    assert mcp.runbook_uris == []                                         # db-down 폴백 없음
    assert any(e.get("reason") == "런북 근거 없음" for e in out["events"])


def test_alert_selects_matching_runbook():
    out, _, mcp = _run("web-01 상태 진단해줘",
                       alerts_by_system={"web-01": ["CPU 91% (threshold 80%)"]})
    assert mcp.runbook_uris == ["runbook://incident-response/cpu-spike"]
