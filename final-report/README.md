# 과제 1 최종 보고서 자료 — Base AI Agent 개선 (단계별 코드 학습 + 수동 확인 가이드)

이 폴더는 **개선을 단계별로 직접 확인하면서 코드를 이해**할 수 있도록 만든 자료다.
각 단계 문서는 같은 순서로 쓰여 있다: **문제 → 바꾼 파일과 이유 → Before 코드 → After 코드 → 동작 원리 → 직접 확인하는 방법 → 테스트 → 캡처 목록 → 한계**.
Before 코드는 원본(`all_total_vue_student.zip`)에서, After 코드는 현재 소스에서 그대로 발췌했다.

## 읽는 순서

| 문서 | 내용 | 변경 기록 |
|---|---|---|
| [00_개요_문제15개_상태표](00_개요_문제15개_상태표.md) | 기존 코드 문제점 15개와 현재 해결 상태 | - |
| [01_라우팅_보강](01_라우팅_보강.md) | 운영 질문이 chat으로 빠지는 문제 | C1, C5 |
| [02_조사대상_결정](02_조사대상_결정.md) | 대체·가정·누락·맥락 상실 제거 | C2, C3, C4 |
| [03_런북선택](03_런북선택.md) | 근거 없을 때 `db-down` 폴백 제거 | C6 |
| [04_근거검증](04_근거검증.md) | 형식만 보던 검증을 사실 대조로 | C7 |
| [05_모듈화](05_모듈화.md) | graph.py 분리, 중복 제거 | C8, C9 |
| [06_부분실패격리](06_부분실패격리.md) | 작업 하나 실패해도 나머지로 진행 + MCP 제한시간 | C10 |
| [07_비동기_타임아웃](07_비동기_타임아웃.md) | 동기 LLM 호출 분리, timeout·thinking 제한 | C11 |
| [08_분석단계_코드결정](08_분석단계_코드결정.md) | 불필요한 분석 호출 제거 | C12 |
| [09_SSE오류_입력검증_시간로그](09_SSE오류_입력검증_시간로그.md) | 오류 전달, 입력 검증, 노드 시간 기록 | C13 |
| [10_전후비교_캡처목록](10_전후비교_캡처목록.md) | B1~B7 전후 비교표, 캡처 체크리스트, 한계·트레이드오프 | - |

## 공통 수동 확인 절차

```bash
cd all_total_vue_student

# 터미널 A — 서버 (코드를 바꾼 뒤에는 Ctrl+C 후 다시 실행)
bash sh/run_agent_ollma.sh

# 터미널 B — 질문 / 테스트
time .venv/bin/python3 ask.py "payments-service 결제 응답 지연을 진단해주세요"
.venv/bin/python3 -m pytest -q tests            # 전체 단위 테스트
.venv/bin/python3 -m pytest -q tests -v         # 테스트 이름까지 보기
```

- **실모델은 답변이 매번 다르다.** 같은 질문을 2회 이상 돌려 보고, 한 번의 결과로 개선 효과를 단정하지 않는다.
- 지연시간은 `qwen3:8b`가 메모리에 올라간 상태에서 비교한다. 재시작 직후 첫 요청은 모델 로딩 때문에 느리다(미리 `ollama run qwen3:8b "ok" --keepalive 30m`).
- `sh/run_agent_ollma.sh`는 시작할 때 `session.db`를 지우고 8100 포트의 프로세스를 종료한다(알려진 위험, `과제1_개선항목_탐색.md`).
- 서버 포트는 **8100**이다.

## 원본 코드로 Before 재현하기 (선택)

개선 전 동작을 직접 보고 싶을 때. 원본은 프로젝트 폴더의 `all_total_vue_student.zip`이다.

```bash
PROJ="<프로젝트 폴더>/all_total_vue_student"          # 현재(개선된) 프로젝트 경로
mkdir -p ~/Desktop/orig_check && cd ~/Desktop/orig_check
unzip -o "<프로젝트 폴더>/all_total_vue_student.zip"
cp "$PROJ/.env" all_total_vue_student/.env
# 원본 실행이 LangSmith의 After 기록과 섞이지 않게 추적을 끈다
sed -i '' 's/^LANGCHAIN_TRACING_V2=.*/LANGCHAIN_TRACING_V2=false/' all_total_vue_student/.env
cd all_total_vue_student
# 현재 서버(터미널 A)를 Ctrl+C로 끈 뒤 — 같은 .venv의 파이썬으로 원본 서버를 실행
PATH="$PROJ/.venv/bin:$PATH" bash sh/run_agent_ollma.sh
```
질문은 현재 프로젝트의 `ask.py`로 보낸다(같은 8100 포트). 끝나면 원본 서버를 Ctrl+C로 끄고 현재 서버를 다시 켠다.
**주의**: `.env`가 복사되므로 `~/Desktop/orig_check`는 GitHub에 올리지 않는다.

## 코드 차이만 보기

```bash
# 원본 vs 현재 (파일 하나)
diff <(unzip -p ../all_total_vue_student.zip all_total_vue_student/agent/graph.py) agent/graph.py
# 개선 중 신규 파일 목록
ls agent agent/nodes tests
```

## 파일 구조 (최종)

```
agent/
├── graph.py            # 분기 함수 + build_graph (배선만)
├── state.py            # AgentState
├── streaming.py        # 토큰 스트리밍 writer 접근 지점
├── timing.py           # 노드별 실행 시간 기록 (C13)
├── routing.py          # 운영 질문 보정, 분석 실행 여부 규칙 (C1, C5, C12)
├── targets.py          # 조사 대상 결정 (C2~C4)
├── incident_rules.py   # 런북 선택 규칙 (C6)
├── verifier.py         # 답변 근거 검증 (C7)
└── nodes/
    ├── routing_nodes.py   # route, chat_answer, plan, clarify, gate
    ├── research_nodes.py  # research, replan_check, analysis
    └── answer_nodes.py    # write, validate, finalize
tests/                  # pytest 71개 (모델·MCP 없이 실행)
```
