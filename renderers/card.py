"""
HTML-only BlueArchive card renderers（融合数据版）。

布局风格：左侧立绘/头像 + 右侧信息板块，半透明子卡片背景，
字段缺失时自动隐藏对应板块，不强制全填。
"""

from __future__ import annotations

import html
import mimetypes
from datetime import datetime
from pathlib import Path

from .html_renderer import (
    BA_ASSETS,
    HTMLCardRenderer,
    FONT_CSS,
    _asset_uri,
)


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


def _format_timestamp(value: object) -> str:
    try:
        timestamp = int(value)
        if timestamp <= 0:
            return ""
        return datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


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
        self.CARD_H = max(220, 142 + len(self.events) * 62)
        self._renderer = HTMLCardRenderer()

    def render(self) -> bytes:
        return self._renderer.render(self._build_html(), self.CARD_W, self.CARD_H)

    def _build_html(self) -> str:
        region_names = {
            "cn": "国服",
            "global": "国际服",
            "jp": "日服",
            "all": "全服",
        }
        rows = []
        for event in self.events:
            start = _format_timestamp(event.get("start_at"))
            end = _format_timestamp(event.get("end_at"))
            date = f"{start} — {end}" if start and end else start or end
            title = event.get("title") or "未命名活动"
            subtitle = event.get("pub_area") or ""
            avatar_images = []
            event_students = event.get("students", [])
            if isinstance(event_students, list):
                for name in event_students:
                    icon_path = self.student_icons.get(name)
                    icon_uri = _local_image_uri(icon_path)
                    if icon_uri:
                        avatar_images.append(
                            f'<img class="row-avatar" src="{_escape(icon_uri)}" alt="">'
                        )
            avatar_markup = "".join(avatar_images)
            marker = (
                f'<div class="row-avatars">{avatar_markup}</div>'
                if avatar_markup
                else '<span class="marker"></span>'
            )
            rows.append(
                f'<article class="row">{marker}<div class="row-main">'
                f"<b>{_escape(title)}</b>"
                f'<small>{_escape(subtitle)}</small></div>'
                f'<time>{_escape(date or self.date)}</time></article>'
            )
        if not rows and self.title != "活动日程":
            detail = "、".join(self.rate_up)
            rows.append(
                f'<article class="row"><span class="marker"></span><div class="row-main">'
                f"<b>{_escape(self.title)}</b><small>{_escape(detail)}</small></div>"
                f'<time>{_escape(self.date)}</time></article>'
            )
        content = "".join(rows) or '<p class="empty">暂时没有可展示的数据</p>'
        card_bg_uri = _asset_uri("img/ui/cards/Card_Bg.png")
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
{FONT_CSS}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; width: {self.CARD_W}px; height: {self.CARD_H}px; font-family: 'ResourceHanRounded','Blueaka','Microsoft YaHei',sans-serif; color: #334459; }}
body {{ background: linear-gradient(135deg,#eaf5fa,#f4f0fa); }}
.card {{ position: relative; min-height: 100%; padding: 22px 26px; overflow: hidden; }}
.backdrop {{ position: absolute; inset: 0; background: url('{card_bg_uri}') center/cover; opacity: .16; }}
.content {{ position: relative; z-index: 1; }}
.header {{ display: flex; align-items: end; justify-content: space-between; margin-bottom: 14px; }}
h1 {{ margin: 0; color: #30455f; font-size: 28px; }}
.region {{ padding: 5px 12px; border-radius: 14px; background: #d8edf8; color: #2781ad; font-size: 13px; }}
.rows {{ display: grid; gap: 7px; }}
.row {{ display: flex; align-items: center; gap: 12px; min-height: 48px; padding: 7px 12px; border-radius: 11px; background: rgba(255,255,255,.82); }}
.marker {{ width: 9px; height: 9px; flex: 0 0 9px; border-radius: 50%; background: #45a5ce; box-shadow: 0 0 0 4px #dceff6; }}
.row-avatars {{ display: flex; flex: 0 0 auto; align-items: center; padding-left: 2px; }}
.row-avatar {{ width: 34px; height: 34px; margin-left: -3px; border: 2px solid #fff; border-radius: 50%; background: #e8f1f6; object-fit: cover; box-shadow: 0 1px 3px rgba(48,77,97,.16); }}
.row-main {{ display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 2px; }}
.row-main b {{ overflow-wrap: anywhere; font-size: 15px; }}
.row-main small {{ color: #8694a4; font-size: 11px; }}
time {{ color: #63778e; font-size: 12px; white-space: nowrap; }}
.empty {{ padding: 24px; text-align: center; color: #8290a1; }}
</style></head><body><main class="card"><div class="backdrop"></div><div class="content">
<header class="header"><h1>{_escape(self.title)}</h1><span class="region">{_escape(region_names.get(self.region, self.region))}</span></header>
<section class="rows">{content}</section></div></main></body></html>"""


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


class BirthdayCard:
    CARD_W = 720

    def __init__(
        self,
        students: list[tuple[str, str]] | None = None,
        student_icons: dict[str, str] | None = None,
    ):
        self.students = students or []
        self.student_icons = student_icons or {}
        row_count = (min(len(self.students), 24) + 1) // 2
        grid_height = row_count * 48 + max(0, row_count - 1) * 8
        self.CARD_H = max(220, 148 + grid_height)
        self._renderer = HTMLCardRenderer()

    def render(self) -> bytes:
        return self._renderer.render(self._build_html(), self.CARD_W, self.CARD_H)

    def _build_html(self) -> str:
        rows = ""
        for name, birthday in self.students[:24]:
            icon_path = self.student_icons.get(name)
            icon_uri = _local_image_uri(icon_path)
            avatar = (
                f'<img class="birthday-avatar" src="{_escape(icon_uri)}" alt="">'
                if icon_uri
                else '<span class="birthday-mark">✦</span>'
            )
            rows += (
                f'<article class="student">{avatar}'
                f'<b>{_escape(name)}</b><time>{_escape(birthday)}</time></article>'
            )
        content = rows or '<p class="empty">暂无生日数据</p>'
        card_bg_uri = _asset_uri("img/ui/cards/Card_Bg.png")
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
{FONT_CSS}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; width: {self.CARD_W}px; height: {self.CARD_H}px; font-family: 'ResourceHanRounded','Blueaka','Microsoft YaHei',sans-serif; color: #334459; }}
body {{ background: linear-gradient(135deg,#fff1e5,#f7f0f8); }}
.card {{ position: relative; min-height: 100%; padding: 22px 26px; overflow: hidden; }}
.backdrop {{ position: absolute; inset: 0; background: url('{card_bg_uri}') center/cover; opacity: .16; }}
.content {{ position: relative; z-index: 1; }}
h1 {{ margin: 0 0 14px; color: #b66b49; font-size: 28px; }}
.students {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
.student {{ display: flex; align-items: center; gap: 9px; min-width: 0; min-height: 42px; padding: 7px 12px; border-radius: 10px; background: rgba(255,255,255,.83); }}
.student b {{ flex: 1; overflow-wrap: anywhere; font-size: 14px; }}
.birthday-avatar {{ width: 34px; height: 34px; flex: 0 0 34px; border-radius: 50%; background: #e8f1f6; object-fit: cover; }}
.birthday-mark {{ color: #e5a84e; }}
time {{ color: #8d7b79; font-size: 12px; }}
.empty {{ padding: 24px; text-align: center; color: #8290a1; }}
</style></head><body><main class="card"><div class="backdrop"></div><div class="content">
<h1>学生生日列表 · 共 {len(self.students)} 位</h1><section class="students">{content}</section>
</div></main></body></html>"""


def render_schedule_card(**kwargs) -> bytes:
    return ScheduleCard(**kwargs).render()


def render_birthday_card(**kwargs) -> bytes:
    return BirthdayCard(**kwargs).render()
