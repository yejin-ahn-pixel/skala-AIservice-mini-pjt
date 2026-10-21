"""targets.py 단위 테스트 — 조사 대상 결정 규칙(B2·B3·B6·B7 회귀 방지)."""
from agent.targets import (apply_targets, find_unregistered, format_clarify,
                           format_notices, resolve_targets)

KNOWN = {"auth-service", "web-01", "payments-service"}


def test_all_mentioned_systems_are_targets_in_order():            # B6
    res = resolve_targets("web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘", [], KNOWN)
    assert res.targets == ["web-01", "auth-service"]
    assert res.source == "query"


def test_unregistered_system_is_not_substituted():                # B2
    res = resolve_targets("billing-service 응답 지연을 진단해주세요", [], KNOWN)
    assert res.targets == []
    assert res.unregistered == ["billing-service"]


def test_mixed_registered_and_unregistered():
    res = resolve_targets("billing-service랑 web-01 상태 확인해줘", [], KNOWN)
    assert res.targets == ["web-01"]
    assert res.unregistered == ["billing-service"]


def test_no_system_and_no_history_asks_back():                    # B3
    res = resolve_targets("로그인이 너무 느려요", [], KNOWN)
    assert res.targets == [] and res.unregistered == [] and res.source == "none"


def test_follow_up_uses_last_user_mentioned_system():             # B7
    history = [
        {"role": "user", "content": "payments-service 결제 응답 지연을 진단해주세요"},
        {"role": "assistant", "content": "auth-service 수치를 참고하면 ..."},  # 답변은 무시
    ]
    res = resolve_targets("그 시스템 메모리는 어때?", history, KNOWN)
    assert res.targets == ["payments-service"]
    assert res.source == "history"


def test_instance_name_is_not_flagged_as_unregistered():
    assert find_unregistered("payments-service-3 instance가 느려요", KNOWN) == []


def test_apply_targets_adds_missing_and_removes_substituted():
    model_tasks = [
        {"id": "task-0", "agent": "research", "kind": "monitoring",
         "target": "auth-service", "status": "pending"},          # 모델이 대체한 시스템
        {"id": "task-analysis", "agent": "analysis",
         "kind": "pattern-analysis", "status": "pending"},
    ]
    out = apply_targets(model_tasks, ["web-01", "auth-service"])
    assert [t["target"] for t in out if t["agent"] == "research"] == ["web-01", "auth-service"]
    assert [t["id"] for t in out if t["agent"] == "research"] == ["task-0", "task-1"]
    assert any(t["agent"] == "analysis" for t in out)               # analysis는 보존


def test_apply_targets_drops_unmentioned_system():                # 묻지 않은 web-01 조사 방지
    model_tasks = [{"id": "task-0", "agent": "research", "kind": "monitoring",
                    "target": "payments-service", "status": "pending"},
                   {"id": "task-1", "agent": "research", "kind": "monitoring",
                    "target": "web-01", "status": "pending"}]
    out = apply_targets(model_tasks, ["payments-service"])
    assert [t["target"] for t in out] == ["payments-service"]


def test_messages():
    assert "billing-service" in format_clarify(["billing-service"], KNOWN)
    assert "조회할 수 없습니다" in format_clarify(["billing-service"], KNOWN)
    assert "어느 시스템" in format_clarify([], KNOWN)
    assert "billing-service" in format_notices(["billing-service"], "query", ["web-01"])
    assert "이전 대화" in format_notices([], "history", ["payments-service"])
    assert format_notices([], "query", ["web-01"]) == ""
