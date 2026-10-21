"""답변 검증 — 초안이 수집된 사실(모니터링·런북·분석)에만 근거하는지 코드로 검사한다.

개선 전 validate_node는 "30자 이상 + 1) 포함"만 봤다. 그래서 실측(B4)에서
  - "최근 배포/트래픽 변화 없음. 배치 작업 패턴 없음" — 런북의 "확인한다"는 지시문을
    모델이 "없음"이라는 조사 결과로 바꿔 쓴 문장이 그대로 통과했고,
  - 실측(B6)에서는 조사한 web-01이 답변에 한 번도 나오지 않았다.
이 모듈은 표준 라이브러리만 쓰는 순수 함수라 모델 없이 단위 테스트할 수 있다.

한계: 규칙·사전 기반이라 목록에 없는 표현의 지어내기는 못 잡는다(LLM-as-judge는 큰 모델을
한 번 더 불러 지연·비용이 늘어 쓰지 않음).
"""
import re

SYSTEM_LIKE = re.compile(r"[a-z][a-z0-9-]+-(?:service|\d+)")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
# 단위가 붙은 수치만 검사한다: 55.1%, 2.4s (단계 번호 "1)"·"5분" 등은 대상이 아님)
UNIT_NUMBER = re.compile(r"(\d+(?:\.\d+)?)\s*(%|s\b)")
PERCENT_TOLERANCE = 1.0     # 97.3% 를 97%로 반올림해 쓰는 것은 허용
SECOND_TOLERANCE = 0.05

# 모니터링 데이터에 없으면 모델이 지어내기 쉬운 '원인 후보' 단어
CLAIM_WORDS = ("배포", "트래픽", "배치", "롤백", "방화벽", "DNS", "설정 변경")
# 그 단어와 함께 "조사 결과를 단정"하는 표현
FINDING_MARKERS = ("없음", "없습니다", "없었", "아님", "아닙니다", "확인됨", "확인되었",
                   "발생했", "있었", "있습니다", "증가했", "원인입니다")
# "수집된 데이터에 없다"고 솔직히 밝히는 문장은 정상으로 본다
DISCLOSURE_MARKERS = ("수집된", "데이터에", "명시", "제공", "확인되지", "알 수 없",
                      "확인할 수 없", "정보가")


def _monitoring_text(collected: list[dict]) -> str:
    """모니터링(+분석) 결과만 한 문자열로 합친다. '원인 후보 단어'가 실제 데이터에 있는지 볼 때 쓴다."""
    parts = []
    for c in collected:
        d = c.get("data")
        if c.get("source") == "monitoring" and isinstance(d, dict):
            parts.append(f"{d.get('system_name', '')} {d.get('status', '')} "
                         f"{d.get('cpu_percent', '')} {d.get('memory_percent', '')} "
                         + " ".join(d.get("alerts", [])))
        elif c.get("source") == "analysis":
            parts.append(str(d))
    return " ".join(parts)


def _facts_text(collected: list[dict]) -> str:
    """수집된 모든 사실 (모니터링 + 런북 + 분석) — 수치·시스템명 대조용."""
    runbooks = " ".join(str(c.get("data", "")) for c in collected
                        if c.get("source") == "runbook")
    return f"{_monitoring_text(collected)} {runbooks}"


def investigated_systems(collected: list[dict]) -> list[str]:
    """실제로 조회에 성공한 시스템 이름들 (조회 실패한 시스템은 포함되지 않는다)."""
    return [c["data"]["system_name"] for c in collected
            if c.get("source") == "monitoring" and isinstance(c.get("data"), dict)
            and c["data"].get("system_name")]


