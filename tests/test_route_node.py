"""route_node 단위 테스트 — 의존성 주입(SupervisorDeps) 덕분에 모델·MCP 없이 노드만 검증한다."""
import asyncio

from agent.deps import SupervisorDeps
from agent.graph import route_node


class StubFastLLM:
    """항상 chat이라고 답하는 소형 모델 흉내 (B1 상황 재현)."""
    def __init__(self, route="chat"):
        self._route = route

    def route(self, query):
        return {"route": self._route, "needs_analysis": False}, {"usd": 0.0}


def _deps(route="chat"):
    return SupervisorDeps(fast_llm=StubFastLLM(route), quality_llm=None, mcp=None,
                          session_store=None, version="v2")


def _run(query, route="chat"):
    state = {"query": query, "events": [], "cost_usd": 0.0}
    return asyncio.run(route_node(state, _deps(route)))


def test_misrouted_ops_question_is_overridden_to_incident():
    out = _run("web-01 상태 진단해줘")
    assert out["route"] == "incident"
    assert out["events"][-1]["overridden_from"] == "chat"


def test_greeting_stays_chat():
    out = _run("안녕, 너는 누구야?")
    assert out["route"] == "chat"
    assert "overridden_from" not in out["events"][-1]


def test_model_incident_decision_is_kept():
    out = _run("web-01 상태 진단해줘", route="incident")
    assert out["route"] == "incident"
    assert "overridden_from" not in out["events"][-1]
