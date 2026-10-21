"""C12 — 분석(analysis) 단계 실행 여부를 코드가 결정하는지 검증."""
import asyncio

from agent.deps import SupervisorDeps
from agent.graph import build_graph
from agent.routing import decide_needs_analysis
from stubs import StubFast, StubMCP, StubQuality

KNOWN = {"auth-service", "web-01", "payments-service"}


def test_rule_single_target_never_needs_analysis():
    assert decide_needs_analysis(True, ["web-01"]) is False     # 모델이 true여도 대상 1개면 false


def test_rule_two_targets_follow_model_opinion():
    assert decide_needs_analysis(True, ["web-01", "auth-service"]) is True
    assert decide_needs_analysis(False, ["web-01", "auth-service"]) is False


class AnalyzingFast(StubFast):
    """항상 needs_analysis=True + analysis task를 포함해 계획하고, analyze 호출 횟수를 센다."""
    def __init__(self):
        super().__init__()
        self.analyze_calls = 0

    def plan(self, q, procedure):
        result, _ = super().plan(q, procedure)
        result["needs_analysis"] = True
        result["tasks"].append({"id": "task-9", "agent": "analysis", "kind": "analysis",
                                "target": "", "status": "pending"})
        return result, {"usd": 0.0}

    def analyze(self, collected):
        self.analyze_calls += 1
        return "특이 패턴 없음", {"usd": 0.0}


def _run(query):
    fast = AnalyzingFast()
    deps = SupervisorDeps(fast_llm=fast, quality_llm=StubQuality(), mcp=StubMCP(),
                          session_store=None, version="v2", known_systems=KNOWN)
    state = {"query": query, "thread_id": "t", "history": [], "events": [], "cost_usd": 0.0}
    return asyncio.run(build_graph(deps).ainvoke(state)), fast


def test_single_system_skips_analysis_llm_call():
    out, fast = _run("web-01 상태 진단해줘")
    assert fast.analyze_calls == 0                       # Before: 1회 호출(불필요한 지연·비용)
    plan = next(e for e in out["events"] if e["type"] == "PlanCreated")
    assert plan["needs_analysis_model"] is True and plan["needs_analysis"] is False
    assert out["gate_removed"] == 1                      # 게이트가 analysis task를 제거


def test_two_systems_still_run_analysis():
    out, fast = _run("web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘")
    assert fast.analyze_calls == 1                       # 비교가 의미 있는 경우는 그대로 실행
