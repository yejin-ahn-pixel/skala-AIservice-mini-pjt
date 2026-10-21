"""테스트용 가짜 모델·MCP — SupervisorDeps에 주입해 Ollama·MCP 서버 없이 그래프를 실행한다."""


class StubMCP:
    def __init__(self, alerts_by_system=None, fail_systems=(), hang_systems=()):
        self.monitored, self.calls, self.runbook_uris = [], [], []
        self.alerts_by_system = alerts_by_system or {}
        self.fail_systems = set(fail_systems)   # 이 시스템 조회는 예외 발생 (C10 테스트용)
        self.hang_systems = set(hang_systems)   # 이 시스템 조회는 응답 없이 멈춤 (타임아웃 테스트용)

    async def find_prompt(self, name):
        self.calls.append("find_prompt")
        return "srv"

    async def get_prompt(self, name, args, server=None):
        self.calls.append("get_prompt")
        return "표준 절차"

    async def call_tool(self, name, args):
        self.monitored.append(args["system_name"])
        if args["system_name"] in self.fail_systems:
            raise ConnectionError("simulated MCP failure")
        if args["system_name"] in self.hang_systems:
            import asyncio
            await asyncio.sleep(3600)
        alerts = self.alerts_by_system.get(args["system_name"], [])
        return {"system_name": args["system_name"],
                "status": "degraded" if alerts else "healthy",
                "cpu_percent": 30.0, "memory_percent": 40.0, "alerts": alerts}

    async def find_resource_template(self, name):
        return "srv", "runbook://incident-response/{incident_type}"

    async def read_resource(self, uri, server=None):
        self.runbook_uris.append(uri)
        return "# Runbook\n1. 확인한다."


class StubFast:
    """route는 incident, plan은 항상 web-01 하나만 세우는 소형 모델 (B6 상황)."""
    def __init__(self):
        self.plan_calls = 0

    def route(self, q):
        return {"route": "incident", "needs_analysis": False}, {"usd": 0.0}

    def plan(self, q, procedure):
        self.plan_calls += 1
        tasks = [{"id": "task-0", "agent": "research", "kind": "monitoring",
                  "target": "web-01", "status": "pending"}]
        return {"needs_analysis": False, "tasks": tasks}, {"usd": 0.0}


class StubQuality:
    """검증을 통과하는 고정 답변."""
    async def write(self, query, collected, attempt, feedback, on_token=None):
        return "1) 수집된 데이터를 기준으로 확인한 결과 정상입니다. 2) 추가 조치 없음.", \
               {"usd": 0.0, "prompt": "p"}


class SeqQuality:
    """attempt 순서대로 준비된 초안을 돌려준다 (마지막 초안 반복). 받은 피드백을 기록한다."""
    def __init__(self, drafts):
        self.drafts, self.feedbacks = drafts, []

    async def write(self, query, collected, attempt, feedback, on_token=None):
        self.feedbacks.append(feedback)
        return self.drafts[min(attempt - 1, len(self.drafts) - 1)], {"usd": 0.0, "prompt": "p"}
