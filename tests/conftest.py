import os
import sys
from pathlib import Path

# [C13] 테스트는 LangSmith로 추적 데이터를 보내지 않는다.
# server.py를 import하면 load_dotenv()가 실제 .env(LangSmith 키, TRACING=true)를 읽는데,
# 그러면 테스트 실행 기록이 개선 전/후 증거를 모아 둔 LangSmith 프로젝트에 섞여 들어간다.
# load_dotenv는 이미 설정된 환경변수를 덮어쓰지 않으므로, import 전에 꺼 둔다.
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_API_KEY"] = ""
os.environ["LANGSMITH_API_KEY"] = ""

# `pytest`를 어디서 실행해도 agent 패키지를 import할 수 있게 프로젝트 루트를 경로에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest


@pytest.fixture(autouse=True)
def no_stream_writer(monkeypatch):
    """토큰 스트리밍(get_stream_writer)은 이 테스트들의 관심사가 아니다 — 환경(Python 버전)에
    따라 비동기 컨텍스트에서 실패할 수 있어 no-op으로 대체한다."""
    import agent.streaming as streaming_module
    monkeypatch.setattr(streaming_module, "get_stream_writer", lambda: (lambda payload: None))
