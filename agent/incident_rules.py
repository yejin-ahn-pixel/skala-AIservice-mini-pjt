"""런북 선택 규칙 — 수집된 모니터링 결과(상태·알림 문구)를 보고 어떤 runbook이 맞는지 고른다.

LLM에게 다시 묻는 대신 코드 레벨 규칙으로 판단한다 — gate(W6)·replan(W7)과 같은 설계
원칙: 실측 데이터로 검증 가능한 결정은 코드가 내린다.

개선 전에는 매칭되는 증상이 없으면 무조건 `db-down`으로 폴백했다. 그래서 정상(healthy,
알림 0건)인 시스템에도 DB 장애 런북이 붙어 답변에 "failover 전환"이 섞였다(실측 B5).
이제는 근거가 없으면 None(런북 없음)을 돌려준다.
"""

# (알림 문구에서 찾을 키워드, 선택할 런북) — 위에서부터 먼저 맞는 규칙이 이긴다.
# 예: 알림 "p95 latency 2.4s", "connection pool 92% used" → "latency"가 먼저 맞아 high-latency
INCIDENT_TYPE_RULES = [
    ("latency", "high-latency"),
    ("pool", "high-latency"),
    ("cpu", "cpu-spike"),
    ("memory", "memory-leak"),
    ("heap", "memory-leak"),
    ("timeout", "network-timeout"),
    ("network", "network-timeout"),
    ("disk", "disk-full"),
    ("down", "db-down"),
]


def classify_incident_type(collected: list[dict]) -> str | None:
    """수집 결과에서 증상 키워드를 찾아 runbook 종류를 반환. 근거가 없으면 None."""
    # 1) monitoring 결과만 골라 "상태 + 알림 문구들"을 한 줄의 소문자 문자열로 합친다.
    #    예: "degraded cpu 91% (threshold 80%) payments-service-3 instance responding slow"
    text = " ".join(
        f"{c['data'].get('status', '')} " + " ".join(c["data"].get("alerts", []))
        for c in collected if c.get("source") == "monitoring"
    ).lower()
    # 2) 규칙을 순서대로 훑어 처음 맞는 키워드의 런북을 돌려준다.
    for keyword, incident_type in INCIDENT_TYPE_RULES:
        if keyword in text:
            return incident_type
    # 3) 하나도 안 맞으면 None. Before: 여기서 "db-down"을 돌려줘 정상 시스템에도
    #    DB 장애 런북이 붙었다. After: "근거 없음"을 그대로 알린다 (replan_check가 런북 조회를 건너뜀).
    return None
