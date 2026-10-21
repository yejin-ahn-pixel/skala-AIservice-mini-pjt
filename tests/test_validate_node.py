"""validate_node + 그래프 재작성 루프 테스트 — 근거 없는 초안은 반려되고, 끝내 못 고치면 경고가 붙는다."""
import asyncio

from agent.deps import SupervisorDeps
from agent.graph import build_graph
from stubs import SeqQuality, StubFast, StubMCP

KNOWN = {"auth-service", "web-01", "payments-service"}
ALERTS = {"payments-service": ["CPU 91% (threshold 80%)"]}

FABRICATED = ("1) payments-service CPU 30.0%, 알림 CPU 91% (threshold 80%). "
              "2) 최근 배포/트래픽 변화 없음. 3) 오토스케일링으로 분산하세요.")
GROUNDED = ("1) payments-service CPU 30.0%, 알림 CPU 91% (threshold 80%). "
            "2) 런북에 따라 인스턴스 추가를 검토하세요. 3) 정상화 후 원인을 분석하세요.")


def _run(query, quality, alerts_by_system=None):
    deps = SupervisorDeps(fast_llm=StubFast(), quality_llm=quality,
                          mcp=StubMCP(alerts_by_system), session_store=None,
                          version="v2", known_systems=KNOWN)
    state = {"query": query, "thread_id": "t", "history": [], "events": [], "cost_usd": 0.0}
    return asyncio.run(build_graph(deps).ainvoke(state))


def _validations(out):
    return [e for e in out["events"] if e["type"] == "Validation"]


def test_fabricated_claim_is_rejected_then_rewritten():                    # B4
    quality = SeqQuality([FABRICATED, GROUNDED])
    out = _run("payments-service 결제 응답 지연을 진단해주세요", quality, ALERTS)
    v = _validations(out)
    assert [e["verdict"] for e in v] == ["불합격", "합격"]
    assert "issues" in v[0]
    assert quality.feedbacks[1] and "배포" in quality.feedbacks[1]          # 사유가 재작성에 전달됨
    assert out["final"] == GROUNDED                                         # 경고 없음


def test_uninvestigated_system_in_answer_is_rejected_then_fixed():         # B6
    only_auth = "1) auth-service CPU 30.0%, 메모리 40.0%. 2) 이상 없음. 3) 계속 관찰하세요."
    both = ("1) web-01 CPU 30.0%, 메모리 40.0%. 2) auth-service CPU 30.0%, 메모리 40.0%. "
            "3) 둘 다 정상입니다.")
    out = _run("web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘",
               SeqQuality([only_auth, both]))
    assert [e["verdict"] for e in _validations(out)] == ["불합격", "합격"]
    assert out["final"] == both


def test_gives_up_after_three_attempts_with_visible_warning():
    out = _run("payments-service 결제 응답 지연을 진단해주세요",
               SeqQuality([FABRICATED]), ALERTS)
    v = _validations(out)
    assert len(v) == 3
    assert v[-1]["verdict"].startswith("합격(최대 시도")
    assert "검증 미통과 항목" in out["final"] and "배포" in out["final"]    # 조용한 통과가 아님
