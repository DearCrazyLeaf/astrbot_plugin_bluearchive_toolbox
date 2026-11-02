from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain, Reply
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.exceptions import ProviderNotFoundError
from astrbot.core.message.message_event_result import MessageChain

try:
    from astrbot.api.platform import AstrBotMessage, MessageMember
except ImportError:
    AstrBotMessage = None
    MessageMember = None

from .api_clients.arona_client import AronaAmbiguousError, AronaClient
from .api_clients.cache_manager import ImageCacheManager
from .api_clients.gamekee import GameKeeClient
from .api_clients.schaledb_client import (
    SchaleDBClient,
    SchaleDBError,
)
from .poll_archive import PollArchive
from .renderers.card import (
    BirthdayCard,
    CardPoolCard,
    ScheduleCard,
    parse_birthday_date,
)
from .renderers.card_fonts import prepare_card_fonts
from .renderers.html_renderer import configure_font_cache_dir, is_chromium_installed
from .renderers.student_detail import render_student_detail

PLUGIN_ID = "astrbot_plugin_bluearchive_toolbox"
LOG_PREFIX = "[Ba ToolBox]"
STUDENT_CHOICE_TIMEOUT = 120
MAX_STUDENT_CHOICES = 10
STUDENT_CHOICE_FALLBACK_FACE_POOL = (
    "5",
    "9",
    "10",
    "14",
    "18",
    "21",
    "23",
    "25",
    "27",
    "29",
    "32",
    "49",
    "53",
    "55",
    "59",
    "60",
    "63",
    "66",
    "74",
    "79",
    "89",
    "96",
    "99",
    "100",
    "105",
    "120",
    "123",
    "124",
    "129",
    "144",
    "147",
    "171",
    "175",
    "179",
    "182",
    "183",
    "201",
)


