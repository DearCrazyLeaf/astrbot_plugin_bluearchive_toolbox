from __future__ import annotations

import importlib
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

PLUGIN_PARENT = Path(__file__).resolve().parents[2]
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

main_module = importlib.import_module("astrbot_plugin_bluearchive_toolbox.main")
BAToolbox = main_module.BAToolbox


class CommandRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = BAToolbox.__new__(BAToolbox)
        self.plugin.config = {
            "basic_settings": {
                "server_region": "cn",
                "umo_groups": ["aiocqhttp:GroupMessage:123456:jp"],
                "message_mode": "text",
            },
            "command_settings": {"detail_query": ["学生卡"]},
            "menu_settings": {
                "menu_keywords": ["菜单", "ba菜单"],
                "detail_query": ["学生", "ba_student"],
                "menu_timeout_seconds": 600,
                "face_pool": ["14", "9"],
                "menu_items": [
                    {"label": "学生生日", "command": "生日"},
                    {"label": "卡池", "command": "千里眼", "face_id": "66"},
                ],
            },
            "debug_settings": {"debug_log": True},
        }

    def test_plugin_logs_use_the_standard_prefix(self):
        with (
            patch.object(main_module.logger, "info") as info,
            patch.object(main_module.logger, "warning") as warning,
            patch.object(main_module.logger, "error") as error,
        ):
            self.plugin._log_debug("router", "matched", command="学生")
            self.plugin._log_warn("fonts", "font unavailable")
            self.plugin._log_error("student", "lookup failed")

        self.assertTrue(info.call_args.args[0].startswith("[Ba ToolBox][router]"))
        self.assertTrue(
            warning.call_args.args[0].startswith("[Ba ToolBox][fonts]")
        )
        self.assertTrue(error.call_args.args[0].startswith("[Ba ToolBox][student]"))

    def test_reads_values_from_schema_groups(self):
        self.assertEqual(self.plugin._get_setting("message_mode"), "text")
        self.assertEqual(
            self.plugin._get_command("detail_query", ["学生"]),
            ["学生卡", "学生", "ba_student"],
        )

    def test_command_triggers_support_plain_and_slash_forms(self):
        commands = ["学生", "ba_student"]

        self.assertEqual(
            self.plugin._match_command("学生 日富美", commands),
            ("学生", "日富美"),
        )
        self.assertEqual(
            self.plugin._match_command("/学生   日富美（赛车）", commands),
            ("学生", "日富美（赛车）"),
        )
        self.assertEqual(
            self.plugin._match_command("/学生", commands),
            ("学生", ""),
        )
        self.assertEqual(
            self.plugin._match_command("学生会活动", commands),
            (None, ""),
        )

    def test_session_region_mapping_handles_colons_in_umo(self):
        umo = "aiocqhttp:GroupMessage:123456"

        self.assertEqual(self.plugin._get_server_region(umo), "jp")
        self.assertEqual(self.plugin._get_server_region("other:session"), "cn")

    def test_all_region_is_supported_in_defaults_umo_and_command_arguments(self):
        event = SimpleNamespace(unified_msg_origin="aiocqhttp:GroupMessage:123456")
        self.plugin.config["basic_settings"]["server_region"] = "ALL"
        self.assertEqual(self.plugin._get_server_region("other:session"), "all")

        self.plugin.config["basic_settings"]["umo_groups"] = [
            "aiocqhttp:GroupMessage:123456:ALL"
        ]
        self.assertEqual(self.plugin._get_server_region(event.unified_msg_origin), "all")
        self.assertEqual(self.plugin._resolve_server_region(event, " ALL "), ("all", None))

    def test_explicit_region_overrides_session_and_invalid_values_are_reported(self):
        event = SimpleNamespace(unified_msg_origin="aiocqhttp:GroupMessage:123456")

        self.assertEqual(
            self.plugin._resolve_server_region(event, " global "),
            ("global", None),
        )
        self.assertEqual(
            self.plugin._resolve_server_region(event, ""),
            ("jp", None),
        )
        self.assertEqual(
            self.plugin._resolve_server_region(event, "mars"),
            ("", "服务器区域参数无效，请使用 cn、global、jp 或 all。"),
        )

    def test_menu_items_use_configured_face_ids_and_support_legacy_strings(self):
        self.plugin.config["menu_settings"]["menu_items"] = [
            {"label": "生日", "command": "生日"},
            {"label": "自定义卡池", "command": "/千里眼", "face_id": "66"},
            "活动日程,活动",
        ]

        self.assertEqual(
            self.plugin._menu_items(),
            [
                {"label": "生日", "command": "生日", "face_id": "14"},
                {"label": "自定义卡池", "command": "/千里眼", "face_id": "66"},
                {"label": "活动日程", "command": "活动", "face_id": ""},
            ],
        )

    def test_menu_text_selection_matches_number_label_and_command(self):
        self.plugin._active_menus = {
            "menu-message": {
                "group_id": "123456",
                "created_at": 9999999999,
                "items": [
                    {"label": "学生生日", "command": "生日", "face_id": "14"},
                    {"label": "卡池", "command": "千里眼", "face_id": "9"},
                ],
            }
        }

        self.assertEqual(
            self.plugin._match_active_menu_item("123456", "1")[1]["command"],
            "生日",
        )
        self.assertEqual(
            self.plugin._match_active_menu_item("123456", "卡池")[1]["command"],
            "千里眼",
        )
        self.assertEqual(
            self.plugin._match_active_menu_item("123456", "/千里眼")[1]["command"],
            "千里眼",
        )
        self.assertIsNone(self.plugin._match_active_menu_item("other", "1"))

    async def test_guide_command_without_student_name_only_returns_usage(self):
        plugin = BAToolbox.__new__(BAToolbox)
        event = SimpleNamespace(plain_result=lambda text: text)

        results = [
            result async for result in plugin._handle_guide(event, "")
        ]

        self.assertEqual(len(results), 1)
        self.assertIn("用法：攻略 <学生名>", results[0])

    async def test_menu_trigger_sends_reactions_and_registers_active_menu(self):
        self.plugin._active_menus = {}
        self.plugin._menu_debounce = {}
        bot = SimpleNamespace(
            send_group_msg=AsyncMock(return_value={"message_id": "menu-1"}),
            set_msg_emoji_like=AsyncMock(),
        )
        event = SimpleNamespace(
            message_str="ba菜单",
            bot=bot,
            get_group_id=lambda: "123456",
            stop_event=lambda: None,
        )

        sent = await self.plugin._send_reaction_menu(event)

        self.assertTrue(sent)
        bot.send_group_msg.assert_awaited_once()
        bot.set_msg_emoji_like.assert_has_awaits(
            [
                call(message_id="menu-1", emoji_id=14, set=True),
                call(message_id="menu-1", emoji_id=66, set=True),
            ]
        )
        self.assertEqual(
            self.plugin._active_menus["menu-1"]["emoji_map"]["66"]["command"],
            "千里眼",
        )

    async def test_menu_reaction_dispatches_mapped_command(self):
        self.plugin._active_menus = {
            "menu-message": {
                "group_id": "123456",
                "created_at": time.time() - 3,
                "items": [],
                "emoji_map": {
                    "14": {"label": "学生生日", "command": "生日"}
                },
            }
        }
        self.plugin._menu_debounce = {}
        self.plugin._execute_menu_command = AsyncMock(return_value=True)
        self.plugin._stop_menu_event = lambda _event: None
        event = SimpleNamespace()
        raw = {
            "notice_type": "group_msg_emoji_like",
            "message_id": "menu-message",
            "user_id": "789",
            "likes": [{"emoji_id": "14"}],
        }

        handled = await self.plugin._handle_menu_reaction(event, raw)

        self.assertTrue(handled)
        self.plugin._execute_menu_command.assert_awaited_once_with(
            event, "789", "123456", "生日"
        )


if __name__ == "__main__":
    unittest.main()