def _unsupported_numbers(draft: str, facts: str) -> list[str]:
    # 수집된 사실에 등장하는 모든 숫자를 모아 둔다 (예: 97.3, 58.1, 91, 80 ...)
    fact_numbers = [float(n) for n in NUMBER.findall(facts)]
    bad = []
    # 초안에서 "단위가 붙은 수치"(%·s)만 꺼내 하나씩 대조한다. 예: "CPU 99%" → 사실에 99가 없으면 위반
    for value, unit in UNIT_NUMBER.findall(draft):
        tol = PERCENT_TOLERANCE if unit == "%" else SECOND_TOLERANCE   # 반올림 오차 허용 범위
        if not any(abs(float(value) - f) <= tol for f in fact_numbers):
            bad.append(f"{value}{unit}")
    return list(dict.fromkeys(bad))


def _unsupported_systems(draft: str, query: str, facts: str) -> list[str]:
    # 질문이나 수집 데이터에 나온 이름만 허용한다. 예: 답변에 "cache-service"가 있는데
    # 조사한 적도 질문에 나온 적도 없으면 "조사하지 않은 시스템 언급"
    allowed = (query + " " + facts).lower()
    return list(dict.fromkeys(t for t in SYSTEM_LIKE.findall(draft.lower())
                              if t not in allowed))


def _unsupported_claims(draft: str, monitoring: str) -> list[str]:
    bad = []
    # 문장 단위로 검사한다 (마침표·줄바꿈으로 분리)
    for sentence in re.split(r"[.\n]", draft):
        # ① "~없음/~확인됨" 같이 조사 결과를 단정하는 문장만 대상
        if not any(m in sentence for m in FINDING_MARKERS):
            continue
        # ② "수집된 데이터에 없음"처럼 솔직히 밝히는 문장은 정상 → 통과
        if any(m in sentence for m in DISCLOSURE_MARKERS):
            continue
        # ③ 원인 후보 단어(배포·트래픽 등)가 있는데 모니터링 데이터에는 없으면 지어낸 것
        #    예(B4): "최근 배포/트래픽 변화 없음" → 배포·트래픽 모두 데이터에 없음 → 위반
        for word in CLAIM_WORDS:
            if word in sentence and word not in monitoring:
                bad.append(word)
    return list(dict.fromkeys(bad))


def find_issues(draft: str, query: str, collected: list[dict]) -> list[str]:
    """초안이 수집된 사실에 근거하지 않는 부분을 사람이 읽을 문장 목록으로 반환."""
    facts, monitoring = _facts_text(collected), _monitoring_text(collected)
    issues = []
    # 네 가지 검사를 차례로 수행한다. 하나라도 걸리면 issues에 사유가 쌓이고,
    # validate_node가 이를 근거로 write_node에 재작성을 요청한다(최대 3회).
    # ① 수치  ② 시스템명  ③ 조사한 시스템 누락  ④ 근거 없는 원인 서술
    if nums := _unsupported_numbers(draft, facts):
        issues.append(f"수집된 데이터에 없는 수치: {', '.join(nums)}")
    if names := _unsupported_systems(draft, query, facts):
        issues.append(f"조사하지 않은 시스템 언급: {', '.join(names)}")
    for system in investigated_systems(collected):
        if system.lower() not in draft.lower():
            issues.append(f"조사한 시스템 {system}에 대한 내용이 답변에 없음")
    if claims := _unsupported_claims(draft, monitoring):
        issues.append(f"모니터링 데이터에 근거 없는 조사 결과 서술: {', '.join(claims)}")
    return issues


def build_feedback(draft: str, format_ok: bool, issues: list[str]) -> str:
    """write_node에 돌려줄 재작성 지시문."""
    lines = []
    if not format_ok:
        lines.append(f"응답이 너무 짧거나 번호 절차가 없습니다({len(draft)}자, 최소 30자). "
                     "절차를 1) 2) 3) 번호로 구체적으로 설명하세요.")
    if issues:
        lines.append("다음은 수집된 사실에 근거가 없습니다: " + "; ".join(issues) +
                     ". 수집된 사실만 사용하고, 확인하지 못한 내용은 '확인되지 않음'이라고 쓰세요.")
    return " ".join(lines)
