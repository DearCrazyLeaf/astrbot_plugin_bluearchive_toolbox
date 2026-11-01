from __future__ import annotations

import importlib
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock

PLUGIN_PARENT = Path(__file__).resolve().parents[2]
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

main_module = importlib.import_module("astrbot_plugin_bluearchive_toolbox.main")
PollArchive = importlib.import_module(
    "astrbot_plugin_bluearchive_toolbox.poll_archive"
).PollArchive


class PollSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_run_archives_successes_and_category_failures(self):
        with TemporaryDirectory() as temp_dir:
            plugin = main_module.BAToolbox.__new__(main_module.BAToolbox)
            plugin.config = {
                "polling_settings": {
                    "poll_region": "all",
                    "poll_categories": ["schedule", "birthday", "pool"],
                    "retention_days": 30,
                }
            }
            plugin.poll_archive = PollArchive(temp_dir)
            plugin._log_warn = Mock()
            plugin._log_error = Mock()
            plugin._log_debug = Mock()
            plugin._render_poll_card = AsyncMock(
                side_effect=[
                    (b"schedule-card", 4),
                    RuntimeError("birthday API unavailable"),
                    (b"pool-card", 2),
                ]
            )

            await plugin._run_daily_poll(
                datetime.fromisoformat("2025-03-08T08:00:00+08:00")
            )

            entries = plugin.poll_archive.get_day("2025-03-08")
            self.assertEqual(
                [entry["category"] for entry in entries],
                ["schedule", "birthday", "pool"],
            )
            self.assertEqual(
                [entry["status"] for entry in entries],
                ["success", "failed", "success"],
            )
            self.assertEqual(entries[1]["error"], "birthday API unavailable")
            self.assertEqual(entries[0]["region"], "all")
            self.assertEqual(
                plugin.poll_archive.image_path(
                    "2025-03-08", "pool", "20250308T080000"
                ).read_bytes(),
                b"pool-card",
            )


if __name__ == "__main__":
    unittest.main()