class BAToolbox(Star):
    """
    BA 工具箱插件。

    支持功能：
      1. 学生详情查询（SchaleDB 完整学生数据，HTML 渲染）
      2. 活动日程（GameKee indexV2）
      3. 卡池千里眼（GameKee cardPool）
      4. 学生生日（SchaleDB）
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._data_dir: Path = StarTools.get_data_dir(PLUGIN_ID)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir = self._data_dir / "cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self.poll_archive = PollArchive(self._data_dir / "poll_archive")
        self._poll_task: asyncio.Task | None = None
        self._active_menus: dict[str, dict[str, Any]] = {}
        self._menu_debounce: dict[tuple[str, str, str], float] = {}
        self._pending_student_queries: dict[str, dict[str, Any]] = {}
        self._student_choices_by_token: dict[str, dict[str, Any]] = {}
        self._student_candidate_menus: dict[str, dict[str, Any]] = {}
        # 图片缓存管理器
        cache_cfg = self.config.get("cache_settings", {})
        max_items = int(cache_cfg.get("max_items", 200))
        max_size_mb = int(cache_cfg.get("max_size_mb", 500))
        ttl_days = int(cache_cfg.get("ttl_days", 7))
        self.image_cache = ImageCacheManager(
            cache_dir=self._cache_dir,
            max_items=max_items,
            max_size_mb=max_size_mb,
            ttl_seconds=ttl_days * 86400,
        )
        self.image_cache.cleanup_expired()

        # API 客户端（延迟初始化）
        self._schaledb_client: SchaleDBClient | None = None
        self._arona_client: AronaClient | None = None
        self._gamekee_client: GameKeeClient | None = None
        if context is not None:
            from .webui import BAToolboxWebUI

            self._webui = BAToolboxWebUI(self)
            self._webui.register()

    # ------------------------------------------------------------------
    # API 客户端属性
    # ------------------------------------------------------------------

    @property
    def schaledb(self) -> SchaleDBClient:
        if self._schaledb_client is None:
            self._schaledb_client = SchaleDBClient(
                cache_dir=self._cache_dir / "schaledb",
                base_url=str(self._get_setting("schaledb_base_url") or "").strip()
                or None,
            )
        return self._schaledb_client

    @property
    def arona(self) -> AronaClient:
        if self._arona_client is None:
            self._arona_client = AronaClient()
        return self._arona_client

    @property
    def gamekee(self) -> GameKeeClient:
        if self._gamekee_client is None:
            self._gamekee_client = GameKeeClient(
                base_url=str(self._get_setting("api_base_url", "")) or None,
            )
        return self._gamekee_client

    # ------------------------------------------------------------------
    # AstrBot 生命周期钩子
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        try:
            await self._ensure_playwright_chromium()
        except Exception as exc:
            logger.error(
                f"{LOG_PREFIX}[renderer] Chromium 自动安装失败；"
                "卡片渲染暂不可用，请检查网络和写入权限，"
                f"或手动运行 `{sys.executable} -m playwright install chromium`：{exc}"
            )
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())
        if not bool(self._get_setting("download_full_font", False)):
            configure_font_cache_dir(None)
            logger.info(
                f"{LOG_PREFIX}[fonts] 使用插件内置精简字体；"
                "可在配置中开启完整字体自动下载"
            )
            return

        fonts, errors = await prepare_card_fonts(self._data_dir / "fonts")
        if fonts:
            logger.info(
                f"{LOG_PREFIX}[fonts] 已准备卡片字体: {', '.join(sorted(fonts))}"
            )
        for filename, error in errors.items():
            logger.warning(
                f"{LOG_PREFIX}[fonts] 无法准备 {filename}: {error}；将回退到插件内置字体"
            )

    async def _ensure_playwright_chromium(self) -> None:
        if await asyncio.to_thread(is_chromium_installed):
            logger.info(f"{LOG_PREFIX}[renderer] Playwright Chromium 已安装")
            return

        logger.info(
            f"{LOG_PREFIX}[renderer] 未检测到 Playwright Chromium，正在自动安装"
        )
        result = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "playwright",
                "install",
                "chromium",
            ],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if result.returncode:
            details = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(
                f"安装命令退出码 {result.returncode}"
                + (f"：{details[-2000:]}" if details else "")
            )
        if not await asyncio.to_thread(is_chromium_installed):
            raise RuntimeError("安装命令已结束，但仍未找到 Chromium 可执行文件")
        logger.info(f"{LOG_PREFIX}[renderer] Playwright Chromium 自动安装完成")

    async def terminate(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        for client in (self._schaledb_client, self._arona_client, self._gamekee_client):
            if client:
                await client.close()

    def _get_setting(self, key: str, default: Any = None) -> Any:
        for group in (
            "basic_settings",
            "command_settings",
            "cache_settings",
            "polling_settings",
            "debug_settings",
        ):
            settings = self.config.get(group)
            if isinstance(settings, dict) and key in settings:
                return settings[key]
        return self.config.get(key, default)

    # ------------------------------------------------------------------
    # Debug logging
    # ------------------------------------------------------------------

    def _debug_enabled(self) -> bool:
        return bool(self._get_setting("debug_log", False))

    def _log_debug(self, stage: str, msg: str, **kwargs: Any) -> None:
        if not self._debug_enabled():
            return
        kv = " ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
        if kv:
            logger.info(f"{LOG_PREFIX}[{stage}] {msg} | {kv}")
        else:
            logger.info(f"{LOG_PREFIX}[{stage}] {msg}")

    def _log_warn(self, stage: str, msg: str, **kwargs: Any) -> None:
        kv = " ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
        if kv:
            logger.warning(f"{LOG_PREFIX}[{stage}] {msg} | {kv}")
        else:
            logger.warning(f"{LOG_PREFIX}[{stage}] {msg}")

    def _log_error(self, stage: str, msg: str, **kwargs: Any) -> None:
        kv = " ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
        if kv:
            logger.error(f"{LOG_PREFIX}[{stage}] {msg} | {kv}")
        else:
            logger.error(f"{LOG_PREFIX}[{stage}] {msg}")

    def _store_card(self, card_bytes: bytes) -> Path:
        return self.image_cache.put(hashlib.sha256(card_bytes).hexdigest(), card_bytes)

    def _poll_push_targets(self) -> list[str]:
        configured = self._get_setting("umo_groups", [])
        if not isinstance(configured, (list, tuple)):
            return []

        targets = []
        for entry in configured:
            if not isinstance(entry, str):
                continue
            umo, separator, region = entry.rpartition(":")
            if (
                separator
                and region.strip().lower() in ("cn", "global", "jp", "all")
                and umo.strip()
            ):
                parts = umo.strip().split(":", 2)
                if len(parts) == 3 and parts[1] == "GroupMessage":
                    targets.append(umo.strip())
        return list(dict.fromkeys(targets))

    async def _push_poll_card(
        self, category: str, card_bytes: bytes, targets: list[str]
    ) -> list[dict[str, str]]:
        results = []
        for target in targets:
            try:
                sent = await self.context.send_message(
                    session=target,
                    message_chain=MessageChain([Image.fromBytes(card_bytes)]),
                )
                if not sent:
                    raise RuntimeError("未找到可用的平台会话")
            except Exception as exc:
                self._log_warn(
                    "poll",
                    "failed to push polling card",
                    category=category,
                    target=target,
                    error=exc,
                )
                results.append(
                    {"target": target, "status": "failed", "error": str(exc)[:500]}
                )
            else:
                results.append({"target": target, "status": "sent", "error": ""})
        return results

    @staticmethod
    async def _render_in_thread(render: Callable[..., bytes], *args: Any) -> bytes:
        return await asyncio.to_thread(render, *args)

    async def _poll_loop(self) -> None:
        while True:
            try:
                if bool(self._get_setting("enabled", True)):
                    poll_time = str(self._get_setting("poll_time", "08:00"))
                    if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", poll_time):
                        now = datetime.now().astimezone()
                        archive_date = now.date().isoformat()
                        if now.strftime(
                            "%H:%M"
                        ) >= poll_time and not self.poll_archive.has_entries(
                            archive_date
                        ):
                            await self._run_daily_poll(now)
                    else:
                        self._log_warn(
                            "poll",
                            "invalid poll time; daily polling skipped",
                            value=poll_time,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log_error("poll", "daily polling loop failed", error=exc)
            await asyncio.sleep(30)

    async def _run_daily_poll(self, now: datetime | None = None) -> None:
        poll_at = (now or datetime.now().astimezone()).astimezone()
        archive_date = poll_at.date().isoformat()
        run_id = poll_at.strftime("%Y%m%dT%H%M%S")
        region = str(self._get_setting("poll_region", "all")).strip().lower()
        if region not in ("cn", "global", "jp", "all"):
            self._log_warn(
                "poll", "invalid poll region; using all servers", value=region
            )
            region = "all"
        configured_categories = self._get_setting(
            "poll_categories", ["schedule", "birthday", "pool"]
        )
        if not isinstance(configured_categories, (list, tuple)):
            configured_categories = ["schedule", "birthday", "pool"]
        categories = [
            category
            for category in ("schedule", "birthday", "pool")
            if category in configured_categories
        ]
        if not categories:
            self._log_warn("poll", "no valid polling categories configured")
            return

        retention_days = self._get_setting("retention_days", 30)
        try:
            retention_days = int(retention_days)
            self.poll_archive.cleanup(retention_days, poll_at.date())
        except (TypeError, ValueError, OSError) as exc:
            self._log_warn("poll", "archive retention cleanup failed", error=exc)

        push_targets = self._poll_push_targets()
        for category in categories:
            card_bytes = None
            item_count = 0
            error = None
            delivery_results = []
            try:
                card_bytes, item_count = await self._render_poll_card(category, region)
            except Exception as exc:
                error = str(exc) or type(exc).__name__
                self._log_error(
                    "poll",
                    "card polling failed",
                    category=category,
                    region=region,
                    error=exc,
                )
            self.poll_archive.save_result(
                archive_date=archive_date,
                run_id=run_id,
                category=category,
                region=region,
                item_count=item_count,
                image=card_bytes,
                error=error,
                delivery_results=delivery_results,
                has_push_targets=bool(push_targets),
            )
            if card_bytes is not None and push_targets:
                delivery_results = await self._push_poll_card(
                    category, card_bytes, push_targets
                )
                self.poll_archive.update_delivery(
                    archive_date=archive_date,
                    category=category,
                    run_id=run_id,
                    delivery_results=delivery_results,
                )
        self._log_debug(
            "poll",
            "daily card polling completed",
            date=archive_date,
            categories=",".join(categories),
            region=region,
        )

    async def _render_poll_card(self, category: str, region: str) -> tuple[bytes, int]:
        if category == "schedule":
            events = await self.gamekee.get_activity_calendar(region)
            if self.gamekee.last_error:
                raise RuntimeError(self.gamekee.last_error)
            card = ScheduleCard(events=events, region=region)
            return await self._render_in_thread(card.render), len(events)

        if category == "pool":
            pools = await self.gamekee.get_card_pools(region)
            if self.gamekee.last_error:
                raise RuntimeError(self.gamekee.last_error)
            student_names = list(
                dict.fromkeys(
                    name
                    for pool in pools
                    for name in pool.get("students", [])
                    if isinstance(name, str) and name
                )
            )
            try:
                student_icons = await self.schaledb.get_student_icon_map(
                    student_names, "cn"
                )
            except SchaleDBError as exc:
                self._log_warn("poll", "student portraits unavailable", error=exc)
                student_icons = {}
            card = CardPoolCard(pools=pools, region=region, student_icons=student_icons)
            return await self._render_in_thread(card.render), len(pools)

        if category == "birthday":
            language = str(self._get_setting("student_data_language") or "cn")
            records = await self.schaledb.get_birthdays_with_ids(language)
            now = datetime.now()
            current_month = now.month
            current_year = now.year
            students = [
                (name, birthday)
                for name, birthday, _ in records
                if (birthday_date := parse_birthday_date(birthday))
                and birthday_date[0] == current_month
            ]
            student_ids = {
                name: student_id for name, _, student_id in records
            }
            icons_by_id = await self.schaledb.get_student_icons_by_id(
                [
                    student_id
                    for name, _ in students
                    if (student_id := student_ids.get(name)) is not None
                ]
            )
            student_icons = {
                name: icons_by_id[str(student_id)]
                for name, _ in students
                if (student_id := student_ids.get(name)) is not None
                and str(student_id) in icons_by_id
            }
            card = BirthdayCard(
                students=students,
                student_icons=student_icons,
                month=current_month,
                year=current_year,
            )
            return await self._render_in_thread(card.render), len(students)

        raise ValueError(f"Unknown polling category: {category}")

    # ------------------------------------------------------------------
    # 消息路由
    # ------------------------------------------------------------------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def router(self, event: AstrMessageEvent):
        self._cleanup_student_choices()
        raw_message = self._menu_raw_message(event)
        if raw_message and raw_message.get("_ba_student_choice_token"):
            token = str(raw_message["_ba_student_choice_token"])
            choice = self._student_choices_by_token.get(token)
            if choice:
                action = raw_message.get("_ba_student_choice_action")
                actor_id = str(raw_message.get("_ba_student_choice_actor_id") or "")
                if (
                    actor_id
                    and str(getattr(event, "get_sender_id", lambda: "")()) == actor_id
                ):
                    async for result in self._finish_student_choice(
                        event,
                        choice,
                        action,
                    ):
                        yield result
            return

        is_internal_menu_command = bool(
            raw_message and raw_message.get("_ba_menu_internal")
        )
        if (
            raw_message
            and not is_internal_menu_command
            and await self._handle_menu_reaction(event, raw_message)
        ):
            return

        msg = event.message_str.strip()
        group_id = str(event.get_group_id() or "")
        user_id = str(event.get_sender_id() or "")
        menu_settings = self._menu_settings()
        menu_keywords = menu_settings.get("menu_keywords", ["菜单", "ba菜单"])
        if isinstance(menu_keywords, str):
            menu_keywords = [menu_keywords]
        if (
            not is_internal_menu_command
            and group_id
            and msg.lower().lstrip("/").strip()
            in {
                str(keyword).strip().lower().lstrip("/")
                for keyword in menu_keywords
                if str(keyword).strip()
            }
        ):
            if menu_settings.get(
                "menu_repeat_block", True
            ) and self._active_menu_for_group(group_id):
                yield event.plain_result(
                    str(
                        menu_settings.get(
                            "menu_repeat_reply",
                            "当前菜单仍然有效，请点击表情或回复菜单项进行查询。",
                        )
                    )
                )
                return
            if not await self._send_reaction_menu(event):
                yield event.plain_result(self._build_reaction_menu_text())
            self._stop_menu_event(event)
            return

        if not is_internal_menu_command and user_id:
            reply_to_id = self._reply_to_message_id(event, raw_message)
            choice = self._student_candidate_menus.get(reply_to_id or "")
            if choice and choice["user_id"] != user_id:
                choice = None
            action = self._match_student_choice(choice, msg) if choice else None
            if choice and action is not None:
                async for result in self._finish_student_choice(event, choice, action):
                    yield result
                return

        menu_selection = (
            self._match_active_menu_item(group_id, msg)
            if group_id and not is_internal_menu_command and not msg.startswith("/")
            else None
        )
        if menu_selection:
            menu_data, item = menu_selection
            user_id = str(event.get_sender_id() or "")
            if user_id and await self._execute_menu_command(
                event, user_id, group_id, item["command"]
            ):
                self._stop_menu_event(event)
                return
            yield event.plain_result("菜单指令触发失败，请检查平台连接和插件日志。")
            return

        # 学生详情查询命令
        detail_cmds = self._get_command("detail_query", ["学生", "ba_student"])
        matched_cmd, args = self._match_command(msg, detail_cmds)
        if matched_cmd is not None:
            self._log_debug(
                "router", "detail command matched", command=matched_cmd, args=args
            )
            async for result in self._handle_student_detail(event, args):
                yield result
            return

        # 活动日程
        schedule_cmds = self._get_command(
            "schedule_query", ["活动", "日程", "ba_schedule"]
        )
        matched_cmd, args = self._match_command(msg, schedule_cmds)
        if matched_cmd is not None:
            self._log_debug(
                "router", "schedule command matched", command=matched_cmd, args=args
            )
            async for result in self._handle_schedule(event, args):
                yield result
            return

        # 卡池千里眼
        pool_cmds = self._get_command("pool_query", ["卡池", "千里眼", "ba_pool"])
        matched_cmd, args = self._match_command(msg, pool_cmds)
        if matched_cmd is not None:
            self._log_debug(
                "router", "pool command matched", command=matched_cmd, args=args
            )
            async for result in self._handle_card_pool(event, args):
                yield result
            return

        # 学生生日
        birthday_cmds = self._get_command("birthday_query", ["生日", "ba_birthday"])
        matched_cmd, args = self._match_command(msg, birthday_cmds)
        if matched_cmd is not None:
            self._log_debug(
                "router", "birthday command matched", command=matched_cmd, args=args
            )
            async for result in self._handle_birthday(event, args):
                yield result
            return

        # 攻略查询
        guide_cmds = self._get_command("guide_query", ["攻略", "ba_guide"])
        matched_cmd, args = self._match_command(msg, guide_cmds)
        if matched_cmd is not None:
            self._log_debug(
                "router", "guide command matched", command=matched_cmd, args=args
            )
            async for result in self._handle_guide(event, args):
                yield result
            return

    def _menu_settings(self) -> dict[str, Any]:
        settings = self.config.get("menu_settings", {})
        return settings if isinstance(settings, dict) else {}

    def _student_choice_settings(self) -> dict[str, Any]:
        settings = self.config.get("student_choice_settings", {})
        return settings if isinstance(settings, dict) else {}

    def _student_search_settings(self) -> dict[str, Any]:
        settings = self.config.get("student_search_settings", {})
        return settings if isinstance(settings, dict) else {}

    def _student_search_aliases(self) -> dict[str, str]:
        configured = self._student_search_settings().get("custom_aliases", [])
        if isinstance(configured, str):
            configured = configured.splitlines()
        if not isinstance(configured, (list, tuple)):
            return {}

        aliases = {}
        for item in configured:
            if not isinstance(item, str) or "=" not in item:
                continue
            alias, canonical = (part.strip() for part in item.split("=", 1))
            if alias and canonical:
                aliases[alias] = canonical
        return aliases

    def _student_llm_enabled(self) -> bool:
        return self._student_search_settings().get("llm_enabled") is True

    async def _search_student_candidates(
        self, name: str, language: str
    ) -> list[dict[str, Any]]:
        self.schaledb.student_aliases = self._student_search_aliases()
        return await self.schaledb.search_student_candidates(name, language)

    def _student_choice_timeout(self) -> int:
        try:
            timeout = int(
                self._student_choice_settings().get(
                    "timeout_seconds", STUDENT_CHOICE_TIMEOUT
                )
            )
        except (TypeError, ValueError):
            timeout = STUDENT_CHOICE_TIMEOUT
        return max(10, min(timeout, 3600))

    def _student_choice_max_active_menus(self) -> int:
        try:
            limit = int(self._student_choice_settings().get("max_active_menus", 3))
        except (TypeError, ValueError):
            limit = 3
        return max(1, min(limit, 3))

    def _cleanup_student_choices(self) -> None:
        now = time.time()
        expired = [
            token
            for token, choice in self._pending_student_queries.items()
            if now - choice["created_at"]
            > choice.get("timeout_seconds", self._student_choice_timeout())
        ]
        for token in expired:
            self._close_student_choice(token)

    def _close_student_choice(self, token: str) -> None:
        choice = self._pending_student_queries.pop(token, None)
        if not choice:
            return
        self._student_choices_by_token.pop(token, None)
        message_id = choice.get("message_id")
        if message_id:
            self._student_candidate_menus.pop(message_id, None)

    @staticmethod
    def _reply_to_message_id(
        event: AstrMessageEvent, raw_message: dict[str, Any] | None = None
    ) -> str | None:
        message_obj = getattr(event, "message_obj", None)
        for component in getattr(message_obj, "message", []) or []:
            if isinstance(component, Reply) and component.id is not None:
                return str(component.id)

        raw_segments = (raw_message or {}).get("message", [])
        if isinstance(raw_segments, list):
            for segment in raw_segments:
                if not isinstance(segment, dict) or segment.get("type") != "reply":
                    continue
                data = segment.get("data")
                if isinstance(data, dict) and data.get("id") is not None:
                    return str(data["id"])
        return None

    @staticmethod
    def _match_student_choice(choice: dict[str, Any], text: str) -> int | str | None:
        normalized = text.strip().casefold()
        if normalized in {"取消", "取消查询", "cancel"}:
            return "cancel"
        if choice.get("llm_enabled") and normalized in {
            "llm",
            "大模型辅助匹配",
            "模型辅助匹配",
        }:
            return "llm"
        candidates = choice["candidates"]
        if normalized.isdigit():
            index = int(normalized)
            if choice.get("llm_enabled") and index == len(candidates) + 1:
                return "llm"
            cancel_index = len(candidates) + 1 + int(bool(choice.get("llm_enabled")))
            if index == cancel_index:
                return "cancel"
            if 1 <= index <= len(candidates):
                return index - 1
        for index, label in enumerate(choice["labels"]):
            if normalized == label.casefold():
                return index
        return None

    async def _finish_student_choice(
        self,
        event: AstrMessageEvent,
        choice: dict[str, Any],
        action: Any,
    ):
        token = choice["token"]
        if self._pending_student_queries.get(token) is not choice:
            return
        if action == "cancel":
            self._close_student_choice(token)
            yield event.plain_result("已取消学生查询。")
            return
        if action == "llm":
            if not choice.get("llm_enabled"):
                return
            async for result in self._finish_student_choice_with_llm(event, choice):
                yield result
            return
        if not isinstance(action, int) or not 0 <= action < len(choice["candidates"]):
            return
        student = choice["candidates"][action]
        if choice.get("query_mode") == "guide":
            results = self._fetch_guide_image(
                event, str(student.get("Name") or student.get("PathName") or "")
            )
        else:
            results = self._render_student_detail(event, student, choice["language"])
        async for result in results:
            yield result

    async def _finish_student_choice_with_llm(
        self, event: AstrMessageEvent, choice: dict[str, Any]
    ):
        identities = choice.get("ranked_identities", [])
        if not identities:
            yield event.plain_result(
                "当前查询没有足够的本地角色候选可供大模型辅助，原候选菜单仍然有效。"
            )
            return

        settings = self._student_search_settings()
        provider_id = str(settings.get("llm_provider_id") or "").strip()
        if not provider_id:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                yield event.plain_result(
                    "无法获取当前会话使用的大模型，请在“学生模糊匹配”中填写模型 Provider ID；"
                    "原候选菜单仍然有效。"
                )
                return
            try:
                provider_id = await self.context.get_current_chat_provider_id(umo)
            except ProviderNotFoundError as exc:
                self._log_warn("student", "LLM provider unavailable", error=exc)
                yield event.plain_result(
                    "当前会话没有可用的大模型，请在“学生模糊匹配”中填写模型 Provider ID；"
                    "原候选菜单仍然有效。"
                )
                return

        timeout = settings.get("llm_timeout_seconds", 15)
        try:
            timeout = max(3, min(int(timeout), 120))
        except (TypeError, ValueError):
            timeout = 15

        prompt_candidates = [
            {
                "candidate_id": identity["candidate_id"],
                "name": identity["name"],
                "variants": identity["variants"],
                "local_score": identity["local_score"],
            }
            for identity in identities
        ]
        system_prompt = (
            "你是碧蓝档案学生名称匹配助手。只能在用户提供的候选名单中排序，"
            "不要创建、改写或补充候选，也不要调用外部知识来猜测。"
            "结合用户输入与候选名称、形态名，按最可能匹配度从高到低排序。"
            "只返回 JSON，不要 Markdown 或解释，格式为："
            '{"ranking":[{"candidate_id":"原候选ID","name":"原候选名称",'
            '"score":0}]}。score 是 0 到 100 的整数，表示相对匹配程度，不是事实保证。'
        )
        prompt = json.dumps(
            {"query": choice["query"], "candidates": prompt_candidates},
            ensure_ascii=False,
        )
        try:
            response = await asyncio.wait_for(
                self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    system_prompt=system_prompt,
                ),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, ProviderNotFoundError) as exc:
            self._log_warn("student", "LLM-assisted matching unavailable", error=exc)
            yield event.plain_result(
                "大模型辅助匹配超时或不可用，原候选菜单仍然有效；"
                "请直接选择菜单中的形态。"
            )
            return

        response_text = str(getattr(response, "completion_text", "") or "").strip()
        if response_text.startswith("```"):
            response_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", response_text)
        try:
            payload = json.loads(response_text)
            ranking = payload["ranking"]
            if not isinstance(ranking, list):
                raise ValueError("ranking must be a list")
            by_id = {item["candidate_id"]: item for item in identities}
            reordered = []
            seen = set()
            for item in ranking:
                if not isinstance(item, dict):
                    raise ValueError("ranking items must be objects")
                candidate_id = str(item.get("candidate_id", ""))
                candidate = by_id.get(candidate_id)
                score = item.get("score")
                if (
                    candidate is None
                    or item.get("name") != candidate["name"]
                    or isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not 0 <= score <= 100
                    or candidate_id in seen
                ):
                    raise ValueError("invalid candidate ranking")
                seen.add(candidate_id)
                reordered.append((candidate, int(score)))
            if seen != by_id.keys():
                raise ValueError("ranking must include every supplied candidate")
            reordered.sort(key=lambda result: result[1], reverse=True)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self._log_warn("student", "LLM returned an invalid ranking", error=exc)
            yield event.plain_result(
                "大模型返回的候选结果无法校验，原候选菜单仍然有效；"
                "请直接选择菜单中的形态。"
            )
            return

        selected, confidence = reordered[0]
        alternatives = [
            candidate["name"]
            for candidate, _ in reordered[1:]
            if candidate["name"] != selected["name"]
        ][:4]
        notice = (
            f"大模型认为最可能是「{selected['name']}」（参考匹配度 {confidence}%）。"
        )
        if alternatives:
            notice += (
                "另外几个相似角色是："
                + "、".join(alternatives)
                + "；如果不对，可以继续用这些名称查询。"
            )
        yield event.plain_result(notice)

        sent, menu_prompt = await self._start_student_choice(
            event,
            choice["query"],
            selected["forms"],
            choice["language"],
            query_mode=choice.get("query_mode", "detail"),
            primary_name=selected["name"],
            recommendations=alternatives,
            replacing_token=choice["token"],
            llm_enabled=False,
        )
        if sent:
            self._close_student_choice(choice["token"])
        else:
            yield event.plain_result(menu_prompt)

    @staticmethod
    def _student_candidate_labels(
        candidates: list[dict[str, Any]],
    ) -> list[str]:
        labels = [
            str(student.get("Name") or student.get("PathName") or student.get("Id"))
            for student in candidates
        ]
        counts = {label: labels.count(label) for label in set(labels)}
        for index, label in enumerate(labels):
            if counts[label] > 1:
                student = candidates[index]
                detail = student.get("PathName") or student.get("Id")
                labels[index] = f"{label}（{detail}）"
        return labels

    async def _start_student_choice(
        self,
        event: AstrMessageEvent,
        query: str,
        candidates: list[dict[str, Any]],
        language: str,
        query_mode: str = "detail",
        suggestions: bool = False,
        primary_name: str = "",
        recommendations: list[str] | None = None,
        replacing_token: str | None = None,
        llm_enabled: bool | None = None,
    ) -> tuple[bool, str]:
        user_id = str(event.get_sender_id() or "")
        if not user_id:
            return False, "无法识别查询者，暂时无法创建学生候选菜单。"

        group_id = str(event.get_group_id() or "")
        bot = getattr(event, "bot", None)
        if (
            not group_id
            or bot is None
            or not hasattr(bot, "send_group_msg")
            or not hasattr(bot, "set_msg_emoji_like")
        ):
            return False, "当前平台不支持发送学生候选互动菜单。"

        self._cleanup_student_choices()
        max_active_menus = self._student_choice_max_active_menus()
        active_in_group = sum(
            choice["group_id"] == group_id and choice["token"] != replacing_token
            for choice in self._pending_student_queries.values()
        )
        if active_in_group >= max_active_menus:
            return (
                False,
                f"本群已有 {max_active_menus} 个学生候选菜单正在等待选择；"
                "请使用已有菜单或等待菜单过期后再查询。",
            )

        shown_candidates = candidates[:MAX_STUDENT_CHOICES]
        labels = self._student_candidate_labels(shown_candidates)
        token = uuid4().hex
        choice = {
            "token": token,
            "group_id": group_id,
            "user_id": user_id,
            "created_at": time.time(),
            "timeout_seconds": self._student_choice_timeout(),
            "query": query,
            "query_mode": query_mode,
            "candidates": shown_candidates,
            "labels": labels,
            "language": language,
            "ranked_identities": getattr(candidates, "ranked_identities", []),
            "llm_enabled": (
                self._student_llm_enabled()
                if llm_enabled is None
                else llm_enabled
            ),
            "message_id": "",
            "emoji_map": {},
        }
        self._pending_student_queries[token] = choice
        self._student_choices_by_token[choice["token"]] = choice

        if suggestions:
            lines = [
                f"「{query}」未能确定具体学生，最接近「{primary_name}」，"
                "请从该学生的形态中选择："
            ]
        else:
            lines = [
                f"「{query}」最接近「{primary_name}」，以下是该学生的形态，请选择："
            ]
        source_names = {"cn": "国服", "en": "国际服", "jp": "日服"}
        for index, (student, label) in enumerate(
            zip(shown_candidates, labels), start=1
        ):
            source = source_names.get(student.get("_data_source_language"), "其他")
            lines.append(f"{index}. {label}（{source}数据）")
        if recommendations:
            lines.append(
                "其他相似角色（如不是你要找的角色，可重新查询）："
                + "、".join(recommendations)
            )
        if choice["llm_enabled"]:
            lines.append(
                f"{len(shown_candidates) + 1}. 🤖 大模型辅助匹配（可略微提升准确性）"
            )
        cancel_index = len(shown_candidates) + 1 + int(choice["llm_enabled"])
        lines.append(f"{cancel_index}. 取消查询")
        if len(candidates) > len(shown_candidates):
            lines.append(
                f"仅显示优先级最高的 {len(shown_candidates)} 项，共有 {len(candidates)} 项；"
                "可输入更具体的学生名称缩小范围。"
            )
        lines.append(
            "群内任何人都可点击表情查询；发起者可回复此消息选择序号/名称。"
            "选择后菜单继续有效，点击取消查询关闭菜单。"
        )
        prompt = "\n".join(lines)

        try:
            response = await bot.send_group_msg(
                group_id=int(group_id),
                message=prompt,
            )
        except Exception as exc:
            self._close_student_choice(token)
            self._log_error("student", "failed to send candidate menu", error=exc)
            return False, prompt
        message_id = (
            str(response.get("message_id", "")) if isinstance(response, dict) else ""
        )
        if not message_id:
            self._close_student_choice(token)
            self._log_error("student", "candidate menu response has no message ID")
            return False, prompt

        choice["message_id"] = message_id
        self._student_candidate_menus[message_id] = choice
        face_pool = self._student_choice_settings().get("face_pool", [])
        if not face_pool:
            face_pool = self._menu_settings().get("face_pool", [])
        if isinstance(face_pool, str):
            face_pool = re.split(r"[\s,]+", face_pool.strip())
        if not isinstance(face_pool, (list, tuple)):
            face_pool = []
        unique_faces: list[str] = []
        for face in (*face_pool, *STUDENT_CHOICE_FALLBACK_FACE_POOL):
            face_id = str(face).strip()
            if face_id.isdigit() and face_id not in unique_faces:
                unique_faces.append(face_id)
        actions: list[int | str] = list(range(len(shown_candidates)))
        if choice["llm_enabled"]:
            actions.append("llm")
        actions.append("cancel")
        action_index = 0
        for face_id in unique_faces:
            if action_index >= len(actions):
                break
            try:
                await bot.set_msg_emoji_like(
                    message_id=message_id,
                    emoji_id=int(face_id),
                    set=True,
                )
            except Exception as exc:
                self._log_warn(
                    "student",
                    "failed to attach candidate reaction",
                    face_id=face_id,
                    error=exc,
                )
                continue
            choice["emoji_map"][face_id] = actions[action_index]
            action_index += 1
            await asyncio.sleep(0.25)
        if action_index < len(actions):
            self._log_warn(
                "student",
                "not enough reactions attached to candidate menu",
                attached=action_index,
                required=len(actions),
            )
        return True, prompt

    def _menu_items(self) -> list[dict[str, str]]:
        settings = self._menu_settings()
        raw_items = settings.get("menu_items", [])
        if not isinstance(raw_items, (list, tuple)):
            return []
        face_pool = settings.get("face_pool", [])
        if isinstance(face_pool, str):
            face_pool = re.split(r"[\s,]+", face_pool.strip())
        if not isinstance(face_pool, (list, tuple)):
            face_pool = []
        available_faces = [str(face).strip() for face in face_pool if str(face).strip()]

        items = []
        for raw_item in raw_items:
            if isinstance(raw_item, dict):
                label = str(raw_item.get("label", "")).strip()
                command = str(raw_item.get("command", "")).strip()
                face_id = str(raw_item.get("face_id", "")).strip()
            elif isinstance(raw_item, str):
                parts = raw_item.split(",", 1)
                label = parts[0].strip()
                command = (parts[1] if len(parts) > 1 else parts[0]).strip()
                face_id = ""
            else:
                continue
            if not label or not command:
                continue
            if not face_id and len(items) < len(available_faces):
                face_id = available_faces[len(items)]
            items.append({"label": label, "command": command, "face_id": face_id})
        return items

    def _build_reaction_menu_text(self) -> str:
        settings = self._menu_settings()
        divider = str(settings.get("menu_divider_char", "─"))[:1] or "─"
        divider_length = max(1, min(int(settings.get("menu_divider_length", 28)), 80))
        lines = [
            str(settings.get("menu_title", "📘 BA 工具箱菜单")),
            divider * divider_length,
        ]
        lines.extend(
            f"  {index}. {item['label']}"
            for index, item in enumerate(self._menu_items(), start=1)
        )
        lines.extend(
            [
                divider * divider_length,
                "点击下方对应表情，或回复序号/菜单名称触发查询。",
            ]
        )
        return "\n".join(lines)

    async def _send_reaction_menu(self, event: AstrMessageEvent) -> bool:
        group_id = str(event.get_group_id() or "")
        bot = getattr(event, "bot", None)
        if (
            not group_id
            or bot is None
            or not hasattr(bot, "send_group_msg")
            or not hasattr(bot, "set_msg_emoji_like")
        ):
            return False
        items = self._menu_items()
        if not items:
            self._log_warn("menu", "menu has no valid items")
            return False
        try:
            response = await bot.send_group_msg(
                group_id=int(group_id),
                message=self._build_reaction_menu_text(),
            )
        except Exception as exc:
            self._log_error("menu", "failed to send menu", error=exc)
            return False
        message_id = (
            str(response.get("message_id", "")) if isinstance(response, dict) else ""
        )
        if not message_id:
            self._log_error("menu", "platform did not return menu message ID")
            return False

        max_reactions = max(0, int(self._menu_settings().get("menu_max_reactions", 20)))
        reaction_items = [item for item in items if item["face_id"]][
            : max_reactions or None
        ]
        now = time.time()
        self._active_menus[message_id] = {
            "group_id": group_id,
            "created_at": now,
            "items": items,
            "emoji_map": {item["face_id"]: item for item in reaction_items},
        }
        for item in reaction_items:
            try:
                await bot.set_msg_emoji_like(
                    message_id=message_id,
                    emoji_id=int(item["face_id"]),
                    set=True,
                )
                await asyncio.sleep(0.25)
            except Exception as exc:
                self._log_warn(
                    "menu",
                    "failed to attach menu reaction",
                    face_id=item["face_id"],
                    error=exc,
                )
        return True

    def _active_menu_for_group(self, group_id: str) -> dict[str, Any] | None:
        now = time.time()
        timeout = max(1, int(self._menu_settings().get("menu_timeout_seconds", 600)))
        expired = [
            message_id
            for message_id, data in self._active_menus.items()
            if now - data["created_at"] > timeout
        ]
        for message_id in expired:
            self._active_menus.pop(message_id, None)
        menus = [
            data for data in self._active_menus.values() if data["group_id"] == group_id
        ]
        return max(menus, key=lambda data: data["created_at"], default=None)

    def _match_active_menu_item(
        self, group_id: str, text: str
    ) -> tuple[dict[str, Any], dict[str, str]] | None:
        menu = self._active_menu_for_group(group_id)
        if menu is None:
            return None
        normalized = text.strip().lstrip("/").strip().casefold()
        for index, item in enumerate(menu["items"], start=1):
            if normalized in {
                str(index),
                f"{index}.",
                item["label"].casefold(),
                item["command"].lstrip("/").casefold(),
            }:
                return menu, item
        return None

    @staticmethod
    def _menu_raw_message(event: AstrMessageEvent) -> dict[str, Any] | None:
        message_obj = getattr(event, "message_obj", None)
        raw = getattr(message_obj, "raw_message", None)
        if raw is None:
            raw = getattr(event, "raw_message", None)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return None
        return raw if isinstance(raw, dict) else None

    async def _handle_menu_reaction(
        self, event: AstrMessageEvent, raw: dict[str, Any]
    ) -> bool:
        notice_type = str(raw.get("notice_type", "")).lower()
        if notice_type not in {"group_msg_reaction", "group_msg_emoji_like"}:
            return False
        operation = str(raw.get("sub_type", "")).lower()
        if operation in {"remove", "cancel", "delete"}:
            return False
        nested = raw.get("message")
        nested = nested if isinstance(nested, dict) else {}
        message_id = str(
            raw.get("message_id") or raw.get("msg_id") or nested.get("message_id") or ""
        )
        clicker_id = str(
            raw.get("user_id")
            or raw.get("operator_id")
            or raw.get("sender_id")
            or raw.get("qq")
            or ""
        )
        face_id = str(raw.get("face_id") or raw.get("emoji_id") or "")
        if not face_id and isinstance(raw.get("likes"), list) and raw["likes"]:
            first = raw["likes"][0]
            if isinstance(first, dict):
                face_id = str(first.get("emoji_id") or first.get("face_id") or "")
        if not face_id:
            face = nested.get("face") or nested.get("emoji")
            if isinstance(face, dict):
                face_id = str(face.get("id") or face.get("emoji_id") or "")

        choice = self._student_candidate_menus.get(message_id)
        if choice and clicker_id and face_id:
            if time.time() - choice["created_at"] > choice["timeout_seconds"]:
                self._close_student_choice(choice["token"])
                return True
            if time.time() - choice["created_at"] < 1.5:
                return True
            action = choice["emoji_map"].get(face_id)
            if action is None:
                return False
            debounce_key = (message_id, clicker_id, face_id)
            now = time.time()
            debounce_seconds = max(
                0, int(self._menu_settings().get("debounce_seconds", 3))
            )
            if now - self._menu_debounce.get(debounce_key, 0) < debounce_seconds:
                return True
            self._menu_debounce[debounce_key] = now
            if not await self._execute_menu_command(
                event,
                clicker_id,
                choice["group_id"],
                "__ba_student_choice__",
                internal_payload={
                    "_ba_student_choice_token": choice["token"],
                    "_ba_student_choice_action": action,
                    "_ba_student_choice_actor_id": clicker_id,
                },
            ):
                self._log_error("student", "failed to dispatch candidate selection")
            self._stop_menu_event(event)
            return True

        menu = self._active_menus.get(message_id)
        if not menu or not clicker_id or not face_id:
            return False
        if time.time() - menu["created_at"] > max(
            1, int(self._menu_settings().get("menu_timeout_seconds", 600))
        ):
            self._active_menus.pop(message_id, None)
            return False
        if time.time() - menu["created_at"] < 1.5:
            return True
        face_item = menu["emoji_map"].get(face_id)
        if face_item is None:
            return False
        debounce_key = (message_id, clicker_id, face_id)
        now = time.time()
        debounce_seconds = max(0, int(self._menu_settings().get("debounce_seconds", 3)))
        if now - self._menu_debounce.get(debounce_key, 0) < debounce_seconds:
            return True
        self._menu_debounce[debounce_key] = now
        if not await self._execute_menu_command(
            event, clicker_id, menu["group_id"], face_item["command"]
        ):
            self._log_error("menu", "failed to dispatch reaction menu command")
        self._stop_menu_event(event)
        return True

    async def _execute_menu_command(
        self,
        event: AstrMessageEvent,
        user_id: str,
        group_id: str,
        command: str,
        internal_payload: dict[str, Any] | None = None,
    ) -> bool:
        if AstrBotMessage is not None and MessageMember is not None:
            try:
                fake_message = AstrBotMessage()
                fake_message.self_id = getattr(
                    getattr(event, "message_obj", None), "self_id", None
                ) or getattr(event, "self_id", "")
                fake_message.sender = MessageMember(user_id=user_id, nickname=user_id)
                fake_message.type = getattr(
                    getattr(event, "message_obj", None), "type", None
                ) or getattr(event, "message_type", "")
                fake_message.group_id = group_id
                fake_message.session_id = group_id
                fake_message.message_id = f"ba_menu_{int(time.time() * 1000)}_{user_id}"
                fake_message.message_str = command
                fake_message.message = [Plain(text=command)]
                fake_message.raw_message = {
                    "_ba_menu_internal": True,
                    **(internal_payload or {}),
                }
                platform_id = (
                    event.get_platform_id()
                    if callable(getattr(event, "get_platform_id", None))
                    else getattr(event, "platform_id", None)
                )
                platform = (
                    self.context.get_platform_inst(platform_id)
                    if platform_id is not None
                    else None
                )
                if (
                    platform
                    and hasattr(platform, "create_event")
                    and hasattr(platform, "commit_event")
                ):
                    virtual_event = platform.create_event(fake_message)
                    platform.commit_event(virtual_event)
                    return True
            except Exception as exc:
                self._log_warn("menu", "virtual command dispatch failed", error=exc)
        if internal_payload:
            return False
        try:
            session = getattr(event, "unified_msg_origin", None) or getattr(
                event, "session_id", None
            )
            if not session:
                session = f"{getattr(event, 'platform_id', 'aiocqhttp')}:group_message:{group_id}"
            await self.context.send_message(
                session=session,
                message_chain=MessageChain([Plain(command)]),
            )
            return True
        except Exception as exc:
            self._log_error("menu", "command dispatch failed", error=exc)
            return False

    def _stop_menu_event(self, event: AstrMessageEvent) -> None:
        stop_event = getattr(event, "stop_event", None)
        if callable(stop_event):
            try:
                stop_event()
            except Exception as exc:
                self._log_warn(
                    "menu", "failed to stop menu event propagation", error=exc
                )

    # ------------------------------------------------------------------
    # 命令解析
    # ------------------------------------------------------------------

    def _get_command(self, key: str, default: list[str]) -> list[str]:
        raw = self._get_setting(key, default)
        if isinstance(raw, str):
            commands = [raw]
        elif isinstance(raw, (list, tuple)):
            commands = [str(item) for item in raw]
        else:
            commands = list(default)
        if key == "detail_query":
            legacy = self._menu_settings().get("detail_query", [])
            if isinstance(legacy, str):
                legacy = [legacy]
            if isinstance(legacy, (list, tuple)):
                commands.extend(str(item) for item in legacy)
        return list(dict.fromkeys(command for command in commands if command.strip()))

    def _match_command(self, msg: str, commands: list[str]) -> tuple[str | None, str]:
        text = msg.strip()
        if text.startswith("/"):
            text = text[1:].lstrip()
        normalized_commands = sorted(
            (
                str(command).strip().lstrip("/")
                for command in commands
                if str(command).strip()
            ),
            key=len,
            reverse=True,
        )
        for command in normalized_commands:
            if text == command:
                return command, ""
            if text.startswith(command) and len(text) > len(command):
                separator = text[len(command)]
                if separator.isspace():
                    return command, text[len(command) :].strip()
        return None, ""

    # ------------------------------------------------------------------
    # 学生详情查询（SchaleDB）
    # ------------------------------------------------------------------

    async def _handle_student_detail(self, event: AstrMessageEvent, name: str):
        """查询完整 SchaleDB 学生数据并渲染详情卡片。"""
        if not name:
            commands = self._get_command("detail_query", ["学生", "ba_student"])
            command = commands[0] if commands else "学生"
            yield event.plain_result(
                "BA 学生详情查询\n"
                "请输入至少一部分学生名称进行模糊搜索并选择。\n"
                f"用法：/{command} <学生名>\n例如：/{command} 爱丽丝"
            )
            return

        self._log_debug("student", "querying student", name=name)

        language = str(
            self._get_setting("student_data_language")
            or self._get_server_region(
                str(getattr(event, "unified_msg_origin", "") or "")
            )
        )
        if language == "all":
            language = "cn"
        try:
            candidates = await self._search_student_candidates(name, language)
        except SchaleDBError as exc:
            self._log_error("student", "SchaleDB lookup failed", error=exc)
            yield event.plain_result(f"学生数据查询失败：{exc}")
            return

        if len(candidates) > 1 or getattr(candidates, "needs_confirmation", False):
            sent, prompt = await self._start_student_choice(
                event,
                name,
                candidates,
                language,
                suggestions=getattr(candidates, "needs_confirmation", False),
                primary_name=getattr(candidates, "primary_name", ""),
                recommendations=getattr(candidates, "recommendations", []),
            )
            if not sent:
                yield event.plain_result(prompt)
            return

        recommendations = getattr(candidates, "recommendations", [])
        if recommendations:
            yield event.plain_result(
                f"已匹配「{getattr(candidates, 'primary_name', '')}」；"
                f"其他相似角色：{'、'.join(recommendations)}"
            )
        student = candidates[0]
        async for result in self._render_student_detail(event, student, language):
            yield result

    async def _render_student_detail(
        self,
        event: AstrMessageEvent,
        student: dict[str, Any],
        language: str,
    ):
        try:
            student = await self.schaledb.prepare_student(student, language)
        except SchaleDBError as exc:
            self._log_error("student", "SchaleDB student preparation failed", error=exc)
            yield event.plain_result(f"学生数据准备失败：{exc}")
            return

        if self.schaledb.last_error:
            self._log_warn(
                "student",
                "SchaleDB used fallback data",
                detail=self.schaledb.last_error,
            )
        if self.schaledb.asset_errors:
            self._log_warn(
                "student",
                "some card assets could not be loaded",
                count=len(self.schaledb.asset_errors),
                assets=",".join(self.schaledb.asset_errors),
            )

        # Render the original SchaleDB record; do not flatten or re-enrich its fields.
        try:
            card_bytes = await self._render_in_thread(render_student_detail, student)
        except Exception as e:
            self._log_error("student", "render failed", error=e)
            yield event.plain_result("渲染失败，请稍后重试。")
            return

        # 保存图片到缓存
        img_path = self._store_card(card_bytes)

        msg_mode = str(self._get_setting("message_mode", "card"))
        if msg_mode in ("card", "mixed"):
            yield event.image_result(str(img_path))
        if msg_mode in ("text", "mixed"):
            yield event.plain_result(
                f"{student.get('Name', student.get('PathName', '未知'))}\n"
                f"稀有度：{student.get('StarGrade', '?')}星\n"
                f"生日：{student.get('Birthday', student.get('BirthDay', '未知'))}\n"
                f"{student.get('ProfileIntroduction', '')[:100]}"
            )

    # ------------------------------------------------------------------
    # 活动日程（GameKee）
    # ------------------------------------------------------------------

    async def _handle_schedule(self, event: AstrMessageEvent, args: str):
        """查询活动日程。"""
        server_region, error = self._resolve_server_region(event, args)
        if error:
            yield event.plain_result(error)
            return

        try:
            events = await self.gamekee.get_activity_calendar(server_region)
        except Exception as e:
            self._log_error("schedule", "fetch failed", error=e)
            yield event.plain_result("获取活动日程失败，请稍后重试。")
            return

        if self.gamekee.last_error:
            self._log_error("schedule", "fetch failed", error=self.gamekee.last_error)
            yield event.plain_result("获取活动日程失败，请稍后重试。")
            return
        if not events:
            yield event.plain_result(
                f"{self._server_region_name(server_region)} 暂无活动日程数据。"
            )
            return

        mode = str(self._get_setting("message_mode", "card"))
        if mode == "text":
            lines = [f"{self._server_region_name(server_region)} 活动日程："]
            lines.extend(
                f"• [{ev.get('pub_area', '')}] {ev.get('title', '未知活动')}"
                for ev in events
            )
            yield event.plain_result("\n".join(lines))
            return

        try:
            lines = [f"{self._server_region_name(server_region)} 活动日程："]
            lines.extend(
                f"• [{ev.get('pub_area', '')}] {ev.get('title', '未知活动')}"
                for ev in events
            )
            card = ScheduleCard(
                title="活动日程",
                events=events,
                region=server_region,
            )
            card_bytes = await self._render_in_thread(card.render)
            yield event.image_result(str(self._store_card(card_bytes)))
            if mode == "mixed":
                yield event.plain_result("\n".join(lines))
        except Exception as exc:
            self._log_error("schedule", "render failed", error=exc)
            yield event.plain_result("活动日程卡片渲染失败，请稍后重试。")

    # ------------------------------------------------------------------
    # 卡池千里眼（GameKee）
    # ------------------------------------------------------------------

    async def _handle_card_pool(self, event: AstrMessageEvent, args: str):
        """查询卡池千里眼。"""
        server_region, error = self._resolve_server_region(event, args)
        if error:
            yield event.plain_result(error)
            return

        try:
            pools = await self.gamekee.get_card_pools(server_region)
        except Exception as e:
            self._log_error("pool", "fetch failed", error=e)
            yield event.plain_result("获取卡池信息失败，请稍后重试。")
            return

        if self.gamekee.last_error:
            self._log_error("pool", "fetch failed", error=self.gamekee.last_error)
            yield event.plain_result("获取卡池信息失败，请稍后重试。")
            return
        if not pools:
            yield event.plain_result(
                f"{self._server_region_name(server_region)} 暂无卡池数据。"
            )
            return

        mode = str(self._get_setting("message_mode", "card"))
        if mode == "text":
            lines = [f"{self._server_region_name(server_region)} 卡池："]
            lines.extend(
                f"• [{pool.get('pub_area', '')}] {pool.get('title', '未知卡池')}"
                for pool in pools
            )
            yield event.plain_result("\n".join(lines))
            return

        try:
            lines = [f"{self._server_region_name(server_region)} 卡池："]
            lines.extend(
                f"• [{pool.get('pub_area', '')}] {pool.get('title', '未知卡池')}"
                for pool in pools
            )
            pool_student_names = list(
                dict.fromkeys(
                    name
                    for pool in pools
                    for name in pool.get("students", [])
                    if isinstance(name, str) and name
                )
            )
            try:
                student_icons = await self.schaledb.get_student_icon_map(
                    pool_student_names, "cn"
                )
            except SchaleDBError as exc:
                self._log_warn("pool", "student portraits unavailable", error=exc)
                student_icons = {}
            if self.schaledb.asset_errors:
                self._log_warn(
                    "pool",
                    "some student portraits unavailable",
                    count=len(self.schaledb.asset_errors),
                )
            missing_portraits = len(set(pool_student_names)) - len(student_icons)
            if missing_portraits:
                self._log_warn(
                    "pool",
                    "student portraits could not be matched",
                    count=missing_portraits,
                )
            card = CardPoolCard(
                pools=pools,
                region=server_region,
                student_icons=student_icons,
            )
            card_bytes = await self._render_in_thread(card.render)
            yield event.image_result(str(self._store_card(card_bytes)))
            if mode == "mixed":
                yield event.plain_result("\n".join(lines))
        except Exception as exc:
            self._log_error("pool", "render failed", error=exc)
            yield event.plain_result("卡池卡片渲染失败，请稍后重试。")

    # ------------------------------------------------------------------
    # 学生生日（SchaleDB）
    # ------------------------------------------------------------------

    async def _handle_birthday(self, event: AstrMessageEvent, args: str):
        """查询学生生日。"""
        query = args.strip()
        month_match = re.fullmatch(r"(\d{1,2})\s*月?", query)
        if month_match and not 1 <= int(month_match.group(1)) <= 12:
            yield event.plain_result("月份无效，请输入 1月 到 12月。")
            return
        requested_month = int(month_match.group(1)) if month_match else None

        language = str(self._get_setting("student_data_language") or "cn")
        try:
            birthday_records = await self.schaledb.get_birthdays_with_ids(language)
        except SchaleDBError as e:
            self._log_error("birthday", "fetch failed", error=e)
            yield event.plain_result(f"获取生日数据失败：{e}")
            return

        if self.schaledb.last_error:
            self._log_warn(
                "birthday",
                "SchaleDB used fallback data",
                detail=self.schaledb.last_error,
            )
        if not birthday_records:
            yield event.plain_result("暂无生日数据。")
            return

        if query and requested_month is None:
            normalized_query = re.sub(r"\s+", "", query).casefold()
            exact_matches = [
                (name, birthday)
                for name, birthday, _ in birthday_records
                if re.sub(r"\s+", "", name).casefold() == normalized_query
            ]
            matches = exact_matches or [
                (name, birthday)
                for name, birthday, _ in birthday_records
                if normalized_query in re.sub(r"\s+", "", name).casefold()
            ]
            if not matches:
                yield event.plain_result(f"没有找到「{query}」的生日记录。")
            elif len(matches) == 1:
                name, birthday = matches[0]
                yield event.plain_result(f"{name}的生日是 {birthday}。")
            else:
                lines = [f"找到多个匹配「{query}」的学生生日："]
                lines.extend(f"• {name}：{birthday}" for name, birthday in matches)
                yield event.plain_result("\n".join(lines))
            return

        now = datetime.now()
        month = requested_month or now.month
        year = now.year
        students_with_ids = [
            (name, birthday, student_id)
            for name, birthday, student_id in birthday_records
            if (birthday_date := parse_birthday_date(birthday))
            and birthday_date[0] == month
        ]
        students = [(name, birthday) for name, birthday, _ in students_with_ids]
        records_by_name = {
            name: student_id for name, _, student_id in students_with_ids
        }
        mode = str(self._get_setting("message_mode", "card"))
        if mode == "text":
            lines = [f"{month}月学生生日（共 {len(students)} 位）："]
            lines.extend(f"• {name}：{birthday}" for name, birthday in students)
            yield event.plain_result("\n".join(lines))
            return

        try:
            try:
                icons_by_id = await self.schaledb.get_student_icons_by_id(
                    [
                        student_id
                        for name, _ in students
                        if (student_id := records_by_name.get(name)) is not None
                    ]
                )
                student_icons = {
                    name: icons_by_id[str(student_id)]
                    for name, _ in students
                    if (student_id := records_by_name.get(name)) is not None
                    and str(student_id) in icons_by_id
                }
            except SchaleDBError as exc:
                self._log_warn("birthday", "student portraits unavailable", error=exc)
                student_icons = {}
            if self.schaledb.asset_errors:
                self._log_warn(
                    "birthday",
                    "some student portraits unavailable",
                    count=len(self.schaledb.asset_errors),
                )
            missing_portraits = len(
                {name for name, _ in students if records_by_name.get(name) is not None}
            ) - len(student_icons)
            if missing_portraits:
                self._log_warn(
                    "birthday",
                    "student portraits could not be matched",
                    count=missing_portraits,
                )
            card = BirthdayCard(
                students=students,
                student_icons=student_icons,
                month=month,
                year=year,
            )
            card_bytes = await self._render_in_thread(card.render)
            yield event.image_result(str(self._store_card(card_bytes)))
            if mode == "mixed":
                lines = [f"{month}月学生生日（共 {len(students)} 位）："]
                lines.extend(f"• {name}：{birthday}" for name, birthday in students)
                yield event.plain_result("\n".join(lines))
        except Exception as exc:
            self._log_error("birthday", "render failed", error=exc)
            yield event.plain_result("学生生日卡片渲染失败，请稍后重试。")

    # ------------------------------------------------------------------
    # 攻略查询（Arona）
    # ------------------------------------------------------------------

    async def _handle_guide(self, event: AstrMessageEvent, name: str):
        """通过模糊匹配的学生名称查询攻略图片。"""
        if not name:
            commands = self._get_command("guide_query", ["攻略", "ba_guide"])
            command = commands[0] if commands else "攻略"
            yield event.plain_result(
                "BA 攻略查询\n"
                "请输入至少一部分学生名称进行模糊搜索并选择。\n"
                f"用法：/{command} <学生名>\n例如：/{command} 爱丽丝"
            )
            return

        language = str(
            self._get_setting("student_data_language")
            or self._get_server_region(
                str(getattr(event, "unified_msg_origin", "") or "")
            )
        )
        if language == "all":
            language = "cn"
        try:
            candidates = await self._search_student_candidates(name, language)
        except SchaleDBError as exc:
            self._log_error("guide", "student name lookup failed", error=exc)
            yield event.plain_result(f"攻略学生名称匹配失败：{exc}")
            return

        if len(candidates) > 1 or getattr(candidates, "needs_confirmation", False):
            sent, prompt = await self._start_student_choice(
                event,
                name,
                candidates,
                language,
                query_mode="guide",
                suggestions=getattr(candidates, "needs_confirmation", False),
                primary_name=getattr(candidates, "primary_name", ""),
                recommendations=getattr(candidates, "recommendations", []),
            )
            if not sent:
                yield event.plain_result(prompt)
            return

        recommendations = getattr(candidates, "recommendations", [])
        if recommendations:
            yield event.plain_result(
                f"已匹配「{getattr(candidates, 'primary_name', '')}」；"
                f"其他相似角色：{'、'.join(recommendations)}"
            )
        student_name = str(
            candidates[0].get("Name") or candidates[0].get("PathName") or name
        )
        async for result in self._fetch_guide_image(event, student_name):
            yield result

    async def _fetch_guide_image(self, event: AstrMessageEvent, name: str):
        try:
            guide_url = await self.arona.get_guide_image(name)
        except AronaAmbiguousError as exc:
            yield event.plain_result(str(exc))
            return
        except Exception as exc:
            self._log_error("guide", "fetch failed", error=exc)
            yield event.plain_result("获取攻略失败，请稍后重试。")
            return

        if self.arona.last_error:
            self._log_error("guide", "fetch failed", error=self.arona.last_error)
            yield event.plain_result("获取攻略失败，请稍后重试。")
            return
        if not guide_url:
            yield event.plain_result(f"未找到学生「{name}」的攻略图。")
            return

        yield event.image_result(guide_url)

    # ------------------------------------------------------------------
    # 服务器区域解析
    # ------------------------------------------------------------------

    def _get_server_region(self, event_umo: str) -> str:
        default_region = str(self._get_setting("server_region", "cn")).strip().lower()
        if default_region not in ("cn", "global", "jp", "all"):
            default_region = "cn"
        if not event_umo:
            return default_region
        umo_groups = self._get_setting("umo_groups", [])
        for entry in umo_groups:
            if not isinstance(entry, str):
                continue
            umo, separator, region = entry.rpartition(":")
            region = region.strip().lower()
            if (
                separator
                and umo.strip() == event_umo
                and region
                in (
                    "cn",
                    "global",
                    "jp",
                    "all",
                )
            ):
                return region
        return default_region

    @staticmethod
    def _server_region_name(region: str) -> str:
        return {
            "cn": "国服",
            "global": "国际服",
            "jp": "日服",
            "all": "全服",
        }.get(region, region)

    def _resolve_server_region(
        self, event: AstrMessageEvent, args: str
    ) -> tuple[str, str | None]:
        requested = args.strip().lower()
        if not requested:
            return (
                self._get_server_region(
                    str(getattr(event, "unified_msg_origin", "") or "")
                ),
                None,
            )
        if requested not in ("cn", "global", "jp", "all"):
            return "", "服务器区域参数无效，请使用 cn、global、jp 或 all。"
        return requested, None

    # ------------------------------------------------------------------
    # 缓存
    # ------------------------------------------------------------------
