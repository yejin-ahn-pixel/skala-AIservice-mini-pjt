"""routing.py 단위 테스트 — B1(web-01 상태 진단이 chat으로 오분류) 회귀 방지."""
import pytest

from agent.routing import should_force_incident

KNOWN = {"auth-service", "web-01", "payments-service"}


@pytest.mark.parametrize("query", [
    "web-01 상태 진단해줘",                       # B1
    "billing-service 응답 지연을 진단해주세요",    # 미등록 시스템도 incident 경로로
    "payments-service CPU 급증 원인 확인해줘",
])
def test_ops_question_with_system_is_incident(query):
    assert should_force_incident(query, KNOWN) is True


@pytest.mark.parametrize("query", [
    "안녕, 너는 누구야?",
    "제 이름은 민수입니다",
    "제 이름이 뭐였죠?",
    "web-01은 어떤 서버야?",        # 시스템명만 있고 운영 키워드 없음
    "서버 상태가 궁금해요",          # 운영 키워드만 있고 시스템명 없음 → 모델 판단에 맡김
])
def test_casual_or_incomplete_is_not_forced(query):
    assert should_force_incident(query, KNOWN) is False


def test_follow_up_with_deictic_and_history_is_incident():      # B7
    history = [{"role": "user", "content": "payments-service 결제 응답 지연을 진단해주세요"}]
    assert should_force_incident("그 시스템 메모리는 어때?", KNOWN, history) is True


def test_deictic_without_history_is_not_forced():
    assert should_force_incident("그 시스템 메모리는 어때?", KNOWN, []) is False
