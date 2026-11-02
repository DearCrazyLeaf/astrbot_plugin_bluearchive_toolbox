from __future__ import annotations

import asyncio
import base64
import importlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PARENT = PLUGIN_ROOT.parent
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

stubbed_astrbot = False
try:
    importlib.import_module("astrbot.api.web")
except ModuleNotFoundError:
    stubbed_astrbot = True
    astrbot_module = ModuleType("astrbot")
    api_module = ModuleType("astrbot.api")
    web_module = ModuleType("astrbot.api.web")
    web_module.error_response = lambda *_args, **_kwargs: None
    web_module.file_response = lambda *_args, **_kwargs: None
    web_module.json_response = lambda *_args, **_kwargs: None
    web_module.request = None
    sys.modules.update(
        {
            "astrbot": astrbot_module,
            "astrbot.api": api_module,
            "astrbot.api.web": web_module,
        }
    )

BAToolboxWebUI = importlib.import_module(
    "astrbot_plugin_bluearchive_toolbox.webui"
).BAToolboxWebUI
webui_module = importlib.import_module("astrbot_plugin_bluearchive_toolbox.webui")
if stubbed_astrbot:
    for module_name in ("astrbot.api.web", "astrbot.api", "astrbot"):
        sys.modules.pop(module_name, None)


class CacheUsageTests(unittest.TestCase):
    def test_poll_archive_preview_returns_base64_image_for_dashboard_bridge(self):
        with TemporaryDirectory() as temp_dir:
            archive = importlib.import_module(
                "astrbot_plugin_bluearchive_toolbox.poll_archive"
            ).PollArchive(temp_dir)
            archive.save_result(
                archive_date="2025-03-08",
                run_id="20250308T080000",
                category="schedule",
                region="all",
                item_count=1,
                image=b"png-card",
            )
            plugin = SimpleNamespace(
                poll_archive=archive,
                _log_warn=lambda *_args, **_kwargs: None,
            )
            with patch.object(
                webui_module,
                "json_response",
                side_effect=lambda payload: payload,
            ):
                result = asyncio.run(
                    BAToolboxWebUI(plugin).poll_archive_image_preview(
                        "2025-03-08", "schedule", "20250308T080000"
                    )
                )

        self.assertEqual(
            result["data"]["image"],
            base64.b64encode(b"png-card").decode("ascii"),
        )
        self.assertEqual(result["data"]["content_type"], "image/png")

    def test_usage_sums_each_managed_cache_category(self):
        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            cache_dir = data_dir / "cache"
            files = {
                cache_dir / "images" / "card.png": b"card-image",
                cache_dir / "meta" / "card.json": b"metadata",
                cache_dir / "schaledb" / "data" / "students.json": b"student-data",
                cache_dir / "schaledb" / "assets" / "student.webp": b"student-image",
                data_dir / "fonts" / "font.ttf": b"font-data",
                data_dir / "untracked.txt": b"not-cache-data",
            }
            for path, content in files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            plugin = SimpleNamespace(
                _cache_dir=cache_dir,
                _data_dir=data_dir,
                image_cache=SimpleNamespace(max_items=200, max_size_bytes=500 * 1024**2),
            )
            result = BAToolboxWebUI(plugin)._cache_usage()

            expected_paths = [
                cache_dir / "images" / "card.png",
                cache_dir / "meta" / "card.json",
                cache_dir / "schaledb" / "data" / "students.json",
                cache_dir / "schaledb" / "assets" / "student.webp",
                data_dir / "fonts" / "font.ttf",
            ]
            self.assertEqual(
                result["total_bytes"],
                sum(path.stat().st_size for path in expected_paths),
            )
            self.assertEqual(result["total_files"], len(expected_paths))
            self.assertEqual(result["card_cache_max_items"], 200)
            self.assertEqual(result["card_cache_max_bytes"], 500 * 1024**2)
            self.assertEqual(
                {item["id"]: item["files"] for item in result["categories"]},
                {
                    "card_images": 1,
                    "card_metadata": 1,
                    "schaledb_data": 1,
                    "schaledb_assets": 1,
                    "downloaded_fonts": 1,
                },
            )

    def test_missing_cache_directories_report_zero_usage(self):
        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            plugin = SimpleNamespace(
                _cache_dir=data_dir / "cache",
                _data_dir=data_dir,
                image_cache=SimpleNamespace(max_items=10, max_size_bytes=1024),
            )

            result = BAToolboxWebUI(plugin)._cache_usage()

            self.assertEqual(result["total_bytes"], 0)
            self.assertEqual(result["total_files"], 0)
            self.assertTrue(all(item["bytes"] == 0 for item in result["categories"]))

    def test_settings_include_ba_toolbox_menu_schema_and_defaults(self):
        plugin = SimpleNamespace(config={})

        group = BAToolboxWebUI(plugin)._groups()["menu_settings"]

        self.assertEqual(group["description"], "表情回应菜单")
        self.assertEqual(group["values"]["menu_keywords"], ["菜单", "ba菜单"])
        self.assertEqual(group["values"]["menu_timeout_seconds"], 600)
        self.assertIn("detail_query", group["values"])
        self.assertIn("menu_items", group["items"])
        self.assertEqual(group["values"]["menu_items"][0]["face_id"], "14")
        self.assertNotIn(
            "攻略",
            [item["command"] for item in group["values"]["menu_items"]],
        )
        self.assertIn("不支持点击后再输入参数", group["items"]["menu_items"]["hint"])

    def test_settings_save_persists_ba_toolbox_menu_options(self):
        class Config(dict):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.save_count = 0

            def save_config(self):
                self.save_count += 1

        menu_config = Config(
            {
                "menu_keywords": ["菜单"],
                "menu_items": ["旧菜单项"],
            }
        )
        plugin = SimpleNamespace(config=Config({"menu_settings": menu_config}))
        body = {
            "groups": {
                "menu_settings": {
                    "menu_keywords": ["菜单", "指令"],
                    "menu_items": [
                        {
                            "label": "学生详情",
                            "command": "学生 日富美",
                            "face_id": "14",
                        }
                    ],
                }
            }
        }

        class RequestStub:
            @staticmethod
            async def json(default=None):
                return body if body is not None else default

        with patch.object(webui_module, "request", RequestStub):
            asyncio.run(BAToolboxWebUI(plugin).save_settings())

        saved_menu_config = plugin.config["menu_settings"]
        self.assertEqual(saved_menu_config["menu_keywords"], ["菜单", "指令"])
        self.assertEqual(
            saved_menu_config["menu_items"],
            [
                {
                    "label": "学生详情",
                    "command": "学生 日富美",
                    "face_id": "14",
                }
            ],
        )
        self.assertEqual(plugin.config.save_count, 1)


if __name__ == "__main__":
    unittest.main()
