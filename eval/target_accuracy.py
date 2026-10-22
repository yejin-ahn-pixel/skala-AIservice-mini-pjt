"""조사 대상 결정 정확도 평가 — 모델·네트워크 없이 규칙만으로 개선 전/후를 같은 질문으로 비교한다.

실행: python eval/target_accuracy.py        (프로젝트 폴더에서)

무엇을 재나: "질문이 가리키는 시스템과, 에이전트가 조사하기로 한 시스템이 같은가".
  - 정답(expected): 조사해야 할 시스템 목록. 빈 목록 []은 '가정하지 않고 되묻기/조회 불가'가 정답.
  - 개선 후: agent.targets.resolve_targets 를 그대로 호출.
  - 개선 전: 원본 코드(all_total_vue_student.zip)의 대상 결정 규칙을 그대로 옮긴 시뮬레이션.
    원본은 모델이 시스템마다 research task를 만든다고 가정하고(= 모델이 이상적으로 답하는 최선의 경우),
    그 뒤 코드의 보정(_guess_target: 못 찾으면 auth-service)과 프롬프트의 기본값(auth-service)을 적용한다.
    이전 대화 기록은 plan()에 전달되지 않았으므로 쓰지 않는다.
  → 개선 전 점수는 '모델이 아무리 잘해도 코드 규칙 때문에 틀리는' 하한선이다. 실제 모델은 이보다 더 틀렸다(B1~B7).
한계: 질문 34건은 사람이 쓴 것이고 정답 기준("지정 안 한 시스템은 가정하지 않는다")은 설계 선택이다.
      라우팅(chat/incident 분류)과 모델 호출은 이 평가의 대상이 아니다.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.targets import SYSTEM_LIKE, load_known_systems, resolve_targets  # noqa: E402

KNOWN = load_known_systems()

H_PAY = [{"role": "user", "content": "payments-service 결제 응답 지연을 진단해주세요"},
         {"role": "assistant", "content": "payments-service는 CPU 96.8%입니다."}]
H_TWO = [{"role": "user", "content": "web-01 CPU가 높아요"},
         {"role": "assistant", "content": "web-01 CPU 34%입니다."},
         {"role": "user", "content": "auth-service 지연도 봐주세요"},
         {"role": "assistant", "content": "auth-service는 정상입니다."}]
H_ASSIST_ONLY = [{"role": "user", "content": "서버가 느려요"},
                 {"role": "assistant", "content": "어느 시스템인지 알려 주세요. web-01, auth-service 등이 있습니다."}]

# (분류, 질문, 이전 대화, 정답 조사 대상)
CASES = [
    # A. 등록 시스템 1개
    ("A 등록 1개", "web-01 상태 진단해줘", None, ["web-01"]),
    ("A 등록 1개", "payments-service CPU 급증 원인 확인해줘", None, ["payments-service"]),
    ("A 등록 1개", "auth-service 로그인 지연을 봐주세요", None, ["auth-service"]),
    ("A 등록 1개", "cache-service 메모리 사용량이 이상해요", None, ["cache-service"]),
    ("A 등록 1개", "gateway-service 에러율이 높아요", None, ["gateway-service"]),
    ("A 등록 1개", "log-service 디스크가 부족한 것 같아요", None, ["log-service"]),
    ("A 등록 1개", "web-01이 느려요", None, ["web-01"]),
    ("A 등록 1개", "payments-service-3 인스턴스가 느리다는 알림이 떴어요", None, ["payments-service"]),
    # B. 등록 시스템 2개 이상
    ("B 등록 여러 개", "web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘", None, ["web-01", "auth-service"]),
    ("B 등록 여러 개", "payments-service와 gateway-service 응답시간을 비교해줘", None, ["payments-service", "gateway-service"]),
    ("B 등록 여러 개", "cache-service, log-service 상태 확인해줘", None, ["cache-service", "log-service"]),
    ("B 등록 여러 개", "auth-service랑 web-01이랑 cache-service 모두 점검해줘", None, ["auth-service", "web-01", "cache-service"]),
    ("B 등록 여러 개", "gateway-service 502 에러, web-01도 같이 확인", None, ["gateway-service", "web-01"]),
    # C. 미등록 시스템 (조회 불가여야 함. 등록 시스템이 같이 있으면 그것만)
    ("C 미등록", "billing-service 응답 지연을 진단해주세요", None, []),
    ("C 미등록", "order-service 에러가 늘었어요", None, []),
    ("C 미등록", "db-01 디스크가 꽉 찼어요", None, []),
    ("C 미등록", "web-02 상태를 알려주세요", None, []),
    ("C 미등록", "billing-service와 auth-service를 같이 확인해줘", None, ["auth-service"]),
    ("C 미등록", "search-service가 느린데 cache-service 때문일까요", None, ["cache-service"]),
    # D. 시스템 미언급 (가정하지 않고 되묻기)
    ("D 미언급", "로그인이 너무 느려요", None, []),
    ("D 미언급", "서버가 느려요", None, []),
    ("D 미언급", "CPU가 치솟고 있어요", None, []),
    ("D 미언급", "장애가 난 것 같아요", None, []),
    ("D 미언급", "결제 응답이 느려졌어요", None, []),
    ("D 미언급", "메모리가 계속 올라가요", None, []),
    # E. 이전 대화를 가리키는 후속 질문
    ("E 후속 질문", "그 시스템 메모리는 어때?", H_PAY, ["payments-service"]),
    ("E 후속 질문", "아까 그 서버 디스크는?", H_PAY, ["payments-service"]),
    ("E 후속 질문", "거기 로그도 봐줘", H_PAY, ["payments-service"]),
    ("E 후속 질문", "그 서비스 CPU도 확인해줘", H_TWO, ["auth-service"]),
    ("E 후속 질문", "그 시스템 메모리는 어때?", None, []),
    ("E 후속 질문", "그 시스템 메모리는 어때?", H_ASSIST_ONLY, []),
    # F. 알려진 한계 (정답은 사람이 보기엔 명확하지만 규칙이 못 잡는 표현)
    ("F 한계 사례", "인증 서비스가 느려요", None, ["auth-service"]),
    ("F 한계 사례", "payment-service 응답이 느려요", None, ["payments-service"]),
    ("F 한계 사례", "WEB-01 상태 진단해줘", None, ["web-01"]),
]


def before(query: str) -> list[str]:
    """원본 코드의 대상 결정(모델이 이상적으로 시스템 이름을 그대로 뽑는다고 가정)."""
    named = list(dict.fromkeys(SYSTEM_LIKE.findall(query)))        # 질문에 나온 시스템 이름처럼 생긴 토큰
    targets: list[str] = []
    for tok in named:
        if tok in KNOWN:
            tgt = tok
        else:   # _guess_target: 등록 이름이 질문에 있으면 첫 것, 아니면 auth-service
            hits = sorted((query.index(s), s) for s in KNOWN if s in query)
            tgt = hits[0][1] if hits else "auth-service"
        if tgt not in targets:
            targets.append(tgt)
    if not targets:                  # 프롬프트: "언급되지 않았으면 auth-service를 기본으로 조사하라"
        targets = ["auth-service"]
    return targets


def after(query: str, history) -> list[str]:
    return resolve_targets(query, history, KNOWN).targets


def main() -> None:
    cats: dict[str, list[int]] = {}
    fails_before, fails_after = [], []
    for cat, q, hist, exp in CASES:
        b, a = before(q), after(q, hist)
        okb, oka = sorted(b) == sorted(exp), sorted(a) == sorted(exp)
        c = cats.setdefault(cat, [0, 0, 0])
        c[0] += 1; c[1] += okb; c[2] += oka
        if not okb: fails_before.append((q, exp, b))
        if not oka: fails_after.append((q, exp, a))
    n = len(CASES)
    tb, ta = sum(c[1] for c in cats.values()), sum(c[2] for c in cats.values())
    print(f"조사 대상 결정 정확도 (질문 {n}건, 모델 호출 없음)\n")
    print("| 분류 | 건수 | 개선 전 | 개선 후 |\n|---|---|---|---|")
    for cat, (cnt, b, a) in cats.items():
        print(f"| {cat} | {cnt} | {b}/{cnt} | {a}/{cnt} |")
    print(f"| **전체** | {n} | **{tb}/{n} ({tb/n:.0%})** | **{ta}/{n} ({ta/n:.0%})** |\n")
    print("개선 후에도 틀린 질문 (정답 → 실제):")
    for q, exp, got in fails_after:
        print(f"  - {q!r}: {exp} → {got}")
    print("\n개선 전에 틀린 질문 수:", len(fails_before))


if __name__ == "__main__":
    main()
