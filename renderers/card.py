"""
HTML-only BlueArchive card renderers（融合数据版）。

布局风格：左侧立绘/头像 + 右侧信息板块，半透明子卡片背景，
字段缺失时自动隐藏对应板块，不强制全填。
"""

from __future__ import annotations

import calendar
import html
import mimetypes
import re
from datetime import datetime
from pathlib import Path

from .html_renderer import (
    BA_ASSETS,
    HTMLCardRenderer,
    FONT_CSS,
    _asset_uri,
)

DASHBOARD_ICON_DIR = BA_ASSETS.parent / "views" / "ba-toolbox" / "assets" / "icons"


def _local_image_uri(path: str | None) -> str:
    if not path:
        return ""
    p = Path(path)
    try:
        rel = p.resolve().relative_to(BA_ASSETS.resolve())
        return _asset_uri(rel.as_posix())
    except ValueError:
        pass
    if p.exists() and p.is_file():
        import base64

        mime = {
            ".webp": "image/webp",
            ".png": "image/png",
        }.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"
    return ""


def _escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _estimated_lines(value: str, characters_per_line: int) -> int:
    return max(1, (len(value) + characters_per_line - 1) // characters_per_line)


def _card_region_label(region: str) -> str:
    return {
        "cn": "国服",
        "global": "国际服",
        "jp": "日服",
        "all": "全服",
    }.get(region, region)


def parse_birthday_date(value: object) -> tuple[int, int] | None:
    match = re.fullmatch(
        r"\s*(\d{1,2})\s*(?:/|月|-)\s*(\d{1,2})日?\s*",
        str(value or ""),
    )
    if not match:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return month, day


class ScheduleCard:
    CARD_W, CARD_H = 720, 260

    def __init__(
        self,
        title: str = "活动日程",
        date: str = "",
        rate_up: list[str] | None = None,
        rarity: int = 3,
        events: list[dict] | None = None,
        region: str = "cn",
        student_icons: dict[str, str] | None = None,
    ):
        self.title = title
        self.date = date
        self.rate_up = rate_up or []
        self.rarity = rarity
        self.events = events or []
        self.region = region
        self.student_icons = student_icons or {}
        self.event_groups = self._group_events(self.events)
        row_heights = [
            self._event_row_height(event)
            for server_events in self.event_groups.values()
            for event in server_events
        ]
        if not row_heights and self.title != "活动日程":
            title_lines = _estimated_lines(self.title, 26)
            detail_lines = _estimated_lines("、".join(self.rate_up), 32)
            row_heights.append(max(58, 24 + title_lines * 19 + detail_lines * 15))
        self.CARD_H = max(
            220,
            146
            + sum(row_heights)
            + len(self.event_groups) * 30
            + max(0, len(self.event_groups) - 1) * 14,
        )
        self._renderer = HTMLCardRenderer()

    @staticmethod
    def _event_timestamp(event: dict, key: str) -> int:
        try:
            return max(0, int(event.get(key) or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    @classmethod
    def _group_events(cls, events: list[dict]) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        for event in events:
            server = str(event.get("pub_area") or "").strip() or "未知服务器"
            groups.setdefault(server, []).append(event)

        server_order = {"国服": 0, "国际服": 1, "日服": 2}
        return {
            server: sorted(
                server_events,
                key=lambda event: (
                    cls._event_timestamp(event, "start_at")
                    or cls._event_timestamp(event, "end_at")
                    or float("inf"),
                    cls._event_timestamp(event, "end_at") or float("inf"),
                ),
            )
            for server, server_events in sorted(
                groups.items(),
                key=lambda item: (server_order.get(item[0], 3), item[0]),
            )
        }

    @classmethod
    def _event_date_range(cls, event: dict) -> tuple[str, str]:
        def format_date(value: int) -> str:
            if not value:
                return "待定"
            try:
                return datetime.fromtimestamp(value).strftime("%m月%d日")
            except (OSError, OverflowError, ValueError):
                return "待定"

        return (
            format_date(cls._event_timestamp(event, "start_at")),
            format_date(cls._event_timestamp(event, "end_at")),
        )

    @classmethod
    def _event_row_height(cls, event: dict) -> int:
        title_lines = _estimated_lines(
            str(event.get("title") or "未命名活动"), 28
        )
        subtitle = str(event.get("subtitle") or event.get("description") or "")
        subtitle_lines = _estimated_lines(subtitle, 42) if subtitle else 0
        return max(66, 22 + title_lines * 19 + subtitle_lines * 15)

    def render(self) -> bytes:
        return self._renderer.render(
            self._build_html(), self.CARD_W, self.CARD_H, framed=False
        )

    def _build_html(self) -> str:
        groups = []
        check_icon = _asset_uri("img/ui/Common_Icon_Check_Y_On.png")
        for server, events in self.event_groups.items():
            rows = []
            for event in events:
                start, end = self._event_date_range(event)
                title = event.get("title") or "未命名活动"
                subtitle = event.get("subtitle") or event.get("description") or ""
                avatar_images = []
                event_students = event.get("students", [])
                if isinstance(event_students, list):
                    for name in event_students[:4]:
                        icon_uri = _local_image_uri(self.student_icons.get(name))
                        if icon_uri:
                            avatar_images.append(
                                f'<img class="row-avatar" src="{_escape(icon_uri)}" alt="">'
                            )
                    if len(event_students) > 4:
                        avatar_images.append(
                            f'<span class="avatar-extra">+{len(event_students) - 4}</span>'
                        )
                avatar_markup = "".join(avatar_images)
                subtitle_markup = (
                    f'<small>{_escape(subtitle)}</small>' if subtitle else ""
                )
                rows.append(
                    f'<article class="schedule-row"><div class="schedule-dates">'
                    f'<span class="schedule-date-caption">活动档期</span>'
                    f'<time>{_escape(start)}<i>至</i>{_escape(end)}</time>'
                    f'</div>'
                    f'<div class="schedule-timeline"><span></span></div>'
                    f'<div class="schedule-event"><img class="event-check" '
                    f'src="{_escape(check_icon)}" alt="">'
                    f'<div class="row-main"><b>{_escape(title)}</b>'
                    f'{subtitle_markup}</div>'
                    f'<div class="row-avatars">{avatar_markup}</div></div></article>'
                )
            groups.append(
                f'<section class="server-group"><h2 class="server-heading">'
                f'{_escape(server)}</h2><div class="schedule-list">'
                f'{"".join(rows)}</div></section>'
            )
        if not groups and self.title != "活动日程":
            detail = "、".join(self.rate_up)
            groups.append(
                f'<section class="server-group"><h2 class="server-heading">'
                f'{_escape(_card_region_label(self.region))}</h2>'
                f'<article class="schedule-row"><div class="schedule-dates">'
                f'<span class="schedule-date-caption">活动档期</span>'
                f'<time>{_escape(self.date or "待定")}</time>'
                f'</div>'
                f'<div class="schedule-timeline"><span></span></div>'
                f'<div class="schedule-event"><img class="event-check" '
                f'src="{_escape(check_icon)}" alt=""><div class="row-main">'
                f'<b>{_escape(self.title)}</b><small>{_escape(detail)}</small>'
                f'</div></div></article></section>'
            )
        content = "".join(groups) or '<p class="empty">暂时没有可展示的数据</p>'
        card_bg_uri = _asset_uri("img/ui/cards/Card_Bg.png")
        dashboard_icon = _local_image_uri(
            str(DASHBOARD_ICON_DIR / "collectible_icon_guidemission_s15_1.webp")
        )
        event_icon = _asset_uri("img/icons/Common_Icon_Event.png")
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
{FONT_CSS}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; width: {self.CARD_W}px; min-height: {self.CARD_H}px; font-family: 'ResourceHanRounded','BA Compact','Blueaka','Microsoft YaHei','Noto Sans CJK SC',sans-serif; color: #334459; }}
body {{ background: linear-gradient(135deg,#eaf5fa,#f4f0fa); }}
.card {{ position: relative; min-height: {self.CARD_H}px; overflow: hidden; padding: 22px 26px 26px; border: 1px solid rgba(99,154,182,.18); border-radius: 20px; background: linear-gradient(135deg,rgba(251,254,255,.92),rgba(245,248,255,.9)); box-shadow: 0 8px 24px rgba(67,117,153,.09); }}
.backdrop {{ position: absolute; inset: 0; background: url('{card_bg_uri}') center/cover no-repeat; transform: scale(1.35); opacity: .2; }}
.content {{ position: relative; z-index: 1; }}
.header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }}
h1 {{ margin: 0; color: #30455f; font-size: 27px; letter-spacing: .3px; }}
.header-copy {{ display: flex; align-items: center; gap: 12px; }}
.header-mark {{ display: grid; width: 48px; height: 44px; flex: 0 0 48px; place-items: center; border: 1px solid rgba(67,157,191,.12); border-radius: 14px; background: linear-gradient(145deg,#e4f6fc,#f4f1fb); }}
.header-mark img {{ width: 42px; height: 36px; object-fit: contain; }}
.header-title {{ display: flex; flex-direction: column; }}
.header-caption {{ display: flex; align-items: center; gap: 6px; max-width: 100%; margin-top: 3px; overflow: hidden; color: #8493a3; font-size: 11px; font-style: italic; white-space: nowrap; }}
.header-code {{ flex: 0 0 auto; color: #438eae; font-size: 9px; font-style: italic; font-weight: 700; letter-spacing: .1em; white-space: nowrap; }}
.header-caption img {{ width: 42px; height: 13px; object-fit: contain; }}
.region {{ padding: 6px 12px; border: 1px solid rgba(58,151,192,.12); border-radius: 14px; background: #e1f2f9; color: #2781ad; font-size: 12px; font-weight: 700; }}
.server-group + .server-group {{ margin-top: 14px; }}
.server-heading {{ display: flex; align-items: center; gap: 9px; margin: 0 0 8px; color: #3787aa; font-size: 13px; }}
.server-heading::after {{ height: 1px; flex: 1; background: linear-gradient(90deg,rgba(74,157,190,.25),transparent); content: ""; }}
.schedule-list {{ display: grid; overflow: hidden; border: 1px solid rgba(76,132,161,.1); border-radius: 15px; background: rgba(255,255,255,.91); box-shadow: 0 3px 9px rgba(48,77,97,.045); }}
.schedule-row {{ display: grid; grid-template-columns: 112px 18px minmax(0,1fr); align-items: stretch; gap: 10px; min-height: 66px; padding: 9px 12px; }}
.schedule-dates {{ display: flex; flex-direction: column; justify-content: center; padding: 4px 7px; border: 1px solid rgba(61,148,185,.12); border-radius: 11px; background: linear-gradient(145deg,#edf8fc,#f4f3fb); text-align: center; }}
.schedule-date-caption {{ margin-bottom: 3px; color: #92a1ac; font-size: 9px; }}
.schedule-dates time {{ display: flex; flex-direction: column; align-items: center; color: #356f8e; font-size: 11px; font-weight: 700; line-height: 1.35; }}
.schedule-dates time i {{ color: #8ca2af; font-size: 9px; font-style: normal; font-weight: 400; }}
.schedule-timeline {{ position: relative; z-index: 2; display: grid; place-items: center; }}
.schedule-timeline::before {{ position: absolute; top: -10px; bottom: -10px; left: 50%; width: 2px; background: #b7dce8; content: ""; }}
.schedule-row:first-child .schedule-timeline::before {{ top: 50%; }}
.schedule-row:last-child .schedule-timeline::before {{ bottom: 50%; }}
.schedule-timeline span {{ z-index: 1; width: 10px; height: 10px; border: 3px solid #fff; border-radius: 50%; background: #55a9c8; box-shadow: 0 0 0 2px #cceaf2; }}
.schedule-event {{ display: flex; min-width: 0; align-items: center; gap: 10px; }}
.event-check {{ width: 23px; height: 23px; flex: 0 0 23px; object-fit: contain; }}
.row-avatars {{ display: flex; flex: 0 0 auto; align-items: center; padding-left: 2px; }}
.row-avatar {{ width: 32px; height: 32px; margin-left: -3px; border: 2px solid #fff; border-radius: 50%; background: #e8f1f6; object-fit: cover; box-shadow: 0 1px 3px rgba(48,77,97,.16); }}
.avatar-extra {{ display: grid; width: 30px; height: 30px; margin-left: -3px; place-items: center; border: 2px solid #fff; border-radius: 50%; background: #e8f1f6; color: #60788d; font-size: 10px; }}
.row-main {{ display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 2px; }}
.row-main b {{ overflow-wrap: anywhere; color: #30455f; font-size: 14px; line-height: 1.35; }}
.row-main small {{ color: #8694a4; font-size: 11px; line-height: 1.3; }}
.empty {{ padding: 30px 24px; border-radius: 13px; background: rgba(255,255,255,.8); text-align: center; color: #8290a1; }}
</style></head><body><main class="card"><div class="backdrop"></div><div class="content">
<header class="header"><div class="header-copy"><span class="header-mark"><img src="{_escape(dashboard_icon)}" alt=""></span>
<div class="header-title"><h1>{_escape(self.title)}</h1>
<span class="header-caption"><span class="header-code">TACTICAL SCHEDULE</span><img src="{_escape(event_icon)}" alt=""><span>近期活动安排</span></span></div></div><span class="region">{_escape(_card_region_label(self.region))}</span></header>
{content}</div></main></body></html>"""


class CardPoolCard(ScheduleCard):
    def __init__(
        self,
        pools: list[dict],
        region: str = "cn",
        student_icons: dict[str, str] | None = None,
    ):
        super().__init__(
            title="卡池千里眼",
            events=pools,
            region=region,
            student_icons=student_icons,
        )
        self.pool_groups = self._group_pools(self.events)
        self.CARD_H = max(
            220,
            154
            + sum(
                30
                + sum(self._pool_row_height(pool) for pool in group_pools)
                for group_pools in self.pool_groups.values()
            )
            + max(0, len(self.pool_groups) - 1) * 17,
        )

    @staticmethod
    def _pool_student_names(pool: dict) -> list[str]:
        students = pool.get("students")
        if isinstance(students, list):
            return list(dict.fromkeys(str(name).strip() for name in students if str(name).strip()))
        title = re.sub(r"^卡池\s*[:：]\s*", "", str(pool.get("title") or ""))
        return list(dict.fromkeys(name.strip() for name in re.split(r"[、，,]", title) if name.strip()))

    @staticmethod
    def _pool_timestamp(pool: dict, key: str) -> int:
        try:
            return max(0, int(pool.get(key) or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    @classmethod
    def _group_pools(cls, pools: list[dict]) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        for pool in pools:
            server = str(pool.get("pub_area") or "").strip() or "未知服务器"
            groups.setdefault(server, []).append(pool)

        server_order = {"国服": 0, "国际服": 1, "日服": 2}
        return {
            server: sorted(
                group_pools,
                key=lambda pool: (
                    cls._pool_timestamp(pool, "start_at")
                    or cls._pool_timestamp(pool, "end_at")
                    or float("inf"),
                    cls._pool_timestamp(pool, "end_at") or float("inf"),
                ),
            )
            for server, group_pools in sorted(
                groups.items(),
                key=lambda item: (server_order.get(item[0], 3), item[0]),
            )
        }

    @classmethod
    def _pool_date_range(cls, pool: dict) -> tuple[str, str]:
        def format_date(value: int) -> str:
            if not value:
                return "待定"
            try:
                return datetime.fromtimestamp(value).strftime("%m月%d日")
            except (OSError, OverflowError, ValueError):
                return "待定"

        return (
            format_date(cls._pool_timestamp(pool, "start_at")),
            format_date(cls._pool_timestamp(pool, "end_at")),
        )

    @classmethod
    def _pool_row_height(cls, pool: dict) -> int:
        names = cls._pool_student_names(pool)
        columns = 3
        member_rows = [
            names[index:index + columns]
            for index in range(0, len(names), columns)
        ]
        member_height = sum(
            max(
                42,
                max((_estimated_lines(name, 10) * 15 + 12 for name in row), default=0),
            )
            for row in member_rows
        ) + max(0, len(member_rows) - 1) * 8
        return max(80, 68 + member_height)

    def _build_html(self) -> str:
        groups = []
        for server, pools in self.pool_groups.items():
            rows = []
            for pool in pools:
                students = self._pool_student_names(pool)
                start, end = self._pool_date_range(pool)
                member_cards = []
                for name in students:
                    icon_uri = _local_image_uri(self.student_icons.get(name))
                    avatar = (
                        f'<img class="pool-avatar" src="{_escape(icon_uri)}" alt="">'
                        if icon_uri
                        else '<span class="pool-avatar-fallback">✦</span>'
                    )
                    member_cards.append(
                        f'<div class="pool-member">{avatar}'
                        f'<span class="pool-member-name">{_escape(name)}</span></div>'
                    )
                members = "".join(member_cards) or '<p class="pool-empty">暂无招募学生信息</p>'
                rows.append(
                    f'<article class="pool-row"><div class="pool-dates">'
                    f'<span class="pool-date-caption">招募档期</span>'
                    f'<time>{_escape(start)}<i>至</i>{_escape(end)}</time>'
                    f'</div>'
                    f'<div class="pool-timeline"><span></span></div>'
                    f'<div class="pool-members">{members}</div></article>'
                )
            group_heading = (
                f'<h2 class="server-heading">{_escape(server)}</h2>'
                if self.region == "all"
                else ""
            )
            groups.append(
                f'<section class="server-group">{group_heading}'
                f'<div class="pool-list">{"".join(rows)}</div></section>'
            )
        content = "".join(groups) or '<p class="empty">暂时没有可展示的卡池</p>'
        card_bg_uri = _asset_uri("img/ui/cards/Card_Bg.png")
        dashboard_icon = _local_image_uri(
            str(DASHBOARD_ICON_DIR / "collectible_icon_guidemission01.webp")
        )
        student_count = sum(len(self._pool_student_names(pool)) for pool in self.events)
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
{FONT_CSS}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; width: {self.CARD_W}px; min-height: {self.CARD_H}px; font-family: 'ResourceHanRounded','BA Compact','Blueaka','Microsoft YaHei','Noto Sans CJK SC',sans-serif; color: #334459; }}
body {{ background: linear-gradient(135deg,#eaf5fa,#edf1fb); }}
.card {{ position: relative; min-height: {self.CARD_H}px; overflow: hidden; padding: 22px 26px 26px; border: 1px solid rgba(99,154,182,.18); border-radius: 20px; background: linear-gradient(135deg,rgba(251,254,255,.92),rgba(245,248,255,.9)); box-shadow: 0 8px 24px rgba(67,117,153,.09); }}
.backdrop {{ position: absolute; inset: 0; background: url('{card_bg_uri}') center/cover no-repeat; transform: scale(1.35); opacity: .2; }}
.content {{ position: relative; z-index: 1; }}
.header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }}
.header-copy {{ display: flex; align-items: center; gap: 12px; }}
.header-mark {{ display: grid; width: 48px; height: 44px; flex: 0 0 48px; place-items: center; border: 1px solid rgba(67,157,191,.12); border-radius: 14px; background: linear-gradient(145deg,#e4f6fc,#f4f1fb); }}
.header-mark img {{ width: 42px; height: 36px; object-fit: contain; }}
.header-title {{ display: flex; flex-direction: column; }}
h1 {{ margin: 0; color: #30455f; font-size: 27px; letter-spacing: .3px; }}
.header-caption {{ display: flex; align-items: center; gap: 6px; max-width: 100%; margin-top: 3px; overflow: hidden; color: #8493a3; font-size: 11px; font-style: italic; white-space: nowrap; }}
.header-code {{ flex: 0 0 auto; color: #438eae; font-size: 9px; font-style: italic; font-weight: 700; letter-spacing: .1em; white-space: nowrap; }}
.region {{ padding: 6px 12px; border: 1px solid rgba(58,151,192,.12); border-radius: 14px; background: #e1f2f9; color: #2781ad; font-size: 12px; font-weight: 700; }}
.pool-summary {{ margin: -4px 0 14px; color: #73869a; font-size: 11px; }}
.server-group + .server-group {{ margin-top: 16px; }}
.server-heading {{ display: flex; align-items: center; gap: 9px; margin: 0 0 8px; color: #3787aa; font-size: 13px; }}
.server-heading::after {{ height: 1px; flex: 1; background: linear-gradient(90deg,rgba(74,157,190,.25),transparent); content: ""; }}
.pool-list {{ display: grid; overflow: hidden; border: 1px solid rgba(76,132,161,.1); border-radius: 15px; background: rgba(255,255,255,.91); box-shadow: 0 3px 9px rgba(48,77,97,.045); }}
.pool-row {{ display: grid; grid-template-columns: 126px 18px minmax(0,1fr); align-items: stretch; gap: 10px; min-height: 80px; padding: 10px 12px; }}
.pool-dates {{ display: flex; flex-direction: column; justify-content: center; padding: 4px 9px; border: 1px solid rgba(61,148,185,.14); border-radius: 12px; background: linear-gradient(145deg,#edf8fc,#f4f3fb); text-align: center; }}
.pool-dates time {{ display: flex; flex-direction: column; align-items: center; margin-top: 4px; color: #356f8e; font-size: 13px; font-weight: 700; line-height: 1.4; }}
.pool-dates time i {{ color: #8ca2af; font-size: 9px; font-style: normal; font-weight: 400; }}
.pool-date-caption {{ margin-bottom: 1px; color: #92a1ac; font-size: 9px; }}
.pool-timeline {{ position: relative; z-index: 2; display: grid; place-items: center; }}
.pool-timeline::before {{ position: absolute; top: -11px; bottom: -11px; left: 50%; width: 2px; background: #b7dce8; content: ""; }}
.pool-row:first-child .pool-timeline::before {{ top: 50%; }}
.pool-row:last-child .pool-timeline::before {{ bottom: 50%; }}
.pool-timeline span {{ z-index: 1; width: 11px; height: 11px; border: 3px solid #fff; border-radius: 50%; background: #55a9c8; box-shadow: 0 0 0 2px #cceaf2; }}
.pool-members {{ display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); align-content: center; gap: 7px 8px; min-width: 0; }}
.pool-member {{ display: flex; min-width: 0; min-height: 42px; align-items: center; gap: 8px; padding: 5px 8px; border: 1px solid #edf2f5; border-radius: 12px; background: #f9fbfd; }}
.pool-avatar,.pool-avatar-fallback {{ display: grid; width: 31px; height: 31px; flex: 0 0 31px; place-items: center; border-radius: 50%; background: #e5eff5; object-fit: cover; }}
.pool-avatar-fallback {{ color: #79a8be; font-size: 12px; }}
.pool-member-name {{ min-width: 0; color: #40546b; font-size: 11px; line-height: 1.3; overflow-wrap: anywhere; }}
.pool-empty {{ grid-column: 1/-1; margin: 4px 0; color: #8a9aaa; font-size: 11px; }}
.empty {{ padding: 30px 24px; border-radius: 13px; background: rgba(255,255,255,.8); text-align: center; color: #8290a1; }}
</style></head><body><main class="card"><div class="backdrop"></div><div class="content">
<header class="header"><div class="header-copy"><span class="header-mark"><img src="{_escape(dashboard_icon)}" alt=""></span>
<div class="header-title"><h1>卡池千里眼</h1>
<span class="header-caption"><span class="header-code">TACTICAL RECRUIT</span><span>按服务器与日期整理的招募进度</span></span></div></div>
<span class="region">{_escape(_card_region_label(self.region))}</span></header>
<p class="pool-summary">共 {len(self.events)} 个招募档期 · {student_count} 个学生位</p>
{content}</div></main></body></html>"""


class BirthdayCard:
    CARD_W = 720

    def __init__(
        self,
        students: list[tuple[str, str]] | None = None,
        student_icons: dict[str, str] | None = None,
        month: int | None = None,
        year: int | None = None,
        today: datetime | None = None,
    ):
        now = today or datetime.now()
        self.month = now.month if month is None else month
        self.year = now.year if year is None else year
        self.today = now.day if (self.month, self.year) == (now.month, now.year) else None
        if not 1 <= self.month <= 12:
            raise ValueError("Birthday calendar month must be between 1 and 12.")
        self.students = [
            (name, birthday)
            for name, birthday in (students or [])
            if (birthday_date := parse_birthday_date(birthday))
            and birthday_date[0] == self.month
        ]
        self.student_icons = student_icons or {}
        self.students_by_day: dict[int, list[tuple[str, str]]] = {}
        for student in self.students:
            birthday_date = parse_birthday_date(student[1])
            if birthday_date:
                self.students_by_day.setdefault(birthday_date[1], []).append(student)
        self.calendar_weeks = calendar.Calendar(firstweekday=6).monthdayscalendar(
            self.year, self.month
        )
        self.week_heights = [
            max(
                72,
                42
                + max(
                    (
                        len(self.students_by_day.get(day, []))
                        for day in week
                        if day
                    ),
                    default=0,
                )
                * 40,
            )
            for week in self.calendar_weeks
        ]
        self.CARD_H = max(
            220,
            210 + sum(self.week_heights) + len(self.calendar_weeks) * 8,
        )
        self._renderer = HTMLCardRenderer()

    def render(self) -> bytes:
        return self._renderer.render(
            self._build_html(), self.CARD_W, self.CARD_H, framed=False
        )

    def _build_html(self) -> str:
        weekday_names = (
            ("日", "Sun"),
            ("一", "Mon"),
            ("二", "Tue"),
            ("三", "Wed"),
            ("四", "Thu"),
            ("五", "Fri"),
            ("六", "Sat"),
        )
        weekdays = "".join(
            f'<span class="weekday"><span>周{weekday}</span><small>{english}</small></span>'
            for weekday, english in weekday_names
        )
        weeks = []
        for week in self.calendar_weeks:
            days = []
            for weekday_index, day in enumerate(week):
                if not day:
                    days.append('<div class="day-cell empty-day"></div>')
                    continue
                entries = []
                for name, _ in self.students_by_day.get(day, []):
                    icon_uri = _local_image_uri(self.student_icons.get(name))
                    avatar = (
                        f'<img class="birthday-avatar" src="{_escape(icon_uri)}" alt="">'
                        if icon_uri
                        else '<span class="birthday-mark">✦</span>'
                    )
                    entries.append(
                        f'<div class="birthday-entry" title="{_escape(name)}">'
                        f'{avatar}<span>{_escape(name)}</span></div>'
                    )
                birthdays = "".join(entries)
                birthday_class = " has-birthday" if entries else ""
                today_class = " today" if day == self.today else ""
                weekend_class = " weekend" if weekday_index in (0, 6) else ""
                days.append(
                    f'<div class="day-cell{birthday_class}{today_class}{weekend_class}">'
                    f'<span class="day-number{" today-ring" if day == self.today else ""}">'
                    f'{day}</span>{birthdays}</div>'
                )
            weeks.append("".join(days))
        card_bg_uri = _asset_uri("img/ui/cards/Card_Bg.png")
        doodle_uris = {
            name: _asset_uri(f"img/ui/birthday/{name}.png")
            for name in ("girl", "mascot", "party_popper", "party_hat", "bunting")
        }
        dashboard_icon = _local_image_uri(
            str(DASHBOARD_ICON_DIR / "item_icon_favor_26.webp")
        )
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
{FONT_CSS}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; width: {self.CARD_W}px; min-height: {self.CARD_H}px; font-family: 'ResourceHanRounded','BA Compact','Blueaka','Microsoft YaHei','Noto Sans CJK SC',sans-serif; color: #334459; }}
body,body * {{ font-family: 'ResourceHanRounded','BA Compact','Blueaka','Microsoft YaHei','Noto Sans CJK SC',sans-serif; }}
body {{ background: linear-gradient(135deg,#fff4e9,#f7f0f8); }}
.card {{ position: relative; min-height: {self.CARD_H}px; overflow: hidden; padding: 22px 26px 26px; border: 1px solid rgba(190,132,98,.18); border-radius: 20px; background: linear-gradient(135deg,rgba(255,251,247,.94),rgba(250,246,253,.92)); box-shadow: 0 8px 24px rgba(128,87,64,.08); }}
.backdrop {{ position: absolute; inset: 0; background: url('{card_bg_uri}') center/cover no-repeat; transform: scale(1.35); opacity: .2; }}
.content {{ position: relative; z-index: 1; }}
header {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }}
.month-stamp {{ display: grid; width: 58px; height: 54px; flex: 0 0 58px; place-items: center; border: 2px solid #494644; background: #fffdf6; box-shadow: 3px 3px 0 rgba(87,69,55,.16); transform: rotate(-2deg); }}
.month-stamp strong {{ color: #292827; font-family: 'ResourceHanRounded','BA Compact','Blueaka','Microsoft YaHei','Noto Sans CJK SC',sans-serif; font-size: 40px; font-weight: 800; line-height: 1; }}
.header-copy {{ display: flex; min-width: 0; flex: 1; flex-direction: column; align-items: flex-start; gap: 0; }}
.header-title-row {{ display: flex; align-items: flex-start; gap: 10px; }}
.header-mark {{ display: grid; width: 46px; height: 44px; flex: 0 0 46px; place-items: center; border: 1px solid rgba(208,139,92,.16); border-radius: 14px; background: linear-gradient(145deg,#fff0e2,#fff7ed); }}
.header-mark img {{ width: 42px; height: 36px; object-fit: contain; }}
.birthday-logo {{ display: block; width: 205px; height: auto; margin-top: -3px; object-fit: contain; object-position: left center; }}
.header-caption {{ display: flex; align-items: center; gap: 7px; max-width: calc(100% - 56px); margin-top: -6px; margin-left: 56px; overflow: hidden; color: #9a8175; font-size: 11px; font-style: italic; white-space: nowrap; }}
.header-code {{ flex: 0 0 auto; color: #b87952; font-size: 9px; font-style: italic; font-weight: 700; letter-spacing: .1em; white-space: nowrap; }}
.calendar {{ position: relative; padding: 10px 12px 0; overflow: hidden; border: 1px solid #d9cbb8; border-radius: 7px; background: #fffdf7; box-shadow: inset 0 0 12px rgba(143,112,78,.06),0 4px 10px rgba(128,87,64,.09); }}
.calendar-doodle-background {{ position: absolute; z-index: 0; top: 2px; right: 14px; width: 138px; height: 58px; object-fit: contain; }}
.calendar-weekdays,.calendar-week {{ display: grid; grid-template-columns: repeat(7,minmax(0,1fr)); }}
.calendar-weekdays {{ position: relative; z-index: 1; margin-bottom: 8px; }}
.weekday {{ display: flex; flex-direction: column; align-items: center; gap: 1px; padding: 4px 0; color: #4f5558; font-size: 11px; font-weight: 700; text-align: center; }}
.weekday small {{ color: #8d9294; font-size: 9px; font-weight: 500; line-height: 1; }}
.calendar-week {{ position: relative; z-index: 1; min-height: var(--week-height); column-gap: 7px; margin-bottom: 8px; }}
.calendar-week:last-child {{ margin-bottom: 0; }}
.day-cell {{ position: relative; display: flex; min-width: 0; min-height: var(--week-height); flex-direction: column; align-items: center; gap: 3px; padding: 4px 2px 8px; background: transparent; }}
.day-cell.weekend,.empty-day {{ background: transparent; }}
.day-cell:not(.empty-day)::after {{ position: absolute; right: 7px; bottom: 0; left: 7px; height: 1px; background: #c5c6c3; content: ""; }}
.day-number {{ position: relative; z-index: 1; display: inline-flex; min-width: 22px; min-height: 18px; align-self: flex-start; align-items: center; justify-content: center; color: #353a3e; font-size: 11px; font-weight: 600; line-height: 1; }}
.calendar-week .day-cell:first-child .day-number {{ color: #d65b50; }}
.calendar-week .day-cell:last-child .day-number {{ color: #70a9d2; }}
.today-ring {{ color: #b52e2b !important; font-weight: 800; }}
.today-ring::before,.today-ring::after {{ position: absolute; z-index: -1; border: 2px solid #d6473f; border-radius: 48% 54% 47% 53%; content: ""; pointer-events: none; }}
.today-ring::before {{ inset: -2px -5px; transform: rotate(-13deg); }}
.today-ring::after {{ inset: -3px -4px -1px -5px; border-width: 1px; opacity: .7; transform: rotate(8deg); }}
.calendar-doodle-footer {{ position: relative; z-index: 1; display: flex; height: 92px; align-items: flex-end; justify-content: space-between; padding: 0 4px 7px; }}
.doodle-party-left {{ width: 44px; height: 54px; object-fit: contain; }}
.doodle-party-right {{ width: 36px; height: 46px; margin-left: 4px; object-fit: contain; }}
.doodle-character {{ width: 78px; height: 76px; object-fit: contain; }}
.doodle-mascot {{ width: 42px; height: 48px; margin-left: -5px; object-fit: contain; }}
.doodle-party-cluster {{ display: flex; height: 76px; align-items: flex-end; }}
.birthday-entry {{ display: flex; width: 100%; min-width: 0; flex-direction: column; align-items: center; gap: 1px; color: #4a4650; font-size: 9px; line-height: 1.15; }}
.birthday-entry * {{ font-family: inherit !important; }}
.birthday-entry span {{ max-width: 100%; overflow: hidden; font-size: 9px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }}
.birthday-avatar,.birthday-mark {{ width: 30px; height: 30px; flex: 0 0 30px; border-radius: 50%; background: #f5e9df; object-fit: cover; }}
.birthday-mark {{ display: grid; place-items: center; background: #fff0dc; color: #d99a4e; }}
</style></head><body><main class="card"><div class="backdrop"></div><div class="content">
<header><div class="header-copy"><div class="header-title-row"><span class="header-mark"><img src="{_escape(dashboard_icon)}" alt=""></span>
<img class="birthday-logo" src="{_escape(_asset_uri('img/ui/birthday/logo.png'))}" alt="学生生日日历"></div>
<div class="header-caption"><span class="header-code">STUDENT BIRTHDAY</span><span>可发送 “生日 月份” 查询对应月份日历</span></div></div>
<div class="month-stamp"><strong>{self.month:02d}</strong></div></header>
<section class="calendar">
<img class="calendar-doodle-background" src="{_escape(doodle_uris['bunting'])}" alt="">
<div class="calendar-weekdays">{weekdays}</div>{"".join(
    f'<div class="calendar-week" style="--week-height:{height}px">{week_markup}</div>'
    for height, week_markup in zip(self.week_heights, weeks)
) }
<div class="calendar-doodle-footer">
<img class="doodle-party-left" src="{_escape(doodle_uris['party_popper'])}" alt="">
<div class="doodle-party-cluster"><img class="doodle-character" src="{_escape(doodle_uris['girl'])}" alt="">
<img class="doodle-mascot" src="{_escape(doodle_uris['mascot'])}" alt="">
<img class="doodle-party-right" src="{_escape(doodle_uris['party_hat'])}" alt=""></div>
</div></section>
</div></main></body></html>"""


def render_schedule_card(**kwargs) -> bytes:
    return ScheduleCard(**kwargs).render()


def render_birthday_card(**kwargs) -> bytes:
    return BirthdayCard(**kwargs).render()
