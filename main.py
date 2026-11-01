from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.message.message_event_result import MessageChain

try:
    from astrbot.api.platform import AstrBotMessage, MessageMember
except ImportError:
    AstrBotMessage = None
    MessageMember = None

from .api_clients.arona_client import AronaAmbiguousError, AronaClient
from .api_clients.cache_manager import ImageCacheManager
from .api_clients.gamekee import GameKeeClient
from .api_clients.schaledb_client import SchaleDBClient, SchaleDBError
from .poll_archive import PollArchive
from .renderers.card import BirthdayCard, CardPoolCard, ScheduleCard
from .renderers.card_fonts import prepare_card_fonts
from .renderers.html_renderer import configure_font_cache_dir
from .renderers.student_detail import render_student_detail

PLUGIN_ID = "astrbot_plugin_bluearchive_toolbox"
LOG_PREFIX = "[Ba ToolBox]"


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
                base_url=str(self._get_setting("schaledb_base_url") or "").strip() or None,
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

    async def _poll_loop(self) -> None:
        while True:
            try:
                if bool(self._get_setting("enabled", True)):
                    poll_time = str(self._get_setting("poll_time", "08:00"))
                    if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", poll_time):
                        now = datetime.now().astimezone()
                        archive_date = now.date().isoformat()
                        if (
                            now.strftime("%H:%M") >= poll_time
                            and not self.poll_archive.has_entries(archive_date)
                        ):
                            await self._run_daily_poll(now)
                    else:
                        self._log_warn(
                            "poll", "invalid poll time; daily polling skipped", value=poll_time
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
            self._log_warn("poll", "invalid poll region; using all servers", value=region)
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

        for category in categories:
            card_bytes = None
            item_count = 0
            error = None
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
            return ScheduleCard(events=events, region=region).render(), len(events)

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
            return (
                CardPoolCard(
                    pools=pools, region=region, student_icons=student_icons
                ).render(),
                len(pools),
            )

        if category == "birthday":
            language = str(self._get_setting("student_data_language") or "cn")
            records = await self.schaledb.get_birthdays_with_ids(language)
            birthdays = [(name, birthday) for name, birthday, _ in records]
            today = (datetime.now().month, datetime.now().day)

            def upcoming_key(record: tuple[str, str]) -> tuple[int, int, int, str]:
                match = re.search(r"(\d{1,2})\s*(?:/|月|-)\s*(\d{1,2})", record[1])
                if not match:
                    return 2, 13, 32, record[0]
                month, day = int(match.group(1)), int(match.group(2))
                return (0 if (month, day) >= today else 1, month, day, record[0])

            students = sorted(birthdays, key=upcoming_key)[:24]
            student_ids = {name: student_id for name, _, student_id in records}
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
            return (
                BirthdayCard(
                    students=students, student_icons=student_icons
                ).render(),
                len(birthdays),
            )

        raise ValueError(f"Unknown polling category: {category}")

    # ------------------------------------------------------------------
    # 消息路由
    # ------------------------------------------------------------------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def router(self, event: AstrMessageEvent):
        raw_message = self._menu_raw_message(event)
        if raw_message and raw_message.get("_ba_menu_internal"):
            return
        if raw_message and await self._handle_menu_reaction(event, raw_message):
            return

        msg = event.message_str.strip()
        group_id = str(event.get_group_id() or "")
        menu_settings = self._menu_settings()
        menu_keywords = menu_settings.get("menu_keywords", ["菜单", "ba菜单"])
        if isinstance(menu_keywords, str):
            menu_keywords = [menu_keywords]
        if group_id and msg.lower().lstrip("/").strip() in {
            str(keyword).strip().lower().lstrip("/")
            for keyword in menu_keywords
            if str(keyword).strip()
        }:
            if menu_settings.get("menu_repeat_block", True) and self._active_menu_for_group(group_id):
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

        menu_selection = self._match_active_menu_item(group_id, msg) if group_id else None
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
            items.append(
                {"label": label, "command": command, "face_id": face_id}
            )
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
        message_id = str(response.get("message_id", "")) if isinstance(response, dict) else ""
        if not message_id:
            self._log_error("menu", "platform did not return menu message ID")
            return False

        max_reactions = max(
            0, int(self._menu_settings().get("menu_max_reactions", 20))
        )
        reaction_items = [
            item for item in items if item["face_id"]
        ][:max_reactions or None]
        now = time.time()
        self._active_menus[message_id] = {
            "group_id": group_id,
            "created_at": now,
            "items": items,
            "emoji_map": {
                item["face_id"]: item for item in reaction_items
            },
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
            data
            for data in self._active_menus.values()
            if data["group_id"] == group_id
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
        message_id = str(raw.get("message_id") or raw.get("msg_id") or nested.get("message_id") or "")
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
        self, event: AstrMessageEvent, user_id: str, group_id: str, command: str
    ) -> bool:
        if AstrBotMessage is not None and MessageMember is not None:
            try:
                fake_message = AstrBotMessage()
                fake_message.self_id = (
                    getattr(getattr(event, "message_obj", None), "self_id", None)
                    or getattr(event, "self_id", "")
                )
                fake_message.sender = MessageMember(
                    user_id=user_id, nickname=user_id
                )
                fake_message.type = (
                    getattr(getattr(event, "message_obj", None), "type", None)
                    or getattr(event, "message_type", "")
                )
                fake_message.group_id = group_id
                fake_message.session_id = group_id
                fake_message.message_id = f"ba_menu_{int(time.time() * 1000)}_{user_id}"
                fake_message.message_str = command
                fake_message.message = [Plain(text=command)]
                fake_message.raw_message = {"_ba_menu_internal": True}
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
                if platform and hasattr(platform, "create_event") and hasattr(
                    platform, "commit_event"
                ):
                    virtual_event = platform.create_event(fake_message)
                    platform.commit_event(virtual_event)
                    return True
            except Exception as exc:
                self._log_warn("menu", "virtual command dispatch failed", error=exc)
        try:
            session = (
                getattr(event, "unified_msg_origin", None)
                or getattr(event, "session_id", None)
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
                self._log_warn("menu", "failed to stop menu event propagation", error=exc)

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
            (str(command).strip().lstrip("/") for command in commands if str(command).strip()),
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
            yield event.plain_result(
                "BA 学生详情查询\n用法：学生 <学生名>\n例如：学生 日富美（赛车）"
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
            student = await self.schaledb.search_student(name, language)
            student = await self.schaledb.prepare_student(student, language)
        except SchaleDBError as exc:
            self._log_error("student", "SchaleDB lookup failed", error=exc)
            yield event.plain_result(f"学生数据查询失败：{exc}")
            return

        if self.schaledb.last_error:
            self._log_warn(
                "student", "SchaleDB used fallback data", detail=self.schaledb.last_error
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
            card_bytes = render_student_detail(student)
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
            yield event.image_result(str(self._store_card(card.render())))
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
                    "pool", "student portraits could not be matched", count=missing_portraits
                )
            card = CardPoolCard(
                pools=pools,
                region=server_region,
                student_icons=student_icons,
            )
            yield event.image_result(str(self._store_card(card.render())))
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
        if args.strip():
            yield event.plain_result("学生生日数据为全服通用，无需指定服务器区域。用法：生日")
            return

        language = str(self._get_setting("student_data_language") or "cn")
        try:
            birthday_records = await self.schaledb.get_birthdays_with_ids(language)
        except SchaleDBError as e:
            self._log_error("birthday", "fetch failed", error=e)
            yield event.plain_result(f"获取生日数据失败：{e}")
            return

        if self.schaledb.last_error:
            self._log_warn(
                "birthday", "SchaleDB used fallback data", detail=self.schaledb.last_error
            )
        if not birthday_records:
            yield event.plain_result("暂无生日数据。")
            return

        birthday_students = [
            (name, birthday) for name, birthday, _ in birthday_records
        ]
        today = (datetime.now().month, datetime.now().day)

        def upcoming_key(record: tuple[str, str]) -> tuple[int, int, int, str]:
            match = re.search(r"(\d{1,2})\s*(?:/|月|-)\s*(\d{1,2})", record[1])
            if not match:
                return 2, 13, 32, record[0]
            month, day = int(match.group(1)), int(match.group(2))
            return (0 if (month, day) >= today else 1, month, day, record[0])

        students = sorted(birthday_students, key=upcoming_key)[:24]
        records_by_name = {
            name: student_id for name, _, student_id in birthday_records
        }
        mode = str(self._get_setting("message_mode", "card"))
        if mode == "text":
            lines = [f"学生生日列表（共 {len(birthday_students)} 位）："]
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
                {
                    name
                    for name, _ in students
                    if records_by_name.get(name) is not None
                }
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
            )
            yield event.image_result(str(self._store_card(card.render())))
            if mode == "mixed":
                lines = [f"学生生日列表（共 {len(birthday_students)} 位）："]
                lines.extend(f"• {name}：{birthday}" for name, birthday in students)
                yield event.plain_result("\n".join(lines))
        except Exception as exc:
            self._log_error("birthday", "render failed", error=exc)
            yield event.plain_result("学生生日卡片渲染失败，请稍后重试。")

    # ------------------------------------------------------------------
    # 攻略查询（Arona）
    # ------------------------------------------------------------------

    async def _handle_guide(self, event: AstrMessageEvent, name: str):
        """查询学生攻略图。"""
        if not name:
            yield event.plain_result(
                "BA 攻略查询\n用法：攻略 <学生名>\n例如：攻略 日富美（赛车）"
            )
            return

        try:
            guide_url = await self.arona.get_guide_image(name)
        except AronaAmbiguousError as e:
            yield event.plain_result(str(e))
            return
        except Exception as e:
            self._log_error("guide", "fetch failed", error=e)
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
            if separator and umo.strip() == event_umo and region in (
                "cn",
                "global",
                "jp",
                "all",
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
