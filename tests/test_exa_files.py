import tempfile
import unittest
from pathlib import Path

from tools.exa_files import (
    AUTO_NOTIFICATION_TEXT_LIMIT,
    cleanup_exports,
    format_bytes,
    render_completion_notification,
    render_task_markdown,
    render_task_stats,
    write_completion_notification_markdown,
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

    def test_completion_notification_file_contains_only_task_id_and_body(self):
        task_id = "r-20260924-0001"
        body = "完整结果正文" * 300
        message = render_completion_notification(task_id, body)
        self.assertEqual(message, f"{task_id}\n\n{body}")
        self.assertGreater(len(message), AUTO_NOTIFICATION_TEXT_LIMIT)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_completion_notification_markdown(temp_dir, task_id, body)
            self.assertEqual(path.name, f"{task_id}.md")
            self.assertEqual(path.read_text(encoding="utf-8"), message + "\n")
            self.assertNotIn("查询内容", path.read_text(encoding="utf-8"))
            self.assertNotIn("费用", path.read_text(encoding="utf-8"))

    def test_long_stats_summary_explains_truncation_and_explicit_export(self):
        task = archived_task()
        task.result = {"text": "x" * 1601}
        text = render_task_stats(task, CapacityStatus(0, 100))

        self.assertIn("仅显示前 1500 / 1601 字符", text)
        self.assertIn("/exa stats -q r-20260924-0001", text)
        summary = text.split("结果摘要：\n", 1)[1]
        self.assertTrue(summary.startswith("x" * 1500))
        self.assertIn("仅显示前 1500 / 1601 字符", summary)


if __name__ == "__main__":
    unittest.main()
