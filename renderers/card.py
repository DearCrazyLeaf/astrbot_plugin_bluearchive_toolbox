"""
HTML-only BlueArchive card renderers（融合数据版）。

布局风格：左侧立绘/头像 + 右侧信息板块，半透明子卡片背景，
字段缺失时自动隐藏对应板块，不强制全填。
"""

from __future__ import annotations

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


def _format_timestamp(value: object) -> str:
    try:
        timestamp = int(value)
        if timestamp <= 0:
            return ""
        return datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _estimated_lines(value: str, characters_per_line: int) -> int:
    return max(1, (len(value) + characters_per_line - 1) // characters_per_line)


def _card_region_label(region: str) -> str:
    return {
        "cn": "国服",
        "global": "国际服",
        "jp": "日服",
        "all": "全服",
    }.get(region, region)


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
        row_heights = []
        for event in self.events:
            title = str(event.get("title") or "未命名活动")
            subtitle = str(event.get("pub_area") or "")
            title_lines = _estimated_lines(title, 26)
            subtitle_lines = _estimated_lines(subtitle, 32)
            row_heights.append(max(58, 24 + title_lines * 19 + subtitle_lines * 15))
        if not row_heights and self.title != "活动日程":
            title_lines = _estimated_lines(self.title, 26)
            detail_lines = _estimated_lines("、".join(self.rate_up), 32)
            row_heights.append(max(58, 24 + title_lines * 19 + detail_lines * 15))
        self.CARD_H = max(220, 118 + sum(row_heights) + max(0, len(row_heights) - 1) * 8)
        self._renderer = HTMLCardRenderer()

    def render(self) -> bytes:
        return self._renderer.render(
            self._build_html(), self.CARD_W, self.CARD_H, framed=False
        )

    def _build_html(self) -> str:
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
                for name in event_students[:4]:
                    icon_path = self.student_icons.get(name)
                    icon_uri = _local_image_uri(icon_path)
                    if icon_uri:
                        avatar_images.append(
                            f'<img class="row-avatar" src="{_escape(icon_uri)}" alt="">'
                        )
                if len(event_students) > 4:
                    avatar_images.append(
                        f'<span class="avatar-extra">+{len(event_students) - 4}</span>'
                    )
            avatar_markup = "".join(avatar_images)
            marker = (
                f'<div class="row-avatars">{avatar_markup}</div>'
                if avatar_markup
                else '<span class="row-marker"></span>'
            )
            rows.append(
                f'<article class="schedule-row"><div class="row-copy">{marker}'
                f'<div class="row-main"><b>{_escape(title)}</b>'
                f'<small>{_escape(subtitle)}</small></div></div>'
                f'<time>{_escape(date or self.date)}</time></article>'
            )
        if not rows and self.title != "活动日程":
            detail = "、".join(self.rate_up)
            rows.append(
                f'<article class="schedule-row"><div class="row-copy">'
                f'<span class="row-marker"></span><div class="row-main">'
                f"<b>{_escape(self.title)}</b><small>{_escape(detail)}</small></div></div>"
                f'<time>{_escape(self.date)}</time></article>'
            )
        content = "".join(rows) or '<p class="empty">暂时没有可展示的数据</p>'
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
.header-caption {{ display: flex; align-items: center; gap: 6px; margin-top: 3px; color: #8493a3; font-size: 11px; }}
.header-code {{ color: #438eae; font-size: 9px; font-weight: 700; letter-spacing: .1em; }}
.header-caption img {{ width: 42px; height: 13px; object-fit: contain; }}
.region {{ padding: 6px 12px; border: 1px solid rgba(58,151,192,.12); border-radius: 14px; background: #e1f2f9; color: #2781ad; font-size: 12px; font-weight: 700; }}
.rows {{ display: grid; gap: 8px; }}
.schedule-row {{ display: grid; grid-template-columns: minmax(0,1fr) 176px; align-items: center; gap: 16px; min-height: 54px; padding: 10px 14px; border: 1px solid rgba(76,132,161,.08); border-radius: 13px; background: rgba(255,255,255,.9); box-shadow: 0 2px 7px rgba(48,77,97,.045); }}
.row-copy {{ display: flex; min-width: 0; align-items: center; gap: 12px; }}
.row-marker {{ width: 8px; height: 8px; flex: 0 0 8px; border-radius: 50%; background: #45a5ce; box-shadow: 0 0 0 4px #e2f2f8; }}
.row-avatars {{ display: flex; flex: 0 0 auto; align-items: center; padding-left: 2px; }}
.row-avatar {{ width: 32px; height: 32px; margin-left: -3px; border: 2px solid #fff; border-radius: 50%; background: #e8f1f6; object-fit: cover; box-shadow: 0 1px 3px rgba(48,77,97,.16); }}
.avatar-extra {{ display: grid; width: 30px; height: 30px; margin-left: -3px; place-items: center; border: 2px solid #fff; border-radius: 50%; background: #e8f1f6; color: #60788d; font-size: 10px; }}
.row-main {{ display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 2px; }}
.row-main b {{ overflow-wrap: anywhere; color: #30455f; font-size: 14px; line-height: 1.35; }}
.row-main small {{ color: #8694a4; font-size: 11px; line-height: 1.3; }}
.schedule-row time {{ color: #59728c; font-size: 11px; line-height: 1.4; text-align: right; white-space: normal; }}
.empty {{ padding: 30px 24px; border-radius: 13px; background: rgba(255,255,255,.8); text-align: center; color: #8290a1; }}
</style></head><body><main class="card"><div class="backdrop"></div><div class="content">
<header class="header"><div class="header-copy"><span class="header-mark"><img src="{_escape(dashboard_icon)}" alt=""></span>
<div class="header-title"><h1>{_escape(self.title)}</h1>
<span class="header-caption"><span class="header-code">TACTICAL SCHEDULE</span><img src="{_escape(event_icon)}" alt="">近期活动安排</span></div></div><span class="region">{_escape(_card_region_label(self.region))}</span></header>
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
        self.CARD_H = max(
            220,
            140
            + sum(self._pool_row_height(pool) for pool in self.events)
            + max(0, len(self.events) - 1) * 10,
        )

    @staticmethod
    def _pool_student_names(pool: dict) -> list[str]:
        students = pool.get("students")
        if isinstance(students, list):
            return list(dict.fromkeys(str(name).strip() for name in students if str(name).strip()))
        title = re.sub(r"^卡池\s*[:：]\s*", "", str(pool.get("title") or ""))
        return list(dict.fromkeys(name.strip() for name in re.split(r"[、，,]", title) if name.strip()))

    @classmethod
    def _pool_row_height(cls, pool: dict) -> int:
        names = cls._pool_student_names(pool)
        columns = 4
        member_rows = [
            names[index:index + columns]
            for index in range(0, len(names), columns)
        ]
        member_height = sum(
            max(
                40,
                max((_estimated_lines(name, 8) * 14 + 10 for name in row), default=0),
            )
            for row in member_rows
        ) + max(0, len(member_rows) - 1) * 7
        return 86 + member_height

    def _build_html(self) -> str:
        rows = []
        for pool_index, pool in enumerate(self.events, start=1):
            students = self._pool_student_names(pool)
            start = _format_timestamp(pool.get("start_at"))
            end = _format_timestamp(pool.get("end_at"))
            date = f"{start} — {end}" if start and end else start or end
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
            subtitle = _escape(pool.get("pub_area") or _card_region_label(self.region))
            rows.append(
                f'<article class="pool-row"><header class="pool-row-header">'
                f'<div class="pool-heading"><span class="pool-index">{pool_index:02d}</span>'
                f'<div><strong>本期招募</strong><small>{subtitle}</small></div></div>'
                f'<time>{_escape(date or "时间待定")}</time></header>'
                f'<div class="pool-members">{members}</div></article>'
            )
        content = "".join(rows) or '<p class="empty">暂时没有可展示的卡池</p>'
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
.header-caption {{ display: flex; align-items: center; gap: 6px; margin-top: 3px; color: #8493a3; font-size: 11px; }}
.header-code {{ color: #438eae; font-size: 9px; font-weight: 700; letter-spacing: .1em; }}
.region {{ padding: 6px 12px; border: 1px solid rgba(58,151,192,.12); border-radius: 14px; background: #e1f2f9; color: #2781ad; font-size: 12px; font-weight: 700; }}
.pool-summary {{ margin: -4px 0 12px; color: #73869a; font-size: 11px; }}
.rows {{ display: grid; gap: 10px; }}
.pool-row {{ padding: 12px 14px 14px; border: 1px solid rgba(76,132,161,.09); border-radius: 14px; background: rgba(255,255,255,.92); box-shadow: 0 3px 9px rgba(48,77,97,.05); }}
.pool-row-header {{ display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 11px; }}
.pool-heading {{ display: flex; min-width: 0; align-items: center; gap: 10px; }}
.pool-index {{ display: grid; width: 30px; height: 30px; flex: 0 0 30px; place-items: center; border-radius: 10px; background: #e8f4fb; color: #3c88ad; font-size: 11px; font-weight: 700; }}
.pool-heading strong {{ display: block; color: #30455f; font-size: 14px; }}
.pool-heading small {{ display: inline-block; margin-top: 2px; padding: 2px 7px; border-radius: 8px; background: #f0f5f8; color: #71859a; font-size: 10px; }}
.pool-row-header time {{ flex: 0 0 auto; color: #536f8c; font-size: 11px; text-align: right; }}
.pool-members {{ display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 7px 8px; }}
.pool-member {{ display: flex; min-width: 0; min-height: 40px; align-items: center; gap: 7px; padding: 5px 7px; border: 1px solid #edf2f5; border-radius: 11px; background: #f8fbfd; }}
.pool-avatar,.pool-avatar-fallback {{ display: grid; width: 30px; height: 30px; flex: 0 0 30px; place-items: center; border-radius: 50%; background: #e5eff5; object-fit: cover; }}
.pool-avatar-fallback {{ color: #79a8be; font-size: 12px; }}
.pool-member-name {{ min-width: 0; color: #40546b; font-size: 11px; line-height: 1.25; overflow-wrap: anywhere; }}
.pool-empty {{ grid-column: 1/-1; margin: 4px 0; color: #8a9aaa; font-size: 11px; }}
.empty {{ padding: 30px 24px; border-radius: 13px; background: rgba(255,255,255,.8); text-align: center; color: #8290a1; }}
</style></head><body><main class="card"><div class="backdrop"></div><div class="content">
<header class="header"><div class="header-copy"><span class="header-mark"><img src="{_escape(dashboard_icon)}" alt=""></span>
<div class="header-title"><h1>卡池千里眼</h1>
<span class="header-caption"><span class="header-code">TACTICAL RECRUIT</span>近期招募与UP学生一览</span></div></div>
<span class="region">{_escape(_card_region_label(self.region))}</span></header>
<p class="pool-summary">共 {len(self.events)} 期招募 · {student_count} 个学生位</p>
<section class="rows">{content}</section></div></main></body></html>"""


class BirthdayCard:
    CARD_W = 720

    def __init__(
        self,
        students: list[tuple[str, str]] | None = None,
        student_icons: dict[str, str] | None = None,
    ):
        self.students = students or []
        self.student_icons = student_icons or {}
        self.visible_students = self.students[:24]
        student_rows = [
            self.visible_students[index:index + 3]
            for index in range(0, len(self.visible_students), 3)
        ]
        row_heights = [
            max(
                50,
                max(
                    (_estimated_lines(name, 8) * 16 + 16 for name, _ in row),
                    default=0,
                ),
            )
            for row in student_rows
        ]
        self.CARD_H = max(
            220,
            118 + sum(row_heights) + max(0, len(row_heights) - 1) * 8,
        )
        self._renderer = HTMLCardRenderer()

    def render(self) -> bytes:
        return self._renderer.render(
            self._build_html(), self.CARD_W, self.CARD_H, framed=False
        )

    def _build_html(self) -> str:
        rows = ""
        for name, birthday in self.visible_students:
            icon_path = self.student_icons.get(name)
            icon_uri = _local_image_uri(icon_path)
            avatar = (
                f'<img class="birthday-avatar" src="{_escape(icon_uri)}" alt="">'
                if icon_uri
                else '<span class="birthday-mark">✦</span>'
            )
            rows += (
                f'<article class="student"><div class="student-info">{avatar}'
                f'<b>{_escape(name)}</b></div><time>{_escape(birthday)}</time></article>'
            )
        content = rows or '<p class="empty">暂无生日数据</p>'
        card_bg_uri = _asset_uri("img/ui/cards/Card_Bg.png")
        dashboard_icon = _local_image_uri(
            str(DASHBOARD_ICON_DIR / "item_icon_favor_26.webp")
        )
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
{FONT_CSS}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; width: {self.CARD_W}px; min-height: {self.CARD_H}px; font-family: 'ResourceHanRounded','BA Compact','Blueaka','Microsoft YaHei','Noto Sans CJK SC',sans-serif; color: #334459; }}
body {{ background: linear-gradient(135deg,#fff4e9,#f7f0f8); }}
.card {{ position: relative; min-height: {self.CARD_H}px; overflow: hidden; padding: 22px 26px 26px; border: 1px solid rgba(190,132,98,.18); border-radius: 20px; background: linear-gradient(135deg,rgba(255,251,247,.94),rgba(250,246,253,.92)); box-shadow: 0 8px 24px rgba(128,87,64,.08); }}
.backdrop {{ position: absolute; inset: 0; background: url('{card_bg_uri}') center/cover no-repeat; transform: scale(1.35); opacity: .2; }}
.content {{ position: relative; z-index: 1; }}
header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }}
.header-copy {{ display: flex; align-items: center; gap: 12px; }}
.header-mark {{ display: grid; width: 48px; height: 44px; flex: 0 0 48px; place-items: center; border: 1px solid rgba(208,139,92,.16); border-radius: 14px; background: linear-gradient(145deg,#fff0e2,#fff7ed); }}
.header-mark img {{ width: 42px; height: 36px; object-fit: contain; }}
h1 {{ margin: 0; color: #a85e3d; font-size: 27px; letter-spacing: .3px; }}
.header-caption {{ margin-top: 3px; color: #9a8175; font-size: 11px; }}
.header-code {{ display: block; margin-bottom: 2px; color: #b87952; font-size: 9px; font-weight: 700; letter-spacing: .1em; }}
.count {{ padding: 6px 11px; border: 1px solid rgba(208,139,92,.14); border-radius: 14px; background: #fff0e3; color: #b36e48; font-size: 12px; font-weight: 700; }}
.students {{ display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 8px; }}
.student {{ display: flex; min-width: 0; min-height: 50px; align-items: center; justify-content: space-between; gap: 7px; padding: 7px 9px; border: 1px solid rgba(190,132,98,.1); border-radius: 12px; background: rgba(255,255,255,.88); box-shadow: 0 2px 7px rgba(128,87,64,.04); }}
.student-info {{ display: flex; min-width: 0; align-items: center; gap: 7px; }}
.student b {{ min-width: 0; color: #4a4650; font-size: 12px; line-height: 1.3; overflow-wrap: anywhere; }}
.birthday-avatar {{ width: 32px; height: 32px; flex: 0 0 32px; border-radius: 50%; background: #f5e9df; object-fit: cover; }}
.birthday-mark {{ display: grid; width: 32px; height: 32px; flex: 0 0 32px; place-items: center; border-radius: 50%; background: #fff0dc; color: #d99a4e; }}
.student time {{ flex: 0 0 auto; color: #a76e50; font-size: 11px; font-weight: 700; white-space: nowrap; }}
.empty {{ grid-column: 1/-1; padding: 24px; border-radius: 12px; background: rgba(255,255,255,.8); text-align: center; color: #8290a1; }}
</style></head><body><main class="card"><div class="backdrop"></div><div class="content">
<header><div class="header-copy"><span class="header-mark"><img src="{_escape(dashboard_icon)}" alt=""></span>
<div><h1>学生生日</h1><div class="header-code">STUDENT BIRTHDAY</div><div class="header-caption">记下每一位学生的特别日子</div></div></div>
<span class="count">共 {len(self.students)} 位</span></header><section class="students">{content}</section>
</div></main></body></html>"""


def render_schedule_card(**kwargs) -> bytes:
    return ScheduleCard(**kwargs).render()


def render_birthday_card(**kwargs) -> bytes:
    return BirthdayCard(**kwargs).render()
