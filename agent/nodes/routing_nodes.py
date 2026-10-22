"""시작 단계 노드: route → (chat_answer | plan → clarify/gate). 조사 대상을 정하는 단계."""
import asyncio

from langsmith import traceable

from ..deps import SupervisorDeps
from ..routing import continue_clarified_query, decide_needs_analysis, should_force_incident
from ..state import AgentState, _cost
from ..streaming import get_writer
from ..targets import apply_targets, format_clarify, resolve_targets

@traceable(name="route_node")
async def route_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 5 — 모든 쿼리가 무거운 파이프라인을 탈 필요는 없다."""
    # [C11] route()는 동기 함수(내부에서 HTTP 호출). async 노드에서 그대로 부르면 응답을 기다리는
    # 동안 이벤트 루프 전체가 멈춰 /health 등 다른 요청이 같이 지연된다.
    # Before: result, usage = deps.fast_llm.route(state["query"])
    # After : to_thread로 별도 스레드에서 실행 → 기다리는 동안 이벤트 루프는 다른 요청을 처리
    result, usage = await asyncio.to_thread(deps.fast_llm.route, state["query"])
    route = result["route"]
    event = {"type": "RouteDecided", "route": route}
    # 소형 모델이 운영 질문을 chat으로 오분류하면 조회 없이 답이 지어진다 → 코드가 보정
    if route == "chat" and should_force_incident(state["query"], deps.known_systems,
                                         state.get("history")):
        route = "incident"
        event = {"type": "RouteDecided", "route": route, "overridden_from": "chat"}
    return {"route": route, "needs_analysis": result["needs_analysis"],
            "cost_usd": _cost(state, usage),
            "events": state.get("events", []) + [event]}


@traceable(name="chat_answer_node")
async def chat_answer_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 5 direct 경로 — 히스토리 기반 응답 (Wave 12 세션 증명 지점).

    토큰 단위 스트리밍 — get_writer()는 /chat/stream처럼 custom 모드로 소비될
    때만 실제로 전송되고, /chat(ainvoke)처럼 스트리밍하지 않는 호출에서는 자동으로
    no-op이 된다."""
    writer = get_writer()
    reply, usage = await deps.fast_llm.chat(
        state["query"], state.get("history", []),
        on_token=lambda piece: writer({"type": "AnswerToken", "delta": piece}))
    return {"final": reply, "last_prompt": usage.get("prompt"),
            "cost_usd": _cost(state, usage),
            "events": state["events"] + [{"type": "Finalize", "pipeline": "chat-direct"}]}


@traceable(name="plan_node")
async def plan_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 4 — Plan/Task 분해. Wave 2의 MCP Prompt가 표준 절차를 깔아준다.

    조사 대상은 모델이 아니라 코드(targets.resolve_targets)가 먼저 정한다 — 대상을
    정할 수 없으면(미등록·미지정) 모델/MCP를 부르지 않고 되묻기로 넘어간다.
    resource와 마찬가지로 이름을 어느 서버가 들고 있는지 미리 가정하지 않고,
    find_prompt()로 연결된 서버들을 먼저 조회해 찾은 뒤에만 get_prompt를 보낸다."""
    query = continue_clarified_query(state["query"], state.get("history"), deps.known_systems)
    res = resolve_targets(query, state.get("history"), deps.known_systems)
    resolved = {"type": "TargetsResolved", "targets": res.targets,
                "unregistered": res.unregistered, "source": res.source}
    if not res.targets:
        return {"clarify": True, "unregistered": res.unregistered,
                "target_source": res.source, "events": state["events"] + [resolved]}

    prompt_server = await deps.mcp.find_prompt("incident_analysis_prompt")
    procedure = await deps.mcp.get_prompt(
        "incident_analysis_prompt",
        {"system_name": res.targets[0], "symptom": query},
        server=prompt_server,
    )
    # [C11] plan()도 동기 HTTP 호출 → 스레드로 분리 (Before: deps.fast_llm.plan(...) 직접 호출)
    result, usage = await asyncio.to_thread(deps.fast_llm.plan, query, procedure)
    tasks = apply_targets(result["tasks"], res.targets)
    targets = [t["target"] for t in tasks
               if t["agent"] == "research" and t.get("target")]
    # [C12] 분석 실행 여부는 모델 의견 + 코드 규칙(대상 2개 이상)으로 결정한다.
    # Before: "needs_analysis": result["needs_analysis"]  (모델 값을 그대로 사용)
    needs_analysis = decide_needs_analysis(result["needs_analysis"], res.targets)
    event = {"type": "PlanCreated", "task_count": len(tasks), "targets": targets,
             "needs_analysis_model": result["needs_analysis"],
             "needs_analysis": needs_analysis}
    return {"query": query, "tasks": tasks, "needs_analysis": needs_analysis,
            "clarify": False, "unregistered": res.unregistered,
            "target_source": res.source, "cost_usd": _cost(state, usage),
            "events": state["events"] + [resolved, event]}


@traceable(name="clarify_node")
async def clarify_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """조사 대상을 정할 수 없을 때 — 가정하지 않고 사용자에게 되묻는다 (모델 호출 없음)."""
    reply = format_clarify(state.get("unregistered", []), deps.known_systems)
    return {"final": reply,
            "events": state["events"] + [{"type": "ClarifyAsked"},
                                         {"type": "Finalize", "pipeline": "clarify"}]}


@traceable(name="gate_node")
async def gate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 6 — 권위 게이트. needs_analysis=False인데 섞여 들어온 analysis task를
    프롬프트가 아니라 코드로 강제 제거한다."""
    tasks = state["tasks"]
    if not state.get("needs_analysis", False):
        kept = [t for t in tasks if t["agent"] != "analysis"]
        removed = len(tasks) - len(kept)
    else:
        kept, removed = tasks, 0
    event = {"type": "GateApplied", "removed_analysis_tasks": removed}
    return {"tasks": kept, "gate_removed": removed,
            "events": state["events"] + [event]}
