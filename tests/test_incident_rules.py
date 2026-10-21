"""incident_rules.py 단위 테스트 — 런북 선택 (B5: 정상 시스템에 db-down 폴백)."""
import pytest

from agent.incident_rules import classify_incident_type


def _mon(status="healthy", alerts=()):
    return [{"source": "monitoring",
             "data": {"system_name": "x", "status": status, "alerts": list(alerts)}}]


def test_healthy_without_alerts_returns_none():                          # B5
    assert classify_incident_type(_mon()) is None


def test_no_collected_data_returns_none():
    assert classify_incident_type([]) is None


@pytest.mark.parametrize("alerts,expected", [
    (["CPU 91% (threshold 80%)"], "cpu-spike"),
    (["memory usage climbing steadily over 6h", "heap near limit"], "memory-leak"),
    (["p95 latency 2.4s (threshold 1.0s)", "connection pool 92% used"], "high-latency"),
    (["upstream timeout to payments-service (504)"], "network-timeout"),
    (["disk usage 95% on /var/log"], "disk-full"),
])
def test_alert_keywords_select_runbook(alerts, expected):
    assert classify_incident_type(_mon("degraded", alerts)) == expected


def test_down_status_selects_db_down():
    assert classify_incident_type(_mon("down")) == "db-down"


def test_unrelated_alert_returns_none_instead_of_fallback():
    assert classify_incident_type(_mon("degraded", ["certificate expires soon"])) is None
