import tempfile
import unittest
from pathlib import Path

from tools.exa_files import (
    cleanup_exports,
    format_bytes,
    render_task_markdown,
    render_task_stats,
    write_task_markdown,
)
from tools.exa_tasks import CapacityStatus, CleanupResult, TaskRecord


def archived_task():
    return TaskRecord(
        task_id="r-20260924-0001",
        run_id="agent_run_123",
        key_slot=2,
        status="completed",
        query="FastAPI security review",
        created_at="2026-09-24T01:00:00+00:00",
        updated_at="2026-09-24T01:02:00+00:00",
        completed_at="2026-09-24T01:02:00+00:00",
        result={"text": "Use OAuth2", "structured": {"risk": "low"}},
        sources=[{"title": "FastAPI security", "url": "https://fastapi.tiangolo.com/"}],
        request_id="req-1",
        cost={"total": 1.25},
    )


class ExaFileRenderingTests(unittest.TestCase):
    def test_markdown_contains_archive_fields_without_key_slot(self):
        markdown = render_task_markdown(archived_task())
        for text in (
            "r-20260924-0001",
            "FastAPI security review",
            "agent_run_123",
            "Use OAuth2",
            "https://fastapi.tiangolo.com/",
            "$1.2500",
        ):
            self.assertIn(text, markdown)
        self.assertNotIn("key_slot", markdown)
        self.assertNotIn("api_key", markdown.lower())

    def test_stats_contains_capacity_and_cleanup(self):
        capacity = CapacityStatus(80 * 1024 * 1024, 100 * 1024 * 1024)
        cleanup = CleanupResult(("r-1",), 1024, 80 * 1024 * 1024, automatic=True)
        text = render_task_stats(archived_task(), capacity, cleanup)
        self.assertIn("80.0%", text)
        self.assertIn("引用数量：1", text)
        self.assertIn("最近自动清理", text)
        self.assertEqual(format_bytes(1024), "1.0 KiB")

    def test_write_and_cleanup_export(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_task_markdown(temp_dir, archived_task())
            self.assertTrue(path.exists())
            self.assertEqual(path.suffix, ".md")
            path.unlink()
            path.touch()
            mtime = path.stat().st_mtime
            self.assertEqual(cleanup_exports(temp_dir, max_age_seconds=0, now=mtime), 0)
            self.assertEqual(cleanup_exports(temp_dir, max_age_seconds=-1), 1)
            self.assertFalse(Path(path).exists())


if __name__ == "__main__":
    unittest.main()
