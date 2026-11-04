from __future__ import annotations

import importlib
import json
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
StudentCandidateMatches = importlib.import_module(
    "astrbot_plugin_bluearchive_toolbox.api_clients.schaledb_client"
).StudentCandidateMatches
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
                "student_choice_timeout_seconds": 120,
                "student_choice_max_active_menus": 3,
                "student_choice_face_pool": [],
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

    def test_reads_user_aliases_from_student_search_settings(self):
        self.plugin.config["student_search_settings"] = {
            "custom_aliases": ["啊露=爱露", "泳装星野=星野", "无效条目"],
            "arona_form_aliases": ["新年=和服", "泳装=水着", "无效条目"],
            "llm_enabled": True,
        }

        self.assertEqual(
            self.plugin._student_search_aliases(),
            {"啊露": "爱露", "泳装星野": "星野"},
        )
        self.assertEqual(
            self.plugin._arona_form_aliases(),
            {"新年": "和服", "泳装": "水着"},
        )
        self.assertTrue(self.plugin._student_llm_enabled())

    def test_schema_preloads_arona_form_aliases(self):
        schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        aliases = schema["student_search_settings"]["items"][
            "arona_form_aliases"
        ]["default"]

        self.assertIn("新年=正月", aliases)
        self.assertIn("レーシング=赛车", aliases)
        self.assertIn("武装=临战", aliases)
        self.assertIn("邦妮=兔女郎", aliases)

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

    def test_default_menu_labels_add_emojis_without_changing_commands(self):
        schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        menu_items = schema["menu_settings"]["items"]["menu_items"]["default"]

        self.assertEqual(
            [(item["label"], item["command"]) for item in menu_items],
            [
                ("👤 学生详情", "学生"),
                ("🎂 学生生日", "生日"),
                ("🔮 卡池千里眼", "千里眼"),
                ("📅 活动日程", "活动"),
                ("📖 攻略查询", "攻略"),
            ],
        )

    def test_reaction_menu_uses_configurable_footer_and_divider(self):
        self.plugin.config["menu_settings"].update(
            {
                "menu_title": "自定义菜单",
                "menu_divider_char": "=",
                "menu_divider_length": 4,
                "menu_footer_text": "自定义说明",
                "menu_items": [{"label": "生日", "command": "生日"}],
            }
        )

        self.assertEqual(
            self.plugin._build_reaction_menu_text(),
            "自定义菜单\n====\n  1. 生日\n====\n自定义说明",
        )

    async def test_student_choice_menu_uses_shared_divider_and_custom_footer(self):
        self.plugin.config["menu_settings"].update(
            {
                "menu_divider_char": "=",
                "menu_divider_length": 4,
                "student_choice_title": "🎓 自定义学生选择",
                "student_choice_footer_text": "候选菜单说明",
            }
        )
        bot = SimpleNamespace(
            send_group_msg=AsyncMock(return_value={"message_id": "candidate-menu"}),
            set_msg_emoji_like=AsyncMock(),
        )
        event = SimpleNamespace(
            bot=bot,
            get_group_id=lambda: "123456",
            get_sender_id=lambda: "789",
        )
        with patch.object(main_module.asyncio, "sleep", new_callable=AsyncMock):
            sent, _ = await self.plugin._start_student_choice(
                event,
                "Alice",
                [{"Id": 1, "Name": "Alice", "_data_source_language": "cn"}],
                "cn",
                primary_name="Alice",
            )

        self.assertTrue(sent)
        self.assertEqual(
            bot.send_group_msg.await_args.kwargs["message"],
            "🎓 自定义学生选择\n"
            "====\n"
            "「Alice」最接近「Alice」，以下是该学生的形态，请选择：\n"
            "====\n"
            "1. Alice（国服数据）\n"
            "2. 取消查询\n"
            "====\n"
            "候选菜单说明",
        )

    async def test_student_choice_labels_use_release_region_not_data_language(self):
        bot = SimpleNamespace(
            send_group_msg=AsyncMock(return_value={"message_id": "candidate-menu"}),
            set_msg_emoji_like=AsyncMock(),
        )
        event = SimpleNamespace(
            bot=bot,
            get_group_id=lambda: "123456",
            get_sender_id=lambda: "789",
        )
        with patch.object(main_module.asyncio, "sleep", new_callable=AsyncMock):
            sent, _ = await self.plugin._start_student_choice(
                event,
                "日富美",
                [
                    {
                        "Id": 10153,
                        "Name": "日富美（赛车）",
                        "_data_source_language": "cn",
                        "_released_regions": ["日服"],
                    }
                ],
                "cn",
                primary_name="日富美",
            )

        self.assertTrue(sent)
        message = bot.send_group_msg.await_args.kwargs["message"]
        self.assertIn("1. 日富美（赛车）（日服实装）", message)

    def test_student_release_label_collapses_all_servers_and_lists_partial_release(self):
        self.assertEqual(
            self.plugin._student_release_label(
                {"_released_regions": ["日服", "国际服", "国服"]}
            ),
            "全服",
        )
        self.assertEqual(
            self.plugin._student_release_label(
                {"_released_regions": ["日服", "国际服"]}
            ),
            "日服、国际服实装",
        )
        self.assertEqual(
            self.plugin._student_release_label({"_released_regions": ["日服"]}),
            "日服实装",
        )
        self.assertEqual(
            self.plugin._student_release_label({"_released_regions": []}),
            "未实装",
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

    async def test_student_detail_asks_confirmation_for_approximate_suggestions(self):
        plugin = BAToolbox.__new__(BAToolbox)
        plugin.config = {"basic_settings": {"student_data_language": "cn"}}
        candidates = StudentCandidateMatches(
            [{"Id": 1, "Name": "星野"}, {"Id": 2, "Name": "星野（泳装）"}],
            primary_name="星野",
            recommendations=["日鞠"],
            needs_confirmation=True,
        )
        plugin._schaledb_client = SimpleNamespace(
            search_student_candidates=AsyncMock(return_value=candidates)
        )
        plugin._arona_client = SimpleNamespace(
            get_guide_image=AsyncMock(
                side_effect=[
                    "https://example.test/hoshino.png",
                    "https://example.test/swimsuit.png",
                ]
            ),
            last_error=None,
        )
        plugin._start_student_choice = AsyncMock(return_value=(True, "菜单"))
        event = SimpleNamespace(unified_msg_origin="", plain_result=lambda text: text)

        results = [
            result async for result in plugin._handle_student_detail(event, "星也")
        ]

        plugin._start_student_choice.assert_awaited_once_with(
            event,
            "星也",
            candidates,
            "cn",
            suggestions=True,
            primary_name="星野",
            recommendations=["日鞠"],
        )
        self.assertEqual(results, [])

    async def test_birthday_student_query_returns_plain_text(self):
        self.plugin._schaledb_client = SimpleNamespace(
            last_error=None,
            get_birthdays_with_ids=AsyncMock(
                return_value=[
                    ("星野", "1/2", 1),
                    ("日富美", "11/27", 2),
                ]
            ),
        )
        event = SimpleNamespace(plain_result=lambda text: text)

        results = [
            result async for result in self.plugin._handle_birthday(event, "星野")
        ]

        self.assertEqual(results, ["星野的生日是 1/2。"])

    async def test_birthday_month_query_renders_only_that_month(self):
        self.plugin.config["basic_settings"]["message_mode"] = "card"
        self.plugin._schaledb_client = SimpleNamespace(
            last_error=None,
            asset_errors=[],
            get_birthdays_with_ids=AsyncMock(
                return_value=[
                    ("星野", "1/2", 1),
                    ("日富美", "1/27", 2),
                    ("梓", "2/1", 3),
                ]
            ),
            get_student_icons_by_id=AsyncMock(return_value={"1": "hoshino.webp"}),
        )
        self.plugin._render_in_thread = AsyncMock(return_value=b"calendar")
        self.plugin._store_card = lambda _data: "birthday.png"
        event = SimpleNamespace(
            plain_result=lambda text: text,
            image_result=lambda path: path,
        )

        with patch.object(main_module, "BirthdayCard") as card_type:
            results = [
                result async for result in self.plugin._handle_birthday(event, "1月")
            ]

        card_type.assert_called_once()
        self.assertEqual(
            card_type.call_args.kwargs["students"],
            [("星野", "1/2"), ("日富美", "1/27")],
        )
        self.assertEqual(card_type.call_args.kwargs["month"], 1)
        self.assertEqual(card_type.call_args.kwargs["student_icons"], {"星野": "hoshino.webp"})
        self.assertEqual(results, ["birthday.png"])

    async def test_birthday_month_query_rejects_invalid_month(self):
        self.plugin._schaledb_client = SimpleNamespace(
            get_birthdays_with_ids=AsyncMock()
        )
        event = SimpleNamespace(plain_result=lambda text: text)

        results = [
            result async for result in self.plugin._handle_birthday(event, "13月")
        ]

        self.assertEqual(results, ["月份无效，请输入 1月 到 12月。"])
        self.plugin._schaledb_client.get_birthdays_with_ids.assert_not_awaited()

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

    async def test_guide_adapts_form_name_before_exact_arona_lookup(self):
        plugin = BAToolbox.__new__(BAToolbox)
        plugin.config = {"basic_settings": {"student_data_language": "cn"}}
        plugin._schaledb_client = SimpleNamespace(
            search_student_candidates=AsyncMock(
                return_value=[{"Id": 10066, "PathName": "aris_maid", "Name": "爱丽丝（女仆）"}]
            )
        )
        plugin._arona_client = SimpleNamespace(
            get_guide_image=AsyncMock(return_value="https://example.test/maid.png"),
            last_error=None,
        )
        event = SimpleNamespace(
            unified_msg_origin="",
            image_result=lambda url: ("image", url),
            plain_result=lambda text: ("text", text),
        )

        results = [
            result async for result in plugin._handle_guide(event, "爱丽丝（女仆）")
        ]

        plugin._arona_client.get_guide_image.assert_awaited_once_with("女仆爱丽丝")
        self.assertEqual(results, [("image", "https://example.test/maid.png")])

    async def test_guide_uses_configured_form_alias_before_arona_lookup(self):
        plugin = BAToolbox.__new__(BAToolbox)
        plugin.config = {
            "student_search_settings": {"arona_form_aliases": ["新年=和服"]}
        }
        plugin._arona_client = SimpleNamespace(
            get_guide_image=AsyncMock(return_value="https://example.test/aru.png"),
            last_error=None,
        )

        candidates = await plugin._resolve_guide_candidates(
            [{"Id": 1, "PathName": "aru_newyear", "Name": "爱露（新年）"}]
        )

        plugin._arona_client.get_guide_image.assert_awaited_once_with("和服爱露")
        self.assertEqual(candidates[0]["_arona_guide_name"], "和服爱露")
        self.assertEqual(
            candidates[0]["_arona_guide_url"],
            "https://example.test/aru.png",
        )

    async def test_guide_asks_confirmation_for_approximate_student_suggestions(self):
        plugin = BAToolbox.__new__(BAToolbox)
        plugin.config = {"basic_settings": {"student_data_language": "cn"}}
        candidates = StudentCandidateMatches(
            [{"Id": 1, "Name": "星野"}, {"Id": 2, "Name": "星野（泳装）"}],
            primary_name="星野",
            recommendations=["日鞠"],
            needs_confirmation=True,
        )
        plugin._schaledb_client = SimpleNamespace(
            search_student_candidates=AsyncMock(return_value=candidates)
        )
        plugin._arona_client = SimpleNamespace(
            get_guide_image=AsyncMock(
                side_effect=[
                    "https://example.test/hoshino.png",
                    "https://example.test/swimsuit.png",
                ]
            ),
            last_error=None,
        )
        plugin._start_student_choice = AsyncMock(return_value=(True, "菜单"))
        event = SimpleNamespace(unified_msg_origin="", plain_result=lambda text: text)

        results = [result async for result in plugin._handle_guide(event, "星也")]

        plugin._start_student_choice.assert_awaited_once()
        call_args = plugin._start_student_choice.await_args
        self.assertEqual(call_args.args[:2], (event, "星也"))
        self.assertEqual(call_args.args[3], "cn")
        self.assertEqual(call_args.kwargs["query_mode"], "guide")
        self.assertTrue(call_args.kwargs["suggestions"])
        self.assertEqual(call_args.kwargs["primary_name"], "星野")
        self.assertEqual(call_args.kwargs["recommendations"], ["日鞠"])
        menu_candidates = plugin._start_student_choice.await_args.args[2]
        self.assertEqual(
            [candidate["Name"] for candidate in menu_candidates],
            ["星野", "星野（泳装）"],
        )
        self.assertEqual(
            [candidate["_arona_guide_url"] for candidate in menu_candidates],
            [
                "https://example.test/hoshino.png",
                "https://example.test/swimsuit.png",
            ],
        )
        self.assertTrue(menu_candidates.needs_confirmation)
        self.assertEqual(results, [])

    async def test_ambiguous_guide_search_starts_guide_candidate_menu(self):
        plugin = BAToolbox.__new__(BAToolbox)
        plugin.config = {"basic_settings": {"student_data_language": "cn"}}
        candidates = [
            {"Id": 1, "Name": "爱丽丝"},
            {
                "Id": 2,
                "PathName": "aris_maid",
                "Name": "爱丽丝（女仆）",
            },
        ]
        plugin._schaledb_client = SimpleNamespace(
            search_student_candidates=AsyncMock(return_value=candidates)
        )
        plugin._arona_client = SimpleNamespace(
            get_guide_image=AsyncMock(
                side_effect=[
                    "https://example.test/alice.png",
                    "https://example.test/maid.png",
                ]
            ),
            last_error=None,
        )
        plugin._start_student_choice = AsyncMock(return_value=(True, "菜单"))
        event = SimpleNamespace(unified_msg_origin="", plain_result=lambda text: text)

        results = [result async for result in plugin._handle_guide(event, "爱丽")]

        plugin._arona_client.get_guide_image.assert_has_awaits(
            [call("爱丽丝"), call("女仆爱丽丝")]
        )
        menu_args = plugin._start_student_choice.await_args.args
        self.assertEqual(
            [candidate["Name"] for candidate in menu_args[2]],
            ["爱丽丝", "爱丽丝（女仆）"],
        )
        self.assertEqual(
            [candidate["_arona_guide_name"] for candidate in menu_args[2]],
            ["爱丽丝", "女仆爱丽丝"],
        )
        self.assertEqual(plugin._start_student_choice.await_args.kwargs["query_mode"], "guide")
        self.assertEqual(results, [])

    async def test_guide_candidate_selection_fetches_guide_without_repeating_search(self):
        plugin = BAToolbox.__new__(BAToolbox)
        choice = {
            "token": "guide-token",
            "candidates": [
                {
                    "Id": 1,
                    "PathName": "aris_maid",
                    "Name": "爱丽丝（女仆）",
                    "_arona_guide_name": "女仆爱丽丝",
                    "_arona_guide_url": "https://example.test/maid.png",
                }
            ],
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

        plugin._arona_client.get_guide_image.assert_not_awaited()
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
        settings = self.plugin.config["menu_settings"]
        settings.update(
            {
                "student_choice_timeout_seconds": 45,
                "student_choice_max_active_menus": 2,
                "student_choice_face_pool": ["18", "23"],
            }
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
                event,
                "亚子",
                candidates,
                "cn",
                primary_name="亚子",
                recommendations=["爱丽丝"],
            )

        self.assertTrue(sent)
        self.assertIn("最接近「亚子」", prompt)
        self.assertIn("1. 亚子（国服数据）", prompt)
        self.assertIn("2. 亚子（礼服）（国服数据）", prompt)
        self.assertIn("其他相似角色", prompt)
        self.assertIn("爱丽丝", prompt)
        self.assertIn("3. 取消查询", prompt)
        calls = bot.set_msg_emoji_like.await_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(
            [call.kwargs["emoji_id"] for call in calls],
            [14, 9, 5],
        )
        choice = self.plugin._student_candidate_menus["candidate-menu"]
        self.assertEqual(choice["emoji_map"], {"14": 0, "9": 1, "5": "cancel"})

    async def test_student_choice_can_show_optional_llm_assistance(self):
        self.plugin.config["student_search_settings"] = {"llm_enabled": True}
        bot = SimpleNamespace(
            send_group_msg=AsyncMock(return_value={"message_id": "candidate-menu"}),
            set_msg_emoji_like=AsyncMock(),
        )
        event = SimpleNamespace(
            bot=bot,
            get_group_id=lambda: "123456",
            get_sender_id=lambda: "789",
        )
        candidates = [{"Id": 1, "Name": "星野", "_data_source_language": "cn"}]

        with patch.object(main_module.asyncio, "sleep", new_callable=AsyncMock):
            sent, prompt = await self.plugin._start_student_choice(
                event, "星也", candidates, "cn"
            )

        self.assertTrue(sent)
        self.assertIn("大模型辅助匹配（可略微提升准确性）", prompt)
        self.assertIn("3. 取消查询", prompt)
        choice = self.plugin._student_candidate_menus["candidate-menu"]
        self.assertEqual(choice["emoji_map"], {"14": 0, "9": "llm", "5": "cancel"})
        self.assertEqual(self.plugin._match_student_choice(choice, "2"), "llm")
        self.assertEqual(self.plugin._match_student_choice(choice, "3"), "cancel")

    async def test_llm_ranking_uses_only_validated_local_candidates(self):
        self.plugin.config["student_search_settings"] = {
            "llm_enabled": True,
            "llm_provider_id": "fast-model",
        }
        selected_forms = [
            {"Id": 10000, "Name": "爱露", "_data_source_language": "cn"},
            {"Id": 10089, "Name": "爱露（礼服）", "_data_source_language": "cn"},
        ]
        identities = [
            {
                "candidate_id": "1",
                "name": "星野",
                "local_score": 72,
                "variants": ["星野", "星野（泳装）"],
                "forms": [{"Id": 10005, "Name": "星野"}],
            },
            {
                "candidate_id": "2",
                "name": "爱露",
                "local_score": 60,
                "variants": ["爱露", "爱露（礼服）"],
                "forms": selected_forms,
            },
        ]
        response_text = json.dumps(
            {
                "ranking": [
                    {"candidate_id": "2", "name": "爱露", "score": 96},
                    {"candidate_id": "1", "name": "星野", "score": 71},
                ]
            },
            ensure_ascii=False,
        )
        self.plugin.context = SimpleNamespace(
            llm_generate=AsyncMock(
                return_value=SimpleNamespace(completion_text=response_text)
            )
        )
        choice = {
            "token": "parent-token",
            "group_id": "123456",
            "user_id": "789",
            "created_at": time.time(),
            "timeout_seconds": 120,
            "query": "礼服小春",
            "query_mode": "detail",
            "candidates": [],
            "labels": [],
            "language": "cn",
            "message_id": "parent-menu",
            "ranked_identities": identities,
            "llm_enabled": True,
        }
        self.plugin._pending_student_queries[choice["token"]] = choice
        self.plugin._student_choices_by_token[choice["token"]] = choice
        self.plugin._student_candidate_menus[choice["message_id"]] = choice
        bot = SimpleNamespace(
            send_group_msg=AsyncMock(return_value={"message_id": "refined-menu"}),
            set_msg_emoji_like=AsyncMock(),
        )
        event = SimpleNamespace(
            bot=bot,
            get_group_id=lambda: "123456",
            get_sender_id=lambda: "789",
            plain_result=lambda text: text,
        )

        with patch.object(main_module.asyncio, "sleep", new_callable=AsyncMock):
            results = [
                result
                async for result in self.plugin._finish_student_choice(
                    event, choice, "llm"
                )
            ]

        self.assertIn("大模型认为最可能是「爱露」（参考匹配度 96%）", results[0])
        self.assertIn("星野", results[0])
        sent_prompt = bot.send_group_msg.await_args.kwargs["message"]
        self.assertIn("爱露（礼服）", sent_prompt)
        self.assertNotIn("星野（泳装）", sent_prompt)
        self.assertNotIn(choice["token"], self.plugin._pending_student_queries)
        refined_choice = next(
            item
            for item in self.plugin._pending_student_queries.values()
            if item["message_id"] == "refined-menu"
        )
        self.assertEqual(
            {student["Id"] for student in refined_choice["candidates"]},
            {10000, 10089},
        )

    async def test_invalid_llm_ranking_keeps_original_candidate_menu(self):
        self.plugin.config["student_search_settings"] = {
            "llm_enabled": True,
            "llm_provider_id": "fast-model",
        }
        self.plugin.context = SimpleNamespace(
            llm_generate=AsyncMock(
                return_value=SimpleNamespace(completion_text="not JSON")
            )
        )
        choice = {
            "token": "candidate-token",
            "group_id": "123456",
            "created_at": time.time(),
            "timeout_seconds": 120,
            "candidates": [],
            "labels": [],
            "language": "cn",
            "query": "星也",
            "ranked_identities": [
                {
                    "candidate_id": "1",
                    "name": "星野",
                    "local_score": 70,
                    "variants": ["星野"],
                    "forms": [{"Id": 1, "Name": "星野"}],
                }
            ],
            "llm_enabled": True,
        }
        self.plugin._pending_student_queries[choice["token"]] = choice
        event = SimpleNamespace(plain_result=lambda text: text)

        results = [
            result
            async for result in self.plugin._finish_student_choice(
                event, choice, "llm"
            )
        ]

        self.assertIn("原候选菜单仍然有效", results[0])
        self.assertIs(
            self.plugin._pending_student_queries[choice["token"]], choice
        )

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
