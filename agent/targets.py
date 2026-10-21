"""조사 대상 결정 — "어느 시스템을 조사할지"를 코드가 정한다.

실측(개선 전)에서 이 결정이 모델과 하드코딩 폴백에 맡겨져 네 가지로 어긋났다.
  B2 미등록 시스템(billing-service) → 조용히 auth-service로 대체돼 엉뚱한 수치를 답함
  B3 시스템 미언급 → auth-service로 가정, 답변에 가정했다는 표시 없음
  B6 시스템이 둘인 질문 → 하나만 조사하고 나머지는 누락
  B7 후속 질문("그 시스템 메모리는?") → 이전 대화를 쓰지 않고 auth-service를 조사
gate_node(W6)와 같은 원칙으로, 코드로 검증 가능한 결정은 코드가 내린다.
이 모듈은 표준 라이브러리만 쓰는 순수 함수라 모델·MCP 없이 단위 테스트할 수 있다.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# monitoring.json에 없는 시스템도 '시스템 이름처럼 생긴' 토큰이면 잡는다 (예: billing-service)
# 정규식 읽는 법: 영소문자로 시작 + (영소문자·숫자·-) 반복 + "-service" 또는 "-숫자"로 끝남
#   매칭 예: billing-service, payments-service-3, web-01   / 안 맞는 예: 로그인, db
SYSTEM_LIKE = re.compile(r"[a-z][a-z0-9-]+-(?:service|\d+)")
# "그 시스템", "해당 서버", "아까" 같은 이전 대화를 가리키는 말
DEICTIC = re.compile(r"(?:그|해당|이|저|같은)\s*(?:시스템|서비스|서버)|거기|아까")


def load_known_systems() -> set[str]:
    """등록된 시스템 목록 = data/monitoring.json의 키 (MCP 서버가 쓰는 것과 같은 소스)."""
    path = Path(__file__).resolve().parent.parent / "data" / "monitoring.json"
    try:
        return set(json.loads(path.read_text()).keys())
    except (OSError, json.JSONDecodeError):
        return {"auth-service", "web-01"}


@dataclass(frozen=True)
class TargetResolution:
    targets: list[str] = field(default_factory=list)       # 조사할 등록 시스템
    unregistered: list[str] = field(default_factory=list)  # 질문에 있으나 등록 안 된 이름
    source: str = "none"                                   # "query" | "history" | "none"


def find_known(text: str, known: set[str]) -> list[str]:
    """text에 등장하는 등록 시스템을 처음 등장한 순서대로 반환."""
    # (등장 위치, 이름) 쌍으로 정렬해 질문에 나온 순서를 보존한다.
    # 예: "web-01 CPU 급증, auth-service도 확인" → ["web-01", "auth-service"]
    found = sorted((text.index(s), s) for s in known if s in text)
    return [s for _, s in found]


def find_unregistered(text: str, known: set[str]) -> list[str]:
    """시스템 이름처럼 생겼지만 등록되지 않은 토큰. 등록 시스템의 하위 이름
    (예: payments-service-3)은 등록 시스템으로 보고 제외한다."""
    out: list[str] = []
    for token in SYSTEM_LIKE.findall(text):
        # 등록 이름이거나 그 하위 이름이면 '미등록'이 아니다.
        # 예: "payments-service-3"은 등록된 payments-service의 인스턴스 이름이므로 제외
        if token in known or any(token.startswith(k) for k in known):
            continue
        if token not in out:
            out.append(token)
    return out


def last_system_from_history(history: list[dict] | None, known: set[str]) -> str | None:
    """이전 **사용자** 질문 중 가장 최근에 등록 시스템을 언급한 것.
    assistant 답변은 보지 않는다 — 답변에는 대체·참고로 언급된 다른 시스템이 섞일 수 있다."""
    # 가장 최근 메시지부터 거꾸로 훑는다 (history는 오래된 것 → 최신 순)
    for msg in reversed(history or []):
        if msg.get("role") != "user":   # 답변(assistant)은 건너뜀
            continue
        found = find_known(msg.get("content", ""), known)
        if found:
            return found[0]
    return None


def resolve_targets(query: str, history: list[dict] | None,
                    known: set[str]) -> TargetResolution:
    # 결정 순서(위에서부터 먼저 해당하는 것):
    #   질문에 등록 시스템 있음 → 전부 조사 / 미등록만 있음 → 조회 불가 / 지시어+이력 → 이전 시스템 /
    #   그 외 → 대상 없음(되묻기)
    mentioned = find_known(query, known)
    unregistered = find_unregistered(query, known)
    if mentioned:                       # B6: 언급된 등록 시스템은 전부 조사
        return TargetResolution(mentioned, unregistered, "query")
    if unregistered:                    # B2: 미등록은 대체하지 않고 조회 불가로 처리
        return TargetResolution([], unregistered, "none")
    last = last_system_from_history(history, known)
    if last:                            # B7: 후속 질문은 이전 대화의 시스템 (답변에 표시)
        return TargetResolution([last], [], "history")
    return TargetResolution([], [], "none")   # B3: 가정하지 않고 되묻기


def apply_targets(tasks: list[dict], targets: list[str]) -> list[dict]:
    """모델이 만든 계획의 모니터링 task를 targets와 일치시킨다.
    빠진 시스템은 추가하고, targets에 없는 시스템(대체·하드코딩된 것)은 제거한다.
    research 외 task(analysis 등)는 그대로 둔다."""
    # 1) 모델이 만든 모니터링 task를 대상 이름으로 찾아 둔다 (재사용하려고)
    by_target = {t["target"]: t for t in tasks
                 if t.get("agent") == "research" and t.get("kind") == "monitoring"}
    # 2) targets 순서대로 task를 새로 구성: 모델이 이미 만든 것은 재사용, 빠진 것은 새로 추가.
    #    targets에 없는 대상(예: 모델이 끼워 넣은 auth-service)은 여기서 자연스럽게 사라진다.
    research = []
    for i, target in enumerate(targets):
        task = dict(by_target.get(target) or {"agent": "research", "kind": "monitoring",
                                              "target": target})
        task["id"], task["status"] = f"task-{i}", "pending"
        research.append(task)
    # 3) 모니터링 외 task(예: analysis)는 건드리지 않고 뒤에 붙인다
    others = [t for t in tasks
              if not (t.get("agent") == "research" and t.get("kind") == "monitoring")]
    return research + others


def format_clarify(unregistered: list[str], known: set[str]) -> str:
    """조사할 시스템을 정할 수 없을 때의 응답 (모델 호출 없이 코드로 생성)."""
    systems = ", ".join(sorted(known))
    if unregistered:
        names = ", ".join(unregistered)
        return (f"{names}은(는) 모니터링에 등록되지 않은 시스템이라 상태를 조회할 수 없습니다. "
                f"등록된 시스템: {systems}. 이 중에서 확인할 시스템을 알려 주세요.")
    return f"어느 시스템을 확인할지 알려 주세요. 등록된 시스템: {systems}."


def format_notices(unregistered: list[str], source: str, targets: list[str]) -> str:
    """답변 끝에 붙이는 안내 문구 — 코드가 한 일(가정·제외)을 사용자에게 드러낸다."""
    lines = []
    if unregistered:
        lines.append(f"※ 미등록 시스템(조회 불가): {', '.join(unregistered)}")
    if source == "history" and targets:
        lines.append(f"※ 시스템이 지정되지 않아 이전 대화의 {targets[0]} 기준으로 조회했습니다.")
    return "\n".join(lines)
