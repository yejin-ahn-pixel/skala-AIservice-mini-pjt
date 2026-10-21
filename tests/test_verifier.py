"""verifier.py 단위 테스트 — B4(근거 없는 서술)·B6(조사한 시스템이 답변에 없음) 회귀 방지."""
from agent.verifier import build_feedback, find_issues

CPU_RUNBOOK = ("# Runbook: CPU 급증 대응 절차\n"
               "1. CPU 사용률과 최근 배포/트래픽 변화 시점을 확인한다.\n"
               "2. 반복 호출, 배치 작업 중복 실행 같은 패턴이 원인인지 확인한다.\n"
               "3. CPU가 90%를 넘고 지속되면 인스턴스를 한 대 더 띄운다.")


def _mon(name="payments-service", cpu=97.3, mem=58.1, alerts=("CPU 91% (threshold 80%)",
                                                              "payments-service-3 instance responding slow")):
    return {"source": "monitoring",
            "data": {"system_name": name, "status": "degraded", "cpu_percent": cpu,
                     "memory_percent": mem, "alerts": list(alerts)}}


COLLECTED = [_mon(), {"source": "runbook", "data": CPU_RUNBOOK}]
QUERY = "payments-service 결제 응답 지연을 진단해주세요"


def test_grounded_draft_has_no_issues():
    draft = ("1) payments-service CPU 97.3%, 메모리 58.1%, 알림 'CPU 91% (threshold 80%)'. "
             "2) CPU 90%를 넘고 지속되면 인스턴스를 추가하세요. 3) payments-service-3 확인.")
    assert find_issues(draft, QUERY, COLLECTED) == []


def test_rounded_percent_is_allowed():
    assert find_issues("1) payments-service CPU 97%.", QUERY, COLLECTED) == []


def test_number_not_in_facts_is_flagged():
    issues = find_issues("1) payments-service CPU 85.5%.", QUERY, COLLECTED)
    assert any("85.5%" in i for i in issues)


def test_runbook_instruction_turned_into_finding_is_flagged():             # B4
    draft = ("1) payments-service CPU 97.3%. 2) 원인 분석: 최근 배포/트래픽 변화 없음. "
             "반복 호출 또는 배치 작업 패턴 없음. 3) 인스턴스를 추가하세요.")
    issues = find_issues(draft, QUERY, COLLECTED)
    assert any("배포" in i and "트래픽" in i and "배치" in i for i in issues)


def test_honest_disclosure_is_allowed():
    draft = "1) payments-service CPU 97.3%. 2) 수집된 데이터에는 배포 정보가 없습니다."
    assert find_issues(draft, QUERY, COLLECTED) == []


def test_recommendation_is_not_a_finding():
    draft = "1) payments-service CPU 97.3%. 2) 직전 배포를 롤백하고 원인을 분석하세요."
    assert find_issues(draft, QUERY, COLLECTED) == []


def test_claim_supported_by_monitoring_is_allowed():
    collected = [_mon(alerts=("배포 직후 CPU 급증",))]
    assert find_issues("1) payments-service 배포 직후 CPU 급증이 확인됨.", QUERY, collected) == []


def test_system_not_investigated_is_flagged():
    issues = find_issues("1) payments-service CPU 97.3%. auth-service도 확인 필요.",
                         QUERY, COLLECTED)
    assert any("auth-service" in i for i in issues)


def test_system_from_query_is_allowed():
    q = "billing-service랑 payments-service 확인해줘"
    assert find_issues("1) payments-service CPU 97.3%. billing-service는 조회 불가.",
                       q, COLLECTED) == []


def test_investigated_system_missing_from_draft_is_flagged():              # B6
    collected = [_mon("web-01", 30.0, 40.0, ()), _mon("auth-service", 81.0, 62.5, ())]
    issues = find_issues("1) auth-service CPU 81.0%, 메모리 62.5%.", "q", collected)
    assert issues == ["조사한 시스템 web-01에 대한 내용이 답변에 없음"]


def test_feedback_lists_issues_and_format():
    fb = build_feedback("짧음", False, ["수집된 데이터에 없는 수치: 85.5%"])
    assert "85.5%" in fb and "번호" in fb and "확인되지 않음" in fb


# ---- 재현 테스트: 개선 전 실행(B4, B6)에서 실제로 나온 답변을 그대로 검증기에 넣는다 ----
from pathlib import Path

RUNBOOKS = Path(__file__).resolve().parent.parent / "data" / "runbooks"

B4_BEFORE_ANSWER = (
    "1) **모니터링 상태 확인**: payments-service의 CPU 사용률 97.3% 및 메모리 58.1%로 상태가 "
    "degraded 중이며, 'CPU 91% (threshold 80%)' 및 'payments-service-3 instance responding slow' "
    "알림 발생. 2) **원인 분석**: 최근 배포/트래픽 변화 없음. 반복 호출 또는 배치 작업 패턴 없음. "
    "CPU 급증 원인으로 인스턴스 부하 분산 필요. 3) **즉시대응**: CPU 90% 이상 지속 시 "
    "오토스케일링으로 인스턴스 1대 추가하여 부하 분산. 정상화 후 프로파일링통해 핫스팟 코드 "
    "식별 및 최적화 작업 등록.")

B6_BEFORE_ANSWER = (
    "auth-service 진단: 1) **커넥션 풀 사용률 92% 초과** → 풀 크기 확장 또는 느린 쿼리/외부 호출 타임아웃 적용 "
    "(runbook 2단계) 2) **p95 지연 2.4s (임계치 1.0s 초과)** → 5분 내 개선되지 않으면 캐시 "
    "계층 또는 읽기 복제본으로 트래픽 분산 (runbook 4단계)")


def test_real_b4_before_answer_is_flagged():
    """개선 전 B4 실제 답변: 런북의 '확인한다'가 '배포/트래픽 변화 없음'이라는 결과로 바뀌어 통과했었다."""
    collected = [_mon(), {"source": "runbook", "data": (RUNBOOKS / "cpu-spike.md").read_text()}]
    issues = find_issues(B4_BEFORE_ANSWER, QUERY, collected)
    assert issues == ["모니터링 데이터에 근거 없는 조사 결과 서술: 배포, 트래픽, 배치"]


def test_real_b6_before_answer_is_flagged():
    """개선 전 B6류 답변(재구성): web-01을 조사했지만 답변에는 auth-service만 있다."""
    collected = [_mon("web-01", 36.7, 49.4, ()),
                 _mon("auth-service", 81.0, 62.5,
                      ("p95 latency 2.4s (threshold 1.0s)", "connection pool 92% used")),
                 {"source": "runbook", "data": (RUNBOOKS / "high-latency.md").read_text()}]
    issues = find_issues(B6_BEFORE_ANSWER, "web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘",
                         collected)
    assert issues == ["조사한 시스템 web-01에 대한 내용이 답변에 없음"]
