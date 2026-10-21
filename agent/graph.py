"""v2 Supervisor — 실제 langgraph.StateGraph (Wave 4·5·6·7·8·10b·15 통합).

route → (chat 직행 | incident 파이프라인)
incident: plan(MCP Prompt 절차 반영) → gate(권위 게이트) → research(가드+병렬+재시도)
          → replan_check(부족 시 gap task 후 재조사) → analysis(needs_analysis=True일 때만
          실제 실행) → write → validate(반려 시 재작성) → finalize

모든 노드는 SupervisorDeps를 functools.partial로 주입받는 순수 함수다(Wave 13).
각 노드는 @traceable로 LangSmith에 계층 트리로 기록된다.

노드 구현은 nodes/ 아래(routing·research·answer)로, 공통 상태는 state.py로 분리했다.
이 파일은 노드를 서로 잇는 배선(분기 함수 + build_graph)만 담당한다.
"""
from langgraph.graph import END, StateGraph

from .deps import SupervisorDeps
from .nodes.answer_nodes import finalize_node, validate_node, write_node
from .nodes.research_nodes import analysis_node, replan_check_node, research_node
from .nodes.routing_nodes import (chat_answer_node, clarify_node, gate_node,
                                  plan_node, route_node)
from .state import AgentState
from .timing import timed_node

__all__ = ["AgentState", "build_graph", "route_node"]


def _after_route(state: AgentState) -> str:
    return "chat_answer" if state["route"] == "chat" else "plan"


def _after_plan(state: AgentState) -> str:
    return "clarify" if state.get("clarify") else "gate"


def _after_replan_check(state: AgentState) -> str:
    last = state["events"][-1]
    return "analysis" if last.get("is_sufficient") else "research"


def _after_validate(state: AgentState) -> str:
    return "finalize" if state.get("validation_feedback") is None else "write"


def build_graph(deps: SupervisorDeps):
    g = StateGraph(AgentState)
    # Wave 13 — 명시적 의존성 주입. [C13] 같은 주입을 하되 노드별 실행 시간도 기록한다.
    # Before: node = lambda fn: partial(fn, deps=deps)
    node = lambda fn: timed_node(fn.__name__.removesuffix("_node"), fn, deps)
    g.add_node("route", node(route_node))
    g.add_node("chat_answer", node(chat_answer_node))
    g.add_node("plan", node(plan_node))
    g.add_node("clarify", node(clarify_node))
    g.add_node("gate", node(gate_node))
    g.add_node("research", node(research_node))
    g.add_node("replan_check", node(replan_check_node))
    g.add_node("analysis", node(analysis_node))
    g.add_node("write", node(write_node))
    g.add_node("validate", node(validate_node))
    g.add_node("finalize", node(finalize_node))

    g.set_entry_point("route")
    g.add_conditional_edges("route", _after_route,
                            {"chat_answer": "chat_answer", "plan": "plan"})
    g.add_edge("chat_answer", END)
    g.add_conditional_edges("plan", _after_plan, {"clarify": "clarify", "gate": "gate"})
    g.add_edge("clarify", END)
    g.add_edge("gate", "research")
    g.add_edge("research", "replan_check")
    g.add_conditional_edges("replan_check", _after_replan_check,
                            {"analysis": "analysis", "research": "research"})
    g.add_edge("analysis", "write")
    g.add_edge("write", "validate")
    g.add_conditional_edges("validate", _after_validate,
                            {"finalize": "finalize", "write": "write"})
    g.add_edge("finalize", END)
    return g.compile()
