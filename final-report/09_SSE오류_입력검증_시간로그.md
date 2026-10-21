# 단계 9 — 스트림 오류 전달 · 입력 검증 · 노드별 시간 기록

> 변경 기록 번호: C13 · 전체 기록은 `과제1_코드변경기록.md` · 목차는 [README](README.md)

## 1. 문제 — 무엇이, 왜, 어떤 영향이 있었나
**현상 ①(SSE 오류, 코드상 확인)**: 스트림 도중 예외가 나면 연결이 그냥 끊기고, 화면(`web_chat.html`)은 `es.onerror`에서 말없이 종료한다 → 사용자에게는 **조용히 멈춘 것**처럼 보인다.
**현상 ②(입력 검증)**: 빈 문자열·공백만 있는 메시지도 그래프(모델 호출)까지 들어간다. 길이 제한이 없다.
**현상 ③(관측)**: 어느 노드가 느린지, 어디서 실패했는지 알려면 LangSmith를 열어야 한다. 서버 로그 설정도 없다.

## 2. 바꾼 파일과 이유
| 파일 | 변경 | 이유 |
|---|---|---|
| `server.py` | 입력 검증(`_clean_message`, 422), 스트림 try/except → `Error` 이벤트 + `[DONE]`, SSE 헤더, `metadata.node_timings` | 오류를 알리고 잘못된 입력은 모델 호출 전에 차단 |
| `agent/timing.py` (신규), `agent/graph.py`, `agent/state.py` | 모든 노드를 `timed_node`로 감싸 로그 + `node_timings` 누적 | 병목 노드를 바로 확인 |
| `web_chat.html` | `Error` 이벤트와 연결 끊김을 화면에 표시 | 조용한 중단 제거 |
| `ask.py` | "느린 노드" 줄 | 캡처용 |
| `tests/test_server.py`, `tests/conftest.py` | 서버 동작 테스트(9개), 테스트 중 LangSmith 전송 차단 | 효과 검증 + 증거 프로젝트 오염 방지 |

## 3. Before 코드 (개선 전 원본)

**원본 `server.py` chat_stream 끝부분**

```python
    async def gen():
        history = deps.session_store.load(thread_id)
        ...
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
```

**원본 `web_chat.html`**

```python
      es.onerror = () => { es.close(); sending.value = false; };
```

## 4. After 코드 (현재)

**현재 `server.py` 178~207줄 (`chat_stream`)**

```python
@app.get("/chat/stream")
async def chat_stream(message: str, thread_id: str = "default"):
    """Wave 3 — 그래프의 노드별 진행 상황을 SSE로 스트리밍.

    이벤트마다 'data: {json}\\n\\n' 형태로 내려보내고 마지막에 data: [DONE] 센티널을
    보낸다 — 프론트엔드는 \\n\\n 경계로 이벤트를 분리해 파싱해야 한다(Wave 3b).
    """
    deps = app.state.deps
    # [C13] 스트림 시작 전에 입력 검증 (Before: 빈 입력도 통과)
    try:
        message = _clean_message(message)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    async def gen():
        # [C13] 스트림 도중 예외가 나면 Before에는 연결이 그냥 끊겨 화면이 조용히 멈췄다.
        # After : 예외를 'Error' 이벤트로 알리고 [DONE]으로 정상 종료한다 (화면이 오류를 표시).
        try:
            async for chunk in _stream_events(deps, message, thread_id):
                yield chunk
        except Exception as exc:   # noqa: BLE001 — 스트림을 깨뜨리지 않고 오류를 전달하는 것이 목적
            logger.exception("chat_stream failed")
            err = {"type": "Error", "error": type(exc).__name__,
                   "message": "처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    # [C13] 프록시/브라우저가 스트림을 모아서 보내지 않도록 헤더 지정 (Before: media_type만 지정)
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
```

**현재 `server.py` 45~56줄 (`_clean_message`)**

```python
def _clean_message(message: str) -> str:
    """[C13] 입력 검증 — 공백만 있는 메시지와 너무 긴 메시지를 거부한다.

    Before: 빈 문자열("")이나 공백만 있는 입력도 그대로 그래프에 들어가 모델을 호출했다.
    After : 앞뒤 공백을 제거한 뒤 비어 있거나 상한을 넘으면 거부(422)한다.
    """
    cleaned = message.strip()
    if not cleaned:
        raise ValueError("message가 비어 있습니다")
    if len(cleaned) > MAX_MESSAGE_LEN:
        raise ValueError(f"message가 너무 깁니다 (최대 {MAX_MESSAGE_LEN}자)")
    return cleaned
```

