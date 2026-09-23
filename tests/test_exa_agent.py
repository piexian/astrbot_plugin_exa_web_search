import json
import unittest

from tools.exa_agent import (
    ExaAgentAPIError,
    ExaAgentClient,
    normalize_remote_run,
    sources_from_events,
)


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, text=None):
        self.status = status
        self._payload = payload
        self._text = text
        self.headers = headers or {}

    async def text(self):
        if self._text is not None:
            return self._text
        return json.dumps(self._payload or {})


class ResponseContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        return False


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return ResponseContext(response)


def completed_run():
    return {
        "id": "agent_run_123",
        "object": "agent_run",
        "status": "completed",
        "stopReason": "schema_satisfied",
        "createdAt": "2026-09-24T01:00:00Z",
        "completedAt": "2026-09-24T01:02:00Z",
        "requestId": "req-1",
        "output": {
            "text": "FastAPI is the winner.",
            "structured": {"winner": "FastAPI"},
            "grounding": [
                {
                    "field": "structured.winner",
                    "confidence": "high",
                    "citations": [
                        {
                            "url": "https://fastapi.tiangolo.com/",
                            "title": "FastAPI",
                        }
                    ],
                }
            ],
        },
        "costDollars": {"total": 1.25, "search": 0.25},
    }


class ExaAgentClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_run_uses_json_and_archives_normalized_fields(self):
        session = FakeSession(FakeResponse(payload=completed_run()))
        client = ExaAgentClient(
            session,
            "https://proxy.example/exa",
            proxy="http://127.0.0.1:7890",
        )

        run = await client.create_run(
            "compare frameworks",
            "secret-key",
            effort="auto",
            budget_max_dollars=5,
            metadata={"task_id": "r-20260924-0001"},
        )

        self.assertEqual(run.run_id, "agent_run_123")
        self.assertEqual(run.sources[0]["url"], "https://fastapi.tiangolo.com/")
        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://proxy.example/exa/agent/runs")
        self.assertEqual(call["headers"]["Accept"], "application/json")
        self.assertEqual(call["headers"]["x-api-key"], "secret-key")
        self.assertEqual(call["json"]["metadata"]["task_id"], "r-20260924-0001")
        self.assertEqual(call["json"]["budget"], {"maxCostDollars": 5})

    async def test_create_error_redacts_key_and_marks_uncertain_outcome(self):
        session = FakeSession(
            FakeResponse(
                status=500,
                payload={"error": {"message": "failed secret-key"}},
            )
        )
        client = ExaAgentClient(session, "https://api.exa.ai")

        with self.assertRaises(ExaAgentAPIError) as raised:
            await client.create_run("query", "secret-key")

        self.assertNotIn("secret-key", str(raised.exception))
        self.assertTrue(raised.exception.outcome_uncertain)
    async def test_create_success_parsing_failures_are_uncertain(self):
        responses = [
            FakeResponse(text=""),
            FakeResponse(text="{"),
            FakeResponse(payload={"status": "completed"}),
            FakeResponse(payload={"id": "agent_run_1", "status": "paused"}),
        ]
        for response in responses:
            with self.subTest(response=response):
                client = ExaAgentClient(FakeSession(response), "https://api.exa.ai")
                with self.assertRaises(ExaAgentAPIError) as raised:
                    await client.create_run("query", "secret-key")
                self.assertTrue(raised.exception.outcome_uncertain)

    async def test_event_pagination_uses_cursor_and_stops(self):
        session = FakeSession(
            FakeResponse(
                payload={
                    "data": [{"event": "agent_run.source.added", "data": {}}],
                    "hasMore": True,
                    "nextCursor": "cursor-1",
                }
            ),
            FakeResponse(
                payload={
                    "data": [
                        {
                            "event": "agent_run.source.added",
                            "data": {
                                "url": "https://example.com/",
                                "title": "Example",
                            },
                        }
                    ],
                    "hasMore": False,
                    "nextCursor": None,
                }
            ),
        )
        client = ExaAgentClient(session, "https://api.exa.ai")

        events = await client.list_all_events("agent_run_123", "secret-key")

        self.assertEqual(len(events), 2)
        self.assertEqual(session.calls[1]["params"], {"limit": 100, "cursor": "cursor-1"})
        self.assertEqual(sources_from_events(events)[0]["url"], "https://example.com/")

    async def test_cancel_uses_official_endpoint(self):
        payload = completed_run()
        payload["status"] = "cancelled"
        payload["stopReason"] = "cancelled"
        session = FakeSession(FakeResponse(payload=payload))
        client = ExaAgentClient(session, "https://api.exa.ai")

        run = await client.cancel_run("agent_run_123", "secret-key")

        self.assertEqual(run.status, "cancelled")
        self.assertEqual(
            session.calls[0]["url"],
            "https://api.exa.ai/agent/runs/agent_run_123/cancel",
        )

    def test_normalizer_rejects_unknown_status(self):
        payload = completed_run()
        payload["status"] = "paused"
        with self.assertRaisesRegex(ValueError, "未知状态"):
            normalize_remote_run(payload)


if __name__ == "__main__":
    unittest.main()
