"""답변 단계 노드: write → validate(형식+근거 검증) → finalize."""
from langsmith import traceable

from ..deps import SupervisorDeps
from ..state import AgentState, _cost
from ..streaming import get_writer
from ..targets import format_notices
from ..verifier import build_feedback, find_issues

@traceable(name="write_node")
async def write_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 8/10a — 고품질 모델로 초안 작성 (1차는 의도적 저품질).

    토큰마다 attempt 번호를 같이 실어 보낸다 — validate가 반려해 재작성(attempt 증가)
    될 때 프론트가 새 말풍선으로 끊어서 보여줄 수 있게 하기 위함(Wave 8 재작성 루프를
    그대로 노출)."""
    attempt = state.get("validation_attempts", 0) + 1
    writer = get_writer()
    draft, usage = await deps.quality_llm.write(
        state["query"], state.get("collected", []),
        attempt, state.get("validation_feedback"),
        on_token=lambda piece: writer(
            {"type": "AnswerToken", "attempt": attempt, "delta": piece}))
    return {"draft": draft, "last_prompt": usage.get("prompt"),
            "cost_usd": _cost(state, usage),
            "events": state["events"] + [{"type": "DraftWritten", "attempt": attempt,
                                          "length": len(draft)}]}


@traceable(name="validate_node")
async def validate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 8 — 코드 기준 검증: 형식(30자 이상 + 절차 번호) + 근거 검사(verifier).

    근거 검사: 수치·시스템명이 수집된 사실에 있는지, 조사한 시스템이 답변에 모두 있는지,
    모니터링에 없는 '조사 결과 서술'이 없는지. 3회까지 반려·재작성하고, 그래도 불합격이면
    조용히 통과시키지 않고 답변 끝에 경고(validation_warning)를 붙인다."""
    draft = state["draft"]
    attempts = state.get("validation_attempts", 0) + 1   # 이번이 몇 번째 검증인지
    # ① 형식 검사 (Before에는 이것뿐이었다): 30자 이상 + 번호 절차 "1)" 포함
    format_ok = len(draft) >= 30 and "1)" in draft
    # ② 근거 검사 (C7에서 추가): 수치·시스템명·조사 누락·지어낸 원인을 수집 데이터와 대조
    issues = find_issues(draft, state["query"], state.get("collected", []))
    ok = format_ok and not issues       # 둘 다 통과해야 합격
    verdict = "합격" if ok else "불합격"
    warning = None
    if not ok and attempts >= 3:   # 실모델 무한 재작성 방지 — 최대 3회 후 경고와 함께 통과
        ok, verdict = True, "합격(최대 시도 도달 — 경고 표시)"
        warning = "; ".join(issues) or "형식 기준 미충족"
    # 불합격이면 사유를 문장으로 만들어 돌려준다 → 그래프가 write_node로 되돌아가 재작성
    # (_after_validate: feedback이 None이면 finalize, 아니면 write)
    feedback = None if ok else build_feedback(draft, format_ok, issues)
    event = {"type": "Validation", "attempt": attempts, "verdict": verdict}
    if issues:
        event["issues"] = issues
    return {"validation_attempts": attempts, "validation_feedback": feedback,
            "validation_warning": warning, "events": state["events"] + [event]}


@traceable(name="finalize_node")
async def finalize_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """최종 답변 = 초안 + 코드가 한 일을 알리는 안내문(※). 안내문 종류:
    미등록 시스템 제외 / 이전 대화 기준 조회 / 일부 조회 실패 / 검증 미통과 항목."""
    notice = format_notices(state.get("unregistered", []), state.get("target_source", ""),
                            [t["target"] for t in state.get("tasks", [])
                             if t["agent"] == "research"])
    # [C10] 조회에 실패한 작업이 있으면 숨기지 않고 알린다 (Before: 실패 시 요청 전체가 오류)
    if state.get("research_errors"):
        fail = f"※ 일부 조회 실패(해당 항목은 답변에서 제외): {', '.join(state['research_errors'])}"
        notice = f"{notice}\n{fail}" if notice else fail
    if state.get("validation_warning"):
        warn = f"※ 검증 미통과 항목: {state['validation_warning']}"
        notice = f"{notice}\n{warn}" if notice else warn
    final = state["draft"] + ("\n\n" + notice if notice else "")
    return {"final": final,
            "events": state["events"] + [{"type": "Finalize", "pipeline": "incident-v2"}]}
