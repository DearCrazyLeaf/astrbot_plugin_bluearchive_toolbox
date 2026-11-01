from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PLUGIN_PARENT = Path(__file__).resolve().parents[2]
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

from astrbot_plugin_bluearchive_toolbox.poll_archive import PollArchive


class PollArchiveTests(unittest.TestCase):
    def test_saves_and_lists_successful_and_failed_results(self):
        with TemporaryDirectory() as temp_dir:
            archive = PollArchive(temp_dir)
            image = b"png-data"
            success = archive.save_result(
                archive_date="2025-03-08",
                run_id="20250308T080000",
                category="schedule",
                region="all",
                item_count=3,
                image=image,
            )
            failure = archive.save_result(
                archive_date="2025-03-08",
                run_id="20250308T080000",
                category="birthday",
                region="all",
                item_count=0,
                image=None,
                error="API unavailable",
            )

            self.assertEqual(success["status"], "success")
            self.assertEqual(success["run_id"], "20250308T080000")
            self.assertEqual(success["delivery_status"], "unconfigured")
            self.assertEqual(failure["status"], "failed")
            self.assertEqual(failure["error"], "API unavailable")
            self.assertEqual(failure["delivery_status"], "skipped")
            self.assertEqual(archive.get_day("2025-03-08"), [success, failure])
            self.assertEqual(
                archive.list_days(),
                [
                    {
                        "date": "2025-03-08",
                        "entry_count": 2,
                        "success_count": 1,
                        "failure_count": 1,
                    }
                ],
            )
            self.assertEqual(
                archive.image_path("2025-03-08", "schedule", success["run_id"]).read_bytes(),
                image,
            )
            with self.assertRaises(FileNotFoundError):
                archive.image_path("2025-03-08", "birthday", failure["run_id"])

    def test_updates_and_persists_delivery_results(self):
        with TemporaryDirectory() as temp_dir:
            archive = PollArchive(temp_dir)
            archive.save_result(
                archive_date="2025-03-08",
                run_id="20250308T080000",
                category="schedule",
                region="all",
                item_count=3,
                image=b"png-data",
                has_push_targets=True,
            )

            updated = archive.update_delivery(
                archive_date="2025-03-08",
                category="schedule",
                run_id="20250308T080000",
                delivery_results=[
                    {
                        "target": "aiocqhttp:GroupMessage:123456",
                        "status": "sent",
                        "error": "",
                    },
                    {
                        "target": "aiocqhttp:GroupMessage:654321",
                        "status": "failed",
                        "error": "connection failed",
                    },
                ],
            )

            self.assertEqual(updated["delivery_status"], "partial")
            self.assertEqual(
                archive.get_day("2025-03-08")[0]["delivery_results"],
                updated["delivery_results"],
            )

    def test_rejects_invalid_archive_paths(self):
        with TemporaryDirectory() as temp_dir:
            archive = PollArchive(temp_dir)
            invalid_identifiers = [
                ("../2025-03-08", "schedule", "20250308T080000"),
                ("2025-03-08", "../schedule", "20250308T080000"),
                ("2025-03-08", "schedule", "../index"),
            ]
            for archive_date, category, run_id in invalid_identifiers:
                with self.subTest(
                    archive_date=archive_date, category=category, run_id=run_id
                ):
                    with self.assertRaises(ValueError):
                        archive.image_path(archive_date, category, run_id)

    def test_cleanup_respects_retention_boundary(self):
        with TemporaryDirectory() as temp_dir:
            archive = PollArchive(temp_dir)
            for archive_date in ("2025-03-06", "2025-03-07", "2025-03-08"):
                archive.save_result(
                    archive_date=archive_date,
                    run_id=f"{archive_date.replace('-', '')}T080000",
                    category="schedule",
                    region="all",
                    item_count=0,
                    image=b"png",
                )

            removed = archive.cleanup(3, today=date(2025, 3, 10))

            self.assertEqual(removed, 1)
            self.assertFalse(Path(temp_dir, "2025-03-06").exists())
            self.assertTrue(Path(temp_dir, "2025-03-07").exists())
            self.assertTrue(Path(temp_dir, "2025-03-08").exists())


if __name__ == "__main__":
    unittest.main()
