from __future__ import annotations

import importlib
import sys
import threading
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
        self.plugin._active_menus = {}
        self.plugin._menu_debounce = {}
        self.plugin._pending_student_queries = {}
        self.plugin._student_choices_by_token = {}
        self.plugin._student_candidate_menus = {}
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
            "student_choice_settings": {
                "timeout_seconds": 120,
                "max_active_menus": 3,
                "face_pool": [],
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
        self.assertTrue(warning.call_args.args[0].startswith("[Ba ToolBox][fonts]"))
        self.assertTrue(error.call_args.args[0].startswith("[Ba ToolBox][student]"))

    async def test_sync_card_render_runs_outside_the_asyncio_thread(self):
        event_loop_thread = threading.get_ident()
        render_thread = await self.plugin._render_in_thread(threading.get_ident)

        self.assertNotEqual(render_thread, event_loop_thread)

    async def test_playwright_chromium_is_installed_when_missing(self):
        with (
            patch.object(
                main_module, "is_chromium_installed", side_effect=[False, True]
            ),
            patch.object(
                main_module.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
            ) as run,
        ):
            await self.plugin._ensure_playwright_chromium()

        self.assertEqual(
            run.call_args.args[0],
            [
                main_module.sys.executable,
                "-m",
                "playwright",
                "install",
                "chromium",
            ],
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 900)

    async def test_playwright_install_failure_is_reported(self):
        with (
            patch.object(main_module, "is_chromium_installed", return_value=False),
            patch.object(
                main_module.subprocess,
                "run",
                return_value=SimpleNamespace(
                    returncode=1, stdout="", stderr="download failed"
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "download failed"),
        ):
            await self.plugin._ensure_playwright_chromium()

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
        self.assertEqual(
            self.plugin._get_server_region(event.unified_msg_origin), "all"
        )
        self.assertEqual(
            self.plugin._resolve_server_region(event, " ALL "), ("all", None)
        )

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

    async def test_student_command_without_name_prompts_for_partial_name(self):
        plugin = BAToolbox.__new__(BAToolbox)
        plugin.config = {"command_settings": {"detail_query": ["学生卡"]}}
        event = SimpleNamespace(plain_result=lambda text: text)

        results = [result async for result in plugin._handle_student_detail(event, "")]

        self.assertEqual(len(results), 1)
        self.assertIn("至少一部分学生名称", results[0])
        self.assertIn("用法：/学生卡 <学生名>", results[0])

    async def test_guide_command_without_student_name_prompts_for_partial_name(self):
        plugin = BAToolbox.__new__(BAToolbox)
        plugin.config = {"command_settings": {"guide_query": ["学生攻略"]}}
        event = SimpleNamespace(plain_result=lambda text: text)

        results = [result async for result in plugin._handle_guide(event, "")]

        self.assertEqual(len(results), 1)
        self.assertIn("至少一部分学生名称", results[0])
        self.assertIn("用法：/学生攻略 <学生名>", results[0])

    async def test_guide_searches_student_candidates_then_uses_localized_name(self):
        plugin = BAToolbox.__new__(BAToolbox)
        plugin.config = {"basic_settings": {"student_data_language": "cn"}}
        plugin._schaledb_client = SimpleNamespace(
            search_student_candidates=AsyncMock(
                return_value=[{"Id": 1, "Name": "爱丽丝"}]
            )
        )
        plugin._arona_client = SimpleNamespace(
            get_guide_image=AsyncMock(return_value="https://example.test/guide.png"),
            last_error=None,
        )
        event = SimpleNamespace(
            unified_msg_origin="",
            image_result=lambda url: ("image", url),
            plain_result=lambda text: ("text", text),
        )

        results = [result async for result in plugin._handle_guide(event, "爱丽")]

        plugin._schaledb_client.search_student_candidates.assert_awaited_once_with(
            "爱丽", "cn"
        )
        plugin._arona_client.get_guide_image.assert_awaited_once_with("爱丽丝")
        self.assertEqual(results, [("image", "https://example.test/guide.png")])

    async def test_ambiguous_guide_search_starts_guide_candidate_menu(self):
        plugin = BAToolbox.__new__(BAToolbox)
        plugin.config = {"basic_settings": {"student_data_language": "cn"}}
        candidates = [
            {"Id": 1, "Name": "爱丽丝"},
            {"Id": 2, "Name": "爱丽丝（女仆）"},
        ]
        plugin._schaledb_client = SimpleNamespace(
            search_student_candidates=AsyncMock(return_value=candidates)
        )
        plugin._start_student_choice = AsyncMock(return_value=(True, "菜单"))
        event = SimpleNamespace(unified_msg_origin="", plain_result=lambda text: text)

        results = [result async for result in plugin._handle_guide(event, "爱丽")]

        plugin._start_student_choice.assert_awaited_once_with(
            event,
            "爱丽",
            candidates,
            "cn",
            query_mode="guide",
        )
        self.assertEqual(results, [])

    async def test_guide_candidate_selection_fetches_guide_without_repeating_search(self):
        plugin = BAToolbox.__new__(BAToolbox)
        choice = {
            "token": "guide-token",
            "candidates": [{"Id": 1, "Name": "爱丽丝（女仆）"}],
            "language": "cn",
            "query_mode": "guide",
        }
        plugin._pending_student_queries = {"guide-token": choice}
        plugin._arona_client = SimpleNamespace(
            get_guide_image=AsyncMock(return_value="https://example.test/maid.png"),
            last_error=None,
        )
        event = SimpleNamespace(
            image_result=lambda url: ("image", url),
            plain_result=lambda text: ("text", text),
        )

        results = [
            result async for result in plugin._finish_student_choice(event, choice, 0)
        ]

        plugin._arona_client.get_guide_image.assert_awaited_once_with("爱丽丝（女仆）")
        self.assertEqual(results, [("image", "https://example.test/maid.png")])

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
                "emoji_map": {"14": {"label": "学生生日", "command": "生日"}},
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

    async def test_internal_menu_command_reaches_query_router(self):
        handled = []

        async def handle_birthday(_event, args):
            handled.append(args)
            yield "birthday result"

        self.plugin._active_menus = {
            "menu-message": {
                "group_id": "123456",
                "created_at": time.time(),
                "items": [{"label": "学生生日", "command": "生日"}],
            }
        }
        self.plugin._handle_birthday = handle_birthday
        self.plugin._execute_menu_command = AsyncMock()
        event = SimpleNamespace(
            message_str="生日",
            raw_message={"_ba_menu_internal": True},
            get_group_id=lambda: "123456",
            get_sender_id=lambda: "789",
        )

        results = [result async for result in self.plugin.router(event)]

        self.assertEqual(results, ["birthday result"])
        self.assertEqual(handled, [""])
        self.plugin._execute_menu_command.assert_not_awaited()

    async def test_slash_query_bypasses_active_menu_selection(self):
        handled = []

        async def handle_birthday(_event, args):
            handled.append(args)
            yield "birthday result"

        self.plugin._active_menus = {
            "menu-message": {
                "group_id": "123456",
                "created_at": time.time(),
                "items": [{"label": "生日", "command": "生日"}],
            }
        }
        self.plugin._handle_birthday = handle_birthday
        self.plugin._execute_menu_command = AsyncMock()
        event = SimpleNamespace(
            message_str="/生日",
            get_group_id=lambda: "123456",
            get_sender_id=lambda: "789",
        )

        results = [result async for result in self.plugin.router(event)]

        self.assertEqual(results, ["birthday result"])
        self.assertEqual(handled, [""])
        self.plugin._execute_menu_command.assert_not_awaited()

    async def test_canceling_student_choice_closes_only_candidate_menu(self):
        choice = {
            "token": "candidate-token",
            "created_at": time.time(),
            "timeout_seconds": 120,
            "candidates": [{"Id": 1, "Name": "Alice"}],
            "labels": ["Alice"],
            "language": "cn",
            "message_id": "candidate-menu",
        }
        self.plugin._pending_student_queries[choice["token"]] = choice
        self.plugin._student_choices_by_token[choice["token"]] = choice
        self.plugin._student_candidate_menus[choice["message_id"]] = choice
        self.plugin._active_menus = {
            "regular-menu": {"group_id": "123456", "created_at": time.time()}
        }
        event = SimpleNamespace(plain_result=lambda text: text)

        results = [
            result
            async for result in self.plugin._finish_student_choice(
                event, choice, "cancel"
            )
        ]

        self.assertEqual(results, ["已取消学生查询。"])
        self.assertNotIn(choice["token"], self.plugin._pending_student_queries)
        self.assertNotIn(choice["token"], self.plugin._student_choices_by_token)
        self.assertNotIn(choice["message_id"], self.plugin._student_candidate_menus)
        self.assertIn("regular-menu", self.plugin._active_menus)

    async def test_selecting_student_keeps_candidate_menu_available(self):
        choice = {
            "token": "candidate-token",
            "created_at": time.time(),
            "timeout_seconds": 120,
            "candidates": [{"Id": 1, "Name": "Alice"}],
            "labels": ["Alice"],
            "language": "cn",
            "message_id": "candidate-menu",
        }
        self.plugin._pending_student_queries[choice["token"]] = choice
        self.plugin._student_choices_by_token[choice["token"]] = choice
        self.plugin._student_candidate_menus[choice["message_id"]] = choice

        async def render_student_detail(_event, student, _language):
            yield student["Name"]

        self.plugin._render_student_detail = render_student_detail
        event = SimpleNamespace(plain_result=lambda text: text)

        for _ in range(2):
            results = [
                result
                async for result in self.plugin._finish_student_choice(event, choice, 0)
            ]
            self.assertEqual(results, ["Alice"])
            self.assertIs(self.plugin._pending_student_queries[choice["token"]], choice)
            self.assertIs(
                self.plugin._student_candidate_menus[choice["message_id"]], choice
            )

    async def test_student_choice_reply_targets_the_referenced_menu(self):
        first_choice = {
            "token": "first-token",
            "group_id": "123456",
            "user_id": "789",
            "created_at": time.time(),
            "timeout_seconds": 120,
            "message_id": "menu-one",
            "candidates": [{"Id": 1, "Name": "Alice"}],
            "labels": ["Alice"],
            "language": "cn",
            "query": "first",
        }
        second_choice = {
            **first_choice,
            "token": "second-token",
            "message_id": "menu-two",
            "query": "second",
        }
        for choice in (first_choice, second_choice):
            self.plugin._pending_student_queries[choice["token"]] = choice
            self.plugin._student_choices_by_token[choice["token"]] = choice
            self.plugin._student_candidate_menus[choice["message_id"]] = choice

        async def finish_choice(_event, choice, _action):
            yield choice["query"]

        self.plugin._finish_student_choice = finish_choice
        event = SimpleNamespace(
            message_str="1",
            message_obj=SimpleNamespace(
                message=[main_module.Reply(id="menu-two")],
                raw_message=None,
            ),
            get_group_id=lambda: "123456",
            get_sender_id=lambda: "789",
            plain_result=lambda text: text,
        )

        results = [result async for result in self.plugin.router(event)]

        self.assertEqual(results, ["second"])

    async def test_student_choice_menus_have_per_group_limit_and_custom_settings(self):
        settings = self.plugin.config["student_choice_settings"]
        settings.update(
            {"timeout_seconds": 45, "max_active_menus": 2, "face_pool": ["18", "23"]}
        )
        bot = SimpleNamespace(
            send_group_msg=AsyncMock(
                side_effect=[
                    {"message_id": "menu-one"},
                    {"message_id": "menu-two"},
                    {"message_id": "other-group-menu"},
                ]
            ),
            set_msg_emoji_like=AsyncMock(),
        )
        group_id = "123456"
        event = SimpleNamespace(
            bot=bot,
            get_group_id=lambda: group_id,
            get_sender_id=lambda: "789",
        )
        candidates = [{"Id": 1, "Name": "Alice", "_data_source_language": "cn"}]

        with patch.object(main_module.asyncio, "sleep", new_callable=AsyncMock):
            first = await self.plugin._start_student_choice(
                event, "Alice query 1", candidates, "cn"
            )
            second = await self.plugin._start_student_choice(
                event, "Alice query 2", candidates, "cn"
            )
            rejected = await self.plugin._start_student_choice(
                event, "Alice query 3", candidates, "cn"
            )
            group_id = "654321"
            other_group = await self.plugin._start_student_choice(
                event, "Alice query 4", candidates, "cn"
            )

        self.assertTrue(first[0])
        self.assertTrue(second[0])
        self.assertFalse(rejected[0])
        self.assertIn("已有 2 个", rejected[1])
        self.assertTrue(other_group[0])
        self.assertEqual(bot.send_group_msg.await_count, 3)
        self.assertEqual(
            {
                choice["message_id"]
                for choice in self.plugin._pending_student_queries.values()
            },
            {"menu-one", "menu-two", "other-group-menu"},
        )
        self.assertTrue(
            all(
                choice["timeout_seconds"] == 45
                for choice in self.plugin._pending_student_queries.values()
            )
        )
        self.assertEqual(
            self.plugin._student_candidate_menus["menu-one"]["emoji_map"],
            {"18": 0, "23": "cancel"},
        )

    def test_expired_student_choice_uses_its_configured_timeout(self):
        choice = {
            "token": "expired-token",
            "created_at": time.time() - 46,
            "timeout_seconds": 45,
            "message_id": "expired-menu",
        }
        self.plugin._pending_student_queries[choice["token"]] = choice
        self.plugin._student_choices_by_token[choice["token"]] = choice
        self.plugin._student_candidate_menus[choice["message_id"]] = choice

        self.plugin._cleanup_student_choices()

        self.assertNotIn(choice["token"], self.plugin._pending_student_queries)
        self.assertNotIn(choice["token"], self.plugin._student_choices_by_token)
        self.assertNotIn(choice["message_id"], self.plugin._student_candidate_menus)

    async def test_student_choice_attaches_unique_reactions_for_candidates_and_cancel(
        self,
    ):
        self.plugin.config["menu_settings"]["face_pool"] = ["14", "9"]
        bot = SimpleNamespace(
            send_group_msg=AsyncMock(return_value={"message_id": "candidate-menu"}),
            set_msg_emoji_like=AsyncMock(),
        )
        event = SimpleNamespace(
            bot=bot,
            get_group_id=lambda: "123456",
            get_sender_id=lambda: "789",
        )
        candidates = [
            {"Id": 1, "Name": "亚子", "_data_source_language": "cn"},
            {"Id": 2, "Name": "亚子（礼服）", "_data_source_language": "cn"},
        ]

        with patch.object(main_module.asyncio, "sleep", new_callable=AsyncMock):
            sent, prompt = await self.plugin._start_student_choice(
                event, "亚子", candidates, "cn"
            )

        self.assertTrue(sent)
        self.assertIn("1. 亚子（国服数据）", prompt)
        self.assertIn("2. 亚子（礼服）（国服数据）", prompt)
        self.assertIn("3. 取消查询", prompt)
        calls = bot.set_msg_emoji_like.await_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(
            [call.kwargs["emoji_id"] for call in calls],
            [14, 9, 5],
        )
        choice = self.plugin._student_candidate_menus["candidate-menu"]
        self.assertEqual(choice["emoji_map"], {"14": 0, "9": 1, "5": "cancel"})

    async def test_failed_candidate_reaction_does_not_shift_action_mapping(self):
        self.plugin.config["menu_settings"]["face_pool"] = ["14", "9"]
        bot = SimpleNamespace(
            send_group_msg=AsyncMock(return_value={"message_id": "candidate-menu"}),
            set_msg_emoji_like=AsyncMock(
                side_effect=[RuntimeError("unsupported emoji"), None, None, None]
            ),
        )
        event = SimpleNamespace(
            bot=bot,
            get_group_id=lambda: "123456",
            get_sender_id=lambda: "789",
        )
        candidates = [
            {"Id": 1, "Name": "亚子", "_data_source_language": "cn"},
            {"Id": 2, "Name": "亚子（礼服）", "_data_source_language": "cn"},
        ]

        with patch.object(main_module.asyncio, "sleep", new_callable=AsyncMock):
            sent, _ = await self.plugin._start_student_choice(
                event, "亚子", candidates, "cn"
            )

        self.assertTrue(sent)
        choice = self.plugin._student_candidate_menus["candidate-menu"]
        self.assertEqual(choice["emoji_map"], {"9": 0, "5": 1, "10": "cancel"})

    async def test_candidate_reaction_dispatches_selection_from_any_user(self):
        choice = {
            "token": "candidate-token",
            "created_at": time.time() - 3,
            "timeout_seconds": 120,
            "user_id": "789",
            "group_id": "123456",
            "message_id": "candidate-menu",
            "emoji_map": {"14": 0},
            "candidates": [{"Id": 1, "Name": "Alice"}],
            "labels": ["Alice"],
            "language": "cn",
        }
        self.plugin._pending_student_queries[choice["token"]] = choice
        self.plugin._student_choices_by_token[choice["token"]] = choice
        self.plugin._student_candidate_menus[choice["message_id"]] = choice
        self.plugin._menu_debounce = {}
        self.plugin._execute_menu_command = AsyncMock(return_value=True)
        self.plugin._stop_menu_event = lambda _event: None
        event = SimpleNamespace()
        raw = {
            "notice_type": "group_msg_emoji_like",
            "message_id": "candidate-menu",
            "user_id": "456",
            "emoji_id": "14",
        }

        handled = await self.plugin._handle_menu_reaction(event, raw)

        self.assertTrue(handled)
        self.plugin._execute_menu_command.assert_awaited_once_with(
            event,
            "456",
            "123456",
            "__ba_student_choice__",
            internal_payload={
                "_ba_student_choice_token": "candidate-token",
                "_ba_student_choice_action": 0,
                "_ba_student_choice_actor_id": "456",
            },
        )


if __name__ == "__main__":
    unittest.main()