**현재 `agent/timing.py` 14~34줄 (`timed_node`)**

```python
def timed_node(name: str, fn, deps):
    """fn(state, deps=deps)을 실행하며 걸린 시간을 기록하는 노드 함수를 돌려준다."""
    bound = partial(fn, deps=deps)   # 의존성 주입은 기존과 동일 (Wave 13)

    async def run(state):
        started = time.perf_counter()
        try:
            delta = await bound(state)
        except Exception as exc:
            logger.error("node=%s FAILED after %.2fs: %s: %s", name,
                         time.perf_counter() - started, type(exc).__name__, exc)
            raise
        elapsed = time.perf_counter() - started
        logger.info("node=%s elapsed=%.2fs", name, elapsed)
        # 같은 노드가 여러 번 실행되면(재조사·재작성) 시간을 합산한다
        timings = dict(state.get("node_timings", {}))
        timings[name] = round(timings.get(name, 0.0) + elapsed, 2)
        return {**delta, "node_timings": timings}

    run.__name__ = name
    return run
```

## 5. 동작 원리 — 코드 읽는 순서
- **입력 검증**: `_clean_message`가 앞뒤 공백을 제거하고, 비었거나 2000자를 넘으면 `ValueError`를 낸다 → `POST /chat`은 Pydantic validator, `GET /chat/stream`은 직접 검사해서 **422**로 응답한다(모델 호출 전).
- **스트림 오류**: 실제 생성 로직은 `_stream_events`로 분리하고, `gen()`이 try/except로 감싼다. 예외가 나면 `{"type":"Error"}` 이벤트를 보내고 `[DONE]`으로 정상 종료한다. 화면은 이 이벤트를 말풍선으로 보여 준다. 실패한 대화는 이력에 저장하지 않는다.
- **노드 시간**: `timed_node`가 노드 실행 전후 시간을 재서 로그(`node=route elapsed=0.4s`)를 남기고 `node_timings`에 누적한다(재작성처럼 같은 노드가 반복되면 합산). 응답의 `metadata.node_timings`와 `ask.py`의 "느린 노드" 줄에서 볼 수 있다.

## 6. 직접 확인하는 방법 (수동)
터미널 A(서버)  : `cd all_total_vue_student && bash sh/run_agent_ollma.sh`  (코드를 바꿨다면 Ctrl+C 후 다시 실행)
터미널 B(질문)  : `cd all_total_vue_student`  → 아래 명령 실행

```bash
# (1) 빈 입력 거부 (Before: 200 + 모델 호출)
curl -i "http://127.0.0.1:8100/chat/stream?message=%20" | head -5          # HTTP/1.1 422
curl -s -X POST http://127.0.0.1:8100/chat -H 'Content-Type: application/json' -d '{"message":"   "}'  # 422 detail

# (2) 느린 노드 확인 (ask.py 화면의 "느린 노드" 줄) + 서버 터미널 A의 로그
time .venv/bin/python3 ask.py "payments-service 결제 응답 지연을 진단해주세요"
#   터미널 A에서  node=route elapsed=…s / node=write elapsed=…s  줄이 보여야 함

# (3) 화면에서 오류 표시 확인(선택): 서버가 켜진 상태에서 브라우저 채팅을 열고 질문을 보낸 직후
#     터미널 A에서 Ctrl+C로 서버를 끄면 "서버와 연결이 끊어졌습니다" 말풍선이 나온다.
```

## 7. 관련 테스트
`.venv/bin/python3 -m pytest -q tests/test_server.py -v`  (빈 입력 422, 길이 초과, 스트림 오류→Error+DONE, 헤더, node_timings)

## 8. 캡처 목록
- `curl -i … message=%20` 의 422 화면
- `ask.py` 결과의 "느린 노드" 줄 + 서버 터미널의 `node=… elapsed=…` 로그
- (선택) 웹 채팅 최종 실행 화면, 연결 끊김 말풍선

## 9. 한계·트레이드오프
- 로그·시간 기록은 서버 프로세스 안의 단순 측정이다(분산 추적은 LangSmith가 담당).
- 입력 길이 2000자는 임의 값이다. 스트림 도중 **클라이언트가 먼저 끊는 경우**는 별도로 처리하지 않았다.
- 인증·속도 제한이 없다(로컬 1인 시연 전제). `web_demo_server.py`가 `0.0.0.0`에 바인딩하는 문제와 기동 스크립트의 `session.db` 삭제는 별도 위험으로 남겨 두었다(`과제1_개선항목_탐색.md`).
