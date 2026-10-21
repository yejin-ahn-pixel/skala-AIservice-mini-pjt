"""[C13] 노드별 실행 시간·오류 기록 (문제 #10: 성능 관측 부족).

Before: 어느 노드가 느린지, 어디서 실패했는지 알려면 LangSmith를 열어봐야 했다.
After : 모든 노드를 이 래퍼로 감싸 (1) 로그로 남기고 (2) 상태의 node_timings에 누적한다.
        /chat 응답의 metadata.node_timings로 바로 볼 수 있어 캡처·비교가 쉽다.
"""
import logging
import time
from functools import partial

logger = logging.getLogger("agent.node")


def timed_node(name: str, fn, deps):
    """fn(state, deps=deps)을 실행하며 걸린 시간을 기록하는 노드 함수를 돌려준다."""
    bound = partial(fn, deps=deps)   # 의존성 주입은 기존과 동일 (Wave 13)

    async def run(state):
        started = time.perf_counter()
        try:
            delta = await bound(state)
        except Exception as exc:
            logger.error("node=%s FAILED after %.2fs: %s: %s", name,
                         time.perf_counter() - started, type(exc).__name__, exc)
            raise
        elapsed = time.perf_counter() - started
        logger.info("node=%s elapsed=%.2fs", name, elapsed)
        # 같은 노드가 여러 번 실행되면(재조사·재작성) 시간을 합산한다
        timings = dict(state.get("node_timings", {}))
        timings[name] = round(timings.get(name, 0.0) + elapsed, 2)
        return {**delta, "node_timings": timings}

    run.__name__ = name
    return run
