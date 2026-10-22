"""라우팅 보강 — 소형 분류 모델의 오분류를 코드 규칙으로 보정한다.

fast 모델(qwen3:0.6b)은 "web-01 상태 진단해줘" 같은 명백한 운영 질문도 chat으로
분류하는 경우가 있다(실측). chat 경로는 모니터링을 조회하지 않으므로 답변이 근거 없이
지어내진다. gate_node(W6)와 같은 설계 원칙으로, 코드로 검증 가능한 판단은 코드가 내린다.
"""
import re

from .targets import DEICTIC, SYSTEM_LIKE, last_system_from_history

# 운영 이슈를 나타내는 키워드 (한 단어만으로는 chat으로 볼 수 없는 것들)
OPS_KEYWORDS = re.compile(
    r"상태|진단|장애|지연|느려|느리|느린|급증|다운|오류|에러|타임아웃|메모리|디스크|"
    r"알림|이상|점검|모니터링|cpu|incident|티켓", re.IGNORECASE)



def mentions_system(query: str, known_systems: set[str]) -> bool:
    """질문에 등록된 시스템명 또는 시스템 이름처럼 보이는 토큰이 있는가."""
    # 등록된 이름이 그대로 들어 있거나("web-01"), 등록은 안 됐지만 이름처럼 생겼으면
    # ("billing-service") True. 미등록 이름도 '시스템 질문'으로 봐야 되묻기까지 갈 수 있다.
    return any(s in query for s in known_systems) or bool(SYSTEM_LIKE.search(query))


def should_force_incident(query: str, known_systems: set[str],
                          history: list[dict] | None = None) -> bool:
    """모델이 chat이라고 했어도 incident로 바꿔야 하는 질문인가.

    '시스템 이름 + 운영 키워드'가 함께 있을 때만 True — 인사·잡담이 incident로
    잘못 넘어가지 않도록 두 조건을 모두 요구한다. 시스템 이름 대신 "그 시스템" 같은
    지시어가 있고 이전 대화에 시스템이 있으면 후속 질문으로 본다."""
    # ⓪ [C15] 직전 답이 "어느 시스템인지" 되묻기였고, 지금 입력에 등록 시스템 이름이 있으면
    #    그 되묻기에 대한 답이다. 운영 키워드가 없어도("web-01"만 입력) 장애 질문으로 이어간다.
    #    Before: 아래 ①에서 걸려 chat으로 분류 → 조회 없이 "web-01"을 그대로 따라 말함
    #    예: (직전 답: "어느 시스템을 확인할지 알려 주세요…") "web-01" → True
    if _last_reply_was_clarify(history) and any(s in query for s in known_systems):
        return True
    # ① 운영 키워드가 하나도 없으면 인사·잡담이다 → 모델 판단(chat)을 그대로 둔다.
    #    예: "안녕하세요" → False
    if not OPS_KEYWORDS.search(query):
        return False
    # ② 키워드가 있고, 질문에 시스템 이름도 있으면 운영 질문이다 → incident로 바꾼다.
    #    예: "web-01 상태 진단해줘"(상태+web-01) → True  (개선 전 B1: 모델이 chat으로 분류)
    if mentions_system(query, known_systems):
        return True
    # ③ 시스템 이름은 없지만 "그 시스템" 같은 지시어가 있고, 이전 대화에 시스템이 있으면
    #    후속 질문으로 본다. 예: (앞 질문: payments-service) "그 시스템 메모리는 어때?" → True
    #    이전 대화에 시스템이 없으면 False (되묻기는 plan 단계에서 처리).
    return bool(DEICTIC.search(query)) and \
        last_system_from_history(history, known_systems) is not None


# format_clarify()가 만드는 되묻기 문장의 공통 부분. 그 문장을 바꾸면 이 값도 같이 바꿔야 한다.
_CLARIFY_MARKERS = ("확인할 시스템을 알려 주세요", "어느 시스템을 확인할지 알려 주세요")


def _last_reply_was_clarify(history: list[dict] | None) -> bool:
    """대화 기록의 마지막 메시지가 에이전트의 '어느 시스템인가' 되묻기인가."""
    if not history:
        return False
    last = history[-1]
    return last.get("role") == "assistant" and \
        any(m in last.get("content", "") for m in _CLARIFY_MARKERS)


def continue_clarified_query(query: str, history: list[dict] | None,
                             known_systems: set[str]) -> str:
    """시스템만 답한 경우, 직전 되묻기의 원래 증상을 이어받는다."""
    if query not in known_systems or not history or len(history) < 2 or \
            not _last_reply_was_clarify(history):
        return query
    previous = history[-2]
    symptom = previous.get("content", "")
    if previous.get("role") != "user" or mentions_system(symptom, known_systems):
        return query
    return f"{query} {symptom}"


def decide_needs_analysis(model_opinion: bool, targets: list[str]) -> bool:
    """[C12] 비교 분석(analysis) 단계를 실행할지 '코드'가 결정한다.

    Before: 소형 모델이 내놓은 needs_analysis 값을 그대로 믿었다.
            조사 대상이 시스템 1개뿐이어도 모델이 true라고 하면 분석 LLM을 한 번 더 호출했다
            (비교할 대상이 없는데도 시간·비용만 소모).
    After : 비교는 '대상이 2개 이상'일 때만 의미가 있으므로, 모델이 true라고 해도
            대상이 1개면 false로 바꾼다. (모델이 false라고 하면 그대로 false)
    판단 기준이 '시스템 개수'라는 확인 가능한 사실이라 코드가 게이트가 된다.
    """
    return bool(model_opinion) and len(targets) >= 2
