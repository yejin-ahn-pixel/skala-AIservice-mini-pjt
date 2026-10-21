"""질문 1개를 서버에 보내고 결과를 읽기 쉽게 출력한다.

사용법:  python3 ask.py "web-01 상태 진단해줘"
(서버는 다른 터미널에서 켜져 있어야 한다: http://127.0.0.1:8100)
"""
import json
import sys
import textwrap
import time
import urllib.request

URL = "http://127.0.0.1:8100/chat"


def render(question: str, d: dict) -> str:
    events = d.get("events", [])
    meta = d.get("metadata", {})
    targets = next((e.get("targets") for e in events if e["type"] == "PlanCreated"), None)
    runbooks = [e["target"] for e in events
                if e["type"] == "TaskCompleted" and e.get("source") == "runbook"]
    verdicts = [f'{e["attempt"]}회 {e["verdict"]}' for e in events if e["type"] == "Validation"]
    issues = [f'{e["attempt"]}회: ' + "; ".join(e["issues"])
              for e in events if e["type"] == "Validation" and e.get("issues")]
    timings = meta.get("node_timings") or {}
    slowest = sorted(timings.items(), key=lambda kv: -kv[1])[:3]
    lines = [
        f"질문        : {question}",
        f"route       : {meta.get('route')}",
        f"조사 대상   : {', '.join(targets) if targets else '-'}",
        f"선택 런북   : {', '.join(runbooks) if runbooks else '-'}",
        f"검증        : {' → '.join(verdicts) if verdicts else '-'}",
        *([f"검증 사유   : {' / '.join(issues)}"] if issues else []),
        f"재시도/비용 : retry {meta.get('retry_count')}회 / ${meta.get('cost_usd')}",
        *([f"느린 노드   : {', '.join(f'{n} {t}s' for n, t in slowest)}"] if slowest else []),
        "단계        : " + " → ".join(e["type"] for e in events),
        "답변        :",
        textwrap.indent(textwrap.fill(d.get("reply", ""), 70), "   "),
    ]
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit('사용법: python3 ask.py "질문"')
    question = sys.argv[1]
    body = json.dumps({"message": question,
                       "thread_id": f"ask-{int(time.time())}"}).encode()
    req = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.load(r)
    except OSError as e:
        sys.exit(f"서버에 연결할 수 없습니다 ({e}). server.py가 켜져 있는지 확인하세요.")
    print(render(question, d))


if __name__ == "__main__":
    main()
