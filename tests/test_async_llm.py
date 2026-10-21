"""C11 — 느린(동기) 모델 호출이 서버의 이벤트 루프를 막지 않는지, Ollama 생성 제한이 붙는지 검증."""
import asyncio
import time

from agent.deps import SupervisorDeps
from agent.graph import build_graph
from stubs import StubFast, StubMCP, StubQuality


class SlowFast(StubFast):
    """route/plan이 동기 함수로 0.6초씩 '블로킹'한다 (실제 httpx.post 대기와 같은 상황)."""
    def route(self, q):
        time.sleep(0.6)
        return super().route(q)

    def plan(self, q, procedure):
        time.sleep(0.6)
        return super().plan(q, procedure)


def test_slow_sync_model_call_does_not_block_event_loop():
    deps = SupervisorDeps(fast_llm=SlowFast(), quality_llm=StubQuality(), mcp=StubMCP(),
                          session_store=None, version="v2", known_systems={"web-01"})
    state = {"query": "web-01 상태 진단해줘", "thread_id": "t", "history": [],
             "events": [], "cost_usd": 0.0}

    async def scenario():
        ticks = 0
        stop = False

        async def ticker():          # /health 같은 '다른 요청'을 흉내: 0.05초마다 한 번 응답
            nonlocal ticks
            while not stop:
                await asyncio.sleep(0.05)
                ticks += 1

        t = asyncio.create_task(ticker())
        await build_graph(deps).ainvoke(state)
        stop = True
        await t
        return ticks

    ticks = asyncio.run(scenario())
    # Before(노드에서 동기 호출 직접 실행): 모델 대기 1.2초 동안 ticker가 못 돌아 (조사 단계의 대기 때만 돌아) 10회 안팎.
    # After(to_thread): 대기 중에도 계속 돌아 25회 안팎.
    assert ticks >= 20, f"이벤트 루프가 막혔다 (ticks={ticks})"


def test_ollama_generation_limits_are_applied():
    from agent.ollama_llm import OLLAMA_TIMEOUT, _gen_params
    p = _gen_params()
    assert p["think"] is False                       # 기본: 사고 과정 토큰 생성 안 함
    assert p["options"]["num_predict"] == 700        # 출력 길이 상한
    assert p["options"]["temperature"] == 0.0        # 기존 설정 유지
    assert OLLAMA_TIMEOUT == 120.0                   # Before: 300초
