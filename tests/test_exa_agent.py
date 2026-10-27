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

    async def test_malformed_status_response_is_retryable_error(self):
        client = ExaAgentClient(
            FakeSession(
                FakeResponse(payload={"id": "agent_run_1", "status": "paused"})
            ),
            "https://api.exa.ai",
        )
        with self.assertRaises(ExaAgentAPIError) as raised:
            await client.get_run("agent_run_1", "secret-key")
        self.assertEqual(raised.exception.status, 200)
        self.assertIn("继续重试", str(raised.exception))

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
        self.assertEqual(
            session.calls[1]["params"], {"limit": 100, "cursor": "cursor-1"}
        )
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



class AgentEffortBudgetContractTests(unittest.TestCase):
    def test_effort_includes_ultra_and_maps_legacy_max(self):
        from tools.exa_agent import (
            AGENT_EFFORTS,
            normalize_agent_effort,
        )

        self.assertIn("ultra", AGENT_EFFORTS)
        self.assertNotIn("max", AGENT_EFFORTS)
        self.assertEqual(normalize_agent_effort("max"), "ultra")
        self.assertEqual(normalize_agent_effort("MAX"), "ultra")
        self.assertEqual(normalize_agent_effort("xhigh"), "xhigh")
        self.assertEqual(normalize_agent_effort("bogus"), "auto")

    def test_budget_clamps_into_official_window(self):
        from tools.exa_agent import normalize_budget_dollars

        self.assertIsNone(normalize_budget_dollars(None))
        self.assertIsNone(normalize_budget_dollars(0))
        self.assertIsNone(normalize_budget_dollars(0.5))
        self.assertEqual(normalize_budget_dollars(1), 1.0)
        self.assertEqual(normalize_budget_dollars(50), 50.0)
        self.assertEqual(normalize_budget_dollars(1000), 100.0)

    def test_duration_clamps_into_official_window(self):
        from tools.exa_agent import normalize_budget_duration_seconds

        self.assertIsNone(normalize_budget_duration_seconds(None))
        self.assertIsNone(normalize_budget_duration_seconds(0))
        self.assertEqual(normalize_budget_duration_seconds(10), 300)
        self.assertEqual(normalize_budget_duration_seconds(600), 600)
        self.assertEqual(normalize_budget_duration_seconds(20000), 10800)

    def test_output_schema_requires_json_object(self):
        from tools.exa_agent import normalize_agent_output_schema

        self.assertEqual(
            normalize_agent_output_schema({"type": "object"}), {"type": "object"}
        )
        self.assertEqual(
            normalize_agent_output_schema('{"type":"object"}'), {"type": "object"}
        )
        for bad in ([], {}, "[]", "null", 3):
            with self.subTest(bad=bad):
                self.assertRaises(ValueError, normalize_agent_output_schema, bad)


class AgentCreateRunContractTests(unittest.IsolatedAsyncioTestCase):

    async def test_ultra_run_carries_budget_duration_schema_and_previous(self):
        session = FakeSession(FakeResponse(payload=completed_run()))
        client = ExaAgentClient(session, "https://api.exa.ai")
        await client.create_run(
            "q",
            "k",
            effort="ultra",
            budget_max_dollars=50,
            budget_max_duration_seconds=600,
            output_schema={"type": "object"},
            previous_run_id="agent_run_prev",
            metadata={"task_id": "t"},
        )
        payload = session.calls[0]["json"]
        self.assertEqual(payload["effort"], "ultra")
        self.assertEqual(
            payload["budget"], {"maxCostDollars": 50, "maxDurationSeconds": 600}
        )
        self.assertEqual(payload["outputSchema"], {"type": "object"})
        self.assertEqual(payload["previousRunId"], "agent_run_prev")
        self.assertEqual(payload["metadata"], {"task_id": "t"})

    async def test_legacy_max_maps_to_ultra_and_keeps_budget(self):
        session = FakeSession(FakeResponse(payload=completed_run()))
        client = ExaAgentClient(session, "https://api.exa.ai")
        await client.create_run("q", "k", effort="max", budget_max_dollars=3)
        payload = session.calls[0]["json"]
        self.assertEqual(payload["effort"], "ultra")
        self.assertEqual(payload["budget"], {"maxCostDollars": 3.0})

    async def test_budget_skipped_for_other_efforts_and_below_one_dollar(self):
        session = FakeSession(
            FakeResponse(payload=completed_run()),
            FakeResponse(payload=completed_run()),
            FakeResponse(payload=completed_run()),
        )
        client = ExaAgentClient(session, "https://api.exa.ai")
        await client.create_run(
            "q", "k", effort="high", budget_max_dollars=3, budget_max_duration_seconds=600
        )
        self.assertNotIn("budget", session.calls[0]["json"])
        await client.create_run("q", "k", effort="auto", budget_max_dollars=5)
        self.assertEqual(
            session.calls[1]["json"]["budget"], {"maxCostDollars": 5.0}
        )
        await client.create_run("q", "k", effort="ultra", budget_max_dollars=0.2)
        self.assertNotIn("budget", session.calls[2]["json"])

    async def test_default_payload_stays_minimal(self):
        session = FakeSession(FakeResponse(payload=completed_run()))
        client = ExaAgentClient(session, "https://api.exa.ai")
        await client.create_run("q", "k")
        self.assertEqual(
            session.calls[0]["json"], {"query": "q", "effort": "auto"}
        )

    async def test_stop_run_posts_to_stop_endpoint(self):
        session = FakeSession(FakeResponse(payload=completed_run()))
        client = ExaAgentClient(session, "https://api.exa.ai")
        run = await client.stop_run("agent_run_123", "k")
        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertTrue(call["url"].endswith("/agent/runs/agent_run_123/stop"))
        self.assertEqual(run.run_id, "agent_run_123")

if __name__ == "__main__":
    unittest.main()
