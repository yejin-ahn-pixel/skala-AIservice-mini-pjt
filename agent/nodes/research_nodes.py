"""조사 단계 노드: research(가드+병렬+재시도) → replan_check(런북 선택) → analysis."""
import asyncio
import os
import time

from langsmith import traceable

from ..deps import SupervisorDeps
from ..incident_rules import classify_incident_type
from ..retry import with_retry
from ..state import AgentState, _cost

# [C10] MCP 호출 1건당 최대 대기 시간(초). 이 시간을 넘기면 TimeoutError → with_retry가 재시도한다.
# Before: 시간 제한이 없어 MCP 서버가 응답 없이 멈추면 요청 전체가 끝없이 기다렸다.
MCP_CALL_TIMEOUT = float(os.getenv("MCP_CALL_TIMEOUT", "15"))


@traceable(name="research_node")
async def research_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 15 가드 + Wave 10b 병렬 + Wave 9 재시도 + Wave 2 Tool/Resource 실호출."""
    tasks = state["tasks"]
    research_indices = [i for i, t in enumerate(tasks)
                        if t["agent"] == "research" and t["status"] == "pending"]
    # Wave 15 — 객체가 아니라 '실행할 목록'을 검사한다 (not research_agent 버그의 수정판)
    if not research_indices:
        return {"events": state["events"] +
                [{"type": "ResearchSkipped", "reason": "pending research task 0개"}]}

    retry_total = state.get("retry_count", 0)

    # URI를 코드에 하드코딩하지 않는다. 여러 MCP 서버가 연결돼 있고 그중 어느 서버가
    # "get_incident_runbook" resource를 제공하는지 모른다고 가정하고, 먼저 연결된
    # 서버들을 조회(list)해 유효한 서버를 찾은 뒤에만 그 서버로 요청한다.
    needs_runbook = any(tasks[i]["kind"] != "monitoring" for i in research_indices)
    runbook_server = runbook_uri_template = None
    if needs_runbook:
        runbook_server, runbook_uri_template = await deps.mcp.find_resource_template(
            "get_incident_runbook")

    async def run_one(idx: int) -> dict:
        task = tasks[idx]
        await asyncio.sleep(0.35)   # 태스크당 고정 지연 — 병렬 효과를 실측하기 위함
        if task["kind"] == "monitoring":
            # [C10] wait_for로 호출 1건의 대기 시간을 제한한다. 시간 초과(TimeoutError)는
            # with_retry가 '일시적 오류'로 보고 재시도한다. (Before: 제한 없이 무한 대기 가능)
            async def call():
                return await asyncio.wait_for(
                    deps.mcp.call_tool("get_monitoring_status",
                                       {"system_name": task["target"]}),
                    timeout=MCP_CALL_TIMEOUT)
            data, retries = await with_retry(call)
            return {"idx": idx, "source": "monitoring", "data": data, "retries": retries}
        uri = runbook_uri_template.format(incident_type=task["target"])
        data = await asyncio.wait_for(
            deps.mcp.read_resource(uri, server=runbook_server), timeout=MCP_CALL_TIMEOUT)
        return {"idx": idx, "source": "runbook", "data": data, "retries": 0}

    started = time.perf_counter()
    # ── [C10] 부분 실패 격리 ───────────────────────────────────────────────
    # Before: 예외를 그대로 전파해서, 작업 하나가 실패하면 이미 성공한 조사 결과까지 버려지고
    #         요청 전체가 오류로 끝났다.
    #   results = await asyncio.gather(*(run_one(i) for i in research_indices))
    # After : return_exceptions=True 이면 실패도 '결과 목록의 한 원소(예외 객체)'로 돌아온다.
    #         아래 루프에서 성공/실패를 나눠 처리해, 성공한 데이터로 계속 진행한다.
    results = await asyncio.gather(*(run_one(i) for i in research_indices),
                                   return_exceptions=True)
    elapsed = round(time.perf_counter() - started, 2)

    new_tasks = [dict(t) for t in tasks]
    collected = list(state.get("collected", []))
    events = list(state["events"])
    errors = list(state.get("research_errors", []))
    for idx, r in zip(research_indices, results):
        t = new_tasks[idx]
        if isinstance(r, BaseException):
            # 실패한 작업: 'failed'로 표시(= pending이 아니므로 재조사 루프에 다시 걸리지 않음),
            # 이벤트와 오류 목록에 남겨 답변 끝에서 사용자에게 알린다.
            t["status"] = "failed"
            reason = type(r).__name__
            errors.append(f"{t['target']}({reason})")
            events.append({"type": "TaskFailed", "id": t["id"], "source": t["kind"],
                           "target": t["target"], "error": reason})
            continue
        t["status"] = "done"
        collected.append({"source": r["source"], "data": r["data"]})
        retry_total += r["retries"]
        events.append({"type": "TaskCompleted", "id": t["id"],
                       "source": r["source"], "target": t["target"],
                       "retries": r["retries"]})
    return {"tasks": new_tasks, "collected": collected, "retry_count": retry_total,
            "parallel_elapsed": elapsed, "events": events, "research_errors": errors}


@traceable(name="replan_check_node")
async def replan_check_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 7 — 1차 수집 결과 평가. runbook이 없으면 gap task만 추가해 재조사."""
    has_runbook = any(c["source"] == "runbook" for c in state.get("collected", []))
    rounds = state.get("replan_rounds", 0)
    if has_runbook or rounds >= 1:
        verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": True}
        return {"events": state["events"] + [verdict]}
    incident_type = classify_incident_type(state.get("collected", []))
    if incident_type is None:   # 증상 근거가 없으면(정상 시스템 등) 엉뚱한 런북을 붙이지 않는다
        verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": True,
                   "reason": "런북 근거 없음"}
        return {"events": state["events"] + [verdict]}
    gap_task = {"id": "task-gap-runbook", "agent": "research", "kind": "runbook",
                "target": incident_type, "status": "pending"}
    verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": False,
               "gap_task": gap_task["id"], "target": incident_type}
    return {"tasks": state["tasks"] + [gap_task], "replan_rounds": rounds + 1,
            "events": state["events"] + [verdict]}


@traceable(name="analysis_node")
async def analysis_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 6 보완 — needs_analysis=True가 정당한 경우(모델의 자기모순이 아니라
    실제로 패턴 비교가 필요하다고 판단한 경우)를 위한 실행 경로. gate가 살려둔
    agent=='analysis' task가 있을 때만 수집된 monitoring 결과들을 비교 분석한다."""
    tasks = state["tasks"]
    pending = [t for t in tasks if t["agent"] == "analysis" and t["status"] == "pending"]
    if not pending:
        return {"events": state["events"] +
                [{"type": "AnalysisSkipped", "reason": "analysis task 0개"}]}
    # [C11] analyze()도 동기 HTTP 호출 → 스레드로 분리
    # Before: summary, usage = deps.fast_llm.analyze(state.get("collected", []))
    summary, usage = await asyncio.to_thread(deps.fast_llm.analyze, state.get("collected", []))
    new_tasks = [dict(t) for t in tasks]
    for t in new_tasks:
        if t["agent"] == "analysis" and t["status"] == "pending":
            t["status"] = "done"
    collected = state.get("collected", []) + [{"source": "analysis", "data": summary}]
    event = {"type": "AnalysisCompleted", "length": len(summary)}
    return {"tasks": new_tasks, "collected": collected,
            "cost_usd": _cost(state, usage), "events": state["events"] + [event]}
