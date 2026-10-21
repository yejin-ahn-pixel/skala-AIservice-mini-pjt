"""에이전트 그래프 공통 상태 — 모든 노드가 같이 쓰는 AgentState와 비용 계산."""
from typing import TypedDict


class AgentState(TypedDict, total=False):
    """그래프를 흐르는 상태. 각 노드는 이 딕셔너리를 읽고, 바꾼 키만 돌려준다(LangGraph가 병합).
    total=False → 모든 키가 선택 사항 (노드 단계에 따라 점점 채워진다)."""
    query: str
    thread_id: str
    history: list[dict]
    route: str
    needs_analysis: bool
    tasks: list[dict]
    gate_removed: int
    collected: list[dict]
    replan_rounds: int
    retry_count: int
    parallel_elapsed: float
    draft: str
    validation_attempts: int
    validation_feedback: str
    final: str
    last_prompt: str
    cost_usd: float
    events: list[dict]
    unregistered: list[str]   # 질문에 있으나 등록 안 된 시스템 (조회 불가 안내용)
    target_source: str        # 조사 대상의 출처: query | history | none
    clarify: bool             # True면 조사 대상을 정할 수 없어 되묻기로 종료
    research_errors: list[str]  # [C10] 조회에 실패한 작업 목록 (예: "web-01(TimeoutError)")
    node_timings: dict[str, float]  # [C13] 노드별 누적 실행 시간(초)
    validation_warning: str   # 최대 시도 후 경고와 함께 통과한 경우의 미통과 사유


def _cost(state: AgentState, usage: dict) -> float:
    return round(state.get("cost_usd", 0.0) + usage["usd"], 6)
