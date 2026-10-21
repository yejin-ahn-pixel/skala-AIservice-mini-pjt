"""토큰 스트리밍 writer 접근 지점 — 한 곳으로 모아 테스트에서 쉽게 대체한다."""
from langgraph.config import get_stream_writer


def get_writer():
    return get_stream_writer()
