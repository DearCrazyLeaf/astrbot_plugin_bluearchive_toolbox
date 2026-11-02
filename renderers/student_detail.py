"""Student profile cards rendered from the native SchaleDB student schema."""

from __future__ import annotations

import base64
import html
import math
import mimetypes
import re
from pathlib import Path
from typing import Any

from .html_renderer import (
    FONT_CSS,
    HTMLCardRenderer,
    STAR_OFF,
    STAR_ON,
    _asset_uri,
)


_SCHOOL_NAMES = {
    "Abydos": "阿拜多斯",
    "Arius": "阿里乌斯",
    "Gehenna": "格黑娜",
    "Hyakkiyako": "百鬼夜行",
    "Millennium": "千年",
    "RedWinter": "赤冬",
    "Shanhaijing": "山海经",
    "SRT": "SRT",
    "Trinity": "三一",
    "Valkyrie": "瓦尔基里",
    "ETC": "其他",
    "Other": "其他",
    "Highlander": "海兰德",
    "Odyssey": "奥德赛",
    "Sakugawa": "栅川中学",
    "Tokiwadai": "常盘台中学",
    "WildHunt": "狂猎",
}
_ROLE_NAMES = {
    "DamageDealer": "输出",
    "Tanker": "坦克",
    "Healer": "治疗",
    "Supporter": "辅助",
    "Vehicle": "载具",
}
_ROLE_ICONS = {
    "DamageDealer": "img/ui/cards/Common_Icon_DamageDealer.png",
    "Tanker": "img/ui/cards/Common_Icon_Tanker.png",
    "Healer": "img/ui/cards/Common_Icon_Healer.png",
    "Supporter": "img/ui/cards/Common_Icon_Supporter.png",
}
_BULLET_NAMES = {
    "Explosion": "爆发",
    "Pierce": "贯穿",
    "Mystic": "神秘",
    "Sonic": "振动",
    "Normal": "普通",
}
_ARMOR_NAMES = {
    "LightArmor": "轻装甲",
    "HeavyArmor": "重装甲",
    "Unarmed": "特殊装甲",
    "ElasticArmor": "弹力装甲",
    "Normal": "复合装甲",
}
_BULLET_COLORS = {
    "Explosion": "#e6535b",
    "Pierce": "#e4bd32",
    "Mystic": "#4389df",
    "Sonic": "#a65bd1",
    "Normal": "#49a66a",
}
_ARMOR_COLORS = {
    "LightArmor": "#e6535b",
    "HeavyArmor": "#e4bd32",
    "Normal": "#49a66a",
    "Unarmed": "#4389df",
    "ElasticArmor": "#a65bd1",
}
_POSITION_NAMES = {"Front": "前排", "Middle": "中排", "Back": "后排"}
_POSITION_ICONS = {
    "Front": "img/ui/front.webp",
    "Middle": "img/ui/middle.webp",
    "Back": "img/ui/back.webp",
}
_SKILL_NAMES = {
    "ex": "EX技能",
    "normal": "普通技能",
    "gearnormal": "强化普通技能",
    "passive": "被动技能",
    "sub": "辅助技能",
    "weaponpassive": "专武被动",
    "extrapassive": "额外被动",
}
_ADAPTATION_NAMES = {0: "D", 1: "C", 2: "B", 3: "A", 4: "S", 5: "SS"}
_STAT_FIELDS = (
    ("生命值", "MaxHP1", "MaxHP100"),
    ("攻击力", "AttackPower1", "AttackPower100"),
    ("防御力", "DefensePower1", "DefensePower100"),
    ("治疗力", "HealPower1", "HealPower100"),
    ("命中", "AccuracyPoint", None),
    ("回避", "DodgePoint", None),
    ("暴击", "CriticalPoint", None),
    ("暴击伤害", "CriticalDamageRate", None),
    ("稳定性", "StabilityPoint", None),
    ("射程", "Range", None),
    ("弹药数", "AmmoCount", None),
    ("Cost回复", "RegenCost", None),
)
_STAT_ICON_FILES = {
    "MaxHP1": "MaxHP",
    "AttackPower1": "AttackPower",
    "DefensePower1": "DefensePower",
    "HealPower1": "HealPower",
    "AccuracyPoint": "AccuracyPoint",
    "DodgePoint": "DodgePoint",
    "CriticalPoint": "CriticalPoint",
    "CriticalDamageRate": "CriticalDamageRate",
    "StabilityPoint": "StabilityPoint",
    "Range": "Range",
    "AmmoCount": "AmmoCount",
}
_PARAMETER_TAGS = {
    "AttackPower": "攻击力",
    "ATK": "攻击力",
    "MaxHP": "生命值",
    "HealPower": "治疗力",
    "CriticalDamage": "暴击伤害",
    "CriticalChance": "暴击",
    "DefensePower": "防御力",
    "Accuracy": "命中",
    "AttackSpeed": "攻击速度",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _subtitle_emphasis(value: str) -> str:
    if re.search(r"[A-Za-z]", value):
        return f"<strong>{value}</strong>"
    return value


def _file_uri(path: str | Path | None) -> str:
    if not path:
        return ""
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _format_description(
    description: str,
    parameters: Any,
    skill_references: dict[str, str] | None = None,
) -> str:
    if not isinstance(parameters, list):
        parameters = []
    description = description.replace("&nbsp;", " ")
    token = re.compile(r"<\?(\d+)>|</?b>|<[bdsc]:([^>]+)>")
    result: list[str] = []
    cursor = 0
    for match in token.finditer(description):
        result.append(_text(description[cursor : match.start()]))
        value = match.group(1)
        if value is not None:
            index = int(value) - 1
            levels = parameters[index] if index < len(parameters) else []
            if not isinstance(levels, list):
                levels = []
            rendered = " / ".join(str(item) for item in levels if item is not None)
            result.append(f'<span class="param">{_text(rendered)}</span>')
        elif match.group(0) == "<b>":
            result.append("<strong>")
        elif match.group(0) == "</b>":
            result.append("</strong>")
        else:
            kind, tag = match.group(0)[1], match.group(2)
            if kind == "s":
                label = (skill_references or {}).get(tag)
                if not label:
                    suffix = tag.rsplit("_", 1)[-1].lower()
                    label = _SKILL_NAMES.get(
                        {
                            "ex": "ex",
                            "normal": "normal",
                            "public": "normal",
                            "gearnormal": "gearnormal",
                            "passive": "passive",
                            "weaponpassive": "weaponpassive",
                            "extrapassive": "extrapassive",
                        }.get(suffix, suffix),
                        "技能效果",
                    )
            else:
                label = _PARAMETER_TAGS.get(tag, tag)
            result.append(f'<span class="effect">{_text(label)}</span>')
        cursor = match.end()
    result.append(_text(description[cursor:]))
    return "".join(result).replace("\n", "<br>")


def _level_stat(value_at_1: Any, value_at_100: Any, level: int) -> int | None:
    if value_at_1 is None or value_at_100 is None:
        return None
    try:
        scale = round((level - 1) / 99, 4)
        value = float(value_at_1) + (
            float(value_at_100) - float(value_at_1)
        ) * scale
    except (TypeError, ValueError):
        return None
    return math.floor(value + 0.5)


class StudentDetailCard:
    """Render a SchaleDB student record as a vertically composed detail card."""

    CARD_W = 720

    def __init__(
        self,
        student_data: dict[str, Any],
    ):
        self.data = student_data
        self._renderer = HTMLCardRenderer()

    def _asset(self, key: str) -> str:
        assets = self.data.get("_render_assets", {})
        if isinstance(assets, dict):
            return _file_uri(assets.get(key))
        return ""

    def _stars(self) -> str:
        rarity = max(1, min(5, int(self.data.get("StarGrade") or 1)))
        return "".join(
            f'<img class="star" src="{_asset_uri(STAR_ON if index < rarity else STAR_OFF)}">'
            for index in range(5)
        )

    def _tag(
        self,
        label: str,
        tone: str = "",
        icon: str = "",
        color: str = "",
    ) -> str:
        if not label:
            return ""
        icon_markup = (
            f'<img class="attribute-icon" src="{_asset_uri(icon)}" alt="">'
            if icon
            else ""
        )
        style = f' style="--icon-color:{color}"' if color and icon else ""
        return f'<span class="tag {tone}"{style}>{icon_markup}{_text(label)}</span>'

    def _localized(self, category: str, value: Any, fallback: dict[str, str]) -> str:
        translations = self.data.get("_render_localization", {})
        if isinstance(translations, dict):
            category_translations = translations.get(category, {})
            if isinstance(category_translations, dict) and category_translations.get(
                value
            ):
                return str(category_translations[value])
        return fallback.get(value, str(value or ""))

    def _equipment(self) -> str:
        student = self.data
        rows: list[str] = []
        weapon_img = self._asset("weapon")
        weapon = student.get("Weapon")
        weapon = weapon if isinstance(weapon, dict) else {}
        if weapon_img or student.get("WeaponType") or weapon.get("Name"):
            image = (
                f'<img class="weapon-icon" src="{weapon_img}" '
                f'alt="专武">'
                if weapon_img
                else ""
            )
            rows.append(
                f'<div class="equipment weapon">'
                f'<strong class="weapon-title">专武 {_text(student.get("WeaponType", ""))}</strong>'
                f'{image}</div>'
            )

        for equipment in student.get("Equipment", []):
            if not isinstance(equipment, str):
                continue
            uri = self._asset(f"equipment:{equipment}")
            icon = (
                f'<img src="{uri}" alt="{_text(equipment)}">'
                if uri
                else f'<span class="equipment-fallback">{_text(equipment[:2])}</span>'
            )
            rows.append(
                f'<div class="equipment">{icon}<small>{_text(equipment)}</small></div>'
            )

        gear = student.get("Gear")
        if isinstance(gear, dict) and gear.get("Name"):
            uri = self._asset("gear")
            icon = (
                f'<img src="{uri}" alt="gear">'
                if uri
                else '<span class="equipment-fallback">爱用品</span>'
            )
            rows.append(
                f'<div class="equipment">{icon}<small>爱用品</small>'
                f'<small>{_text(gear.get("Name"))}</small></div>'
            )
        if not rows:
            return ""
        return (
            '<section class="panel equipment-panel"><h2>武器与装备</h2>'
            f'<div class="equipment-row">{"".join(rows)}</div></section>'
        )

    def _profile(self) -> str:
        student = self.data
        fields = (
            ("年龄", student.get("CharacterAge")),
            ("生日", student.get("Birthday") or student.get("BirthDay")),
            ("身高", student.get("CharHeightMetric")),
            ("年级", student.get("SchoolYear")),
            ("趣味", student.get("Hobby")),
            ("设计", student.get("Designer")),
            ("插画", student.get("Illustrator")),
            ("CV", student.get("CharacterVoice")),
        )
        entries = "".join(
            f'<div class="profile-entry"><span>{_text(label)}</span>'
            f'<strong>{_text(value)}</strong></div>'
            for label, value in fields
            if value
        )
        introduction = str(student.get("ProfileIntroduction") or "").strip()
        introduction = introduction.replace("\r\n", "\n").replace("\r", "\n")
        quote = str(student.get("CharacterSSRNew") or "").strip()
        biography = ""
        if introduction:
            biography += (
                f'<p class="profile-introduction">'
                f'{_text(introduction).replace(chr(10), "<br>")}</p>'
            )
        if quote:
            biography += f'<p class="profile-quote">“{_text(quote)}”</p>'
        if not entries and not biography:
            return ""
        school_uri = self._asset("school")
        school_badge = (
            f'<img class="school-badge" src="{school_uri}" alt="">'
            if school_uri
            else ""
        )
        return (
            '<section class="profile-panel"><div class="notebook-holes">'
            f'{"".join("<span></span>" for _ in range(9))}</div>'
            f'<div class="profile-heading"><h2>学生简介</h2>{school_badge}</div>'
            f'<div class="profile-grid">{entries}</div>'
            f'{biography}</section>'
        )

    def _memory_lobby(self) -> str:
        memory_lobby = self.data.get("MemoryLobby")
        if not isinstance(memory_lobby, list):
            return ""
        unlock_level = next(
            (
                level
                for level in memory_lobby
                if isinstance(level, int) and level > 0
            ),
            None,
        )
        if unlock_level is None:
            return ""
        image = self._asset("lobby") or self._asset("student_icon")
        image_markup = (
            f'<img class="lobby-icon" src="{image}" alt="">'
            if image
            else '<span class="lobby-icon lobby-placeholder"></span>'
        )
        name = self.data.get("Name") or self.data.get("PathName") or "学生"
        bgm = self.data.get("MemoryLobbyBGM")
        music = f'<span class="lobby-music">♫ Theme {_text(bgm)}</span>' if bgm else ""
        return (
            '<section class="memory-panel"><div class="lobby-art">'
            f'{image_markup}<span class="lobby-unlock">{unlock_level}</span></div>'
            f'<div class="lobby-copy"><h2>纪念大厅</h2>'
            f'<p>{_text(name)} 羁绊等级达到{unlock_level}后解锁</p>'
            f'{music}</div></section>'
        )

    def _gifts_and_furniture(self) -> str:
        favorites = self.data.get("_render_favorites", [])
        furnitures = self.data.get("_render_furnitures", [])
        columns: list[str] = []
        for title, records, prefix in (
            ("好感赠礼", favorites, "item"),
            ("互动家具", furnitures, "furniture"),
        ):
            icons: list[str] = []
            for record in records[:8]:
                icon = record.get("Icon")
                if not icon:
                    continue
                uri = self._asset(f"{prefix}:{icon}")
                if uri:
                    icons.append(
                        f'<img src="{uri}" alt="{_text(record.get("Name", title))}" '
                        f'title="{_text(record.get("Name", ""))}">'
                    )
            if icons:
                columns.append(
                    f'<div class="gift-column"><h3>{title}</h3>'
                    f'<div class="icon-grid">{"".join(icons)}</div></div>'
                )
        if not columns:
            return ""
        return f'<section class="panel gift-panel">{"".join(columns)}</section>'

    def _adaptation(self) -> str:
        entries = (
            ("街道", "Street", self.data.get("StreetBattleAdaptation")),
            ("室外", "Outdoor", self.data.get("OutdoorBattleAdaptation")),
            ("室内", "Indoor", self.data.get("IndoorBattleAdaptation")),
        )
        cells: list[str] = []
        for label, key, value in entries:
            if value is None:
                continue
            try:
                level = int(value)
            except (TypeError, ValueError):
                continue
            terrain = self._asset(f"terrain:{key}")
            adapt = self._asset(f"adapt:{level}")
            if not terrain:
                terrain_file = f"img/schaledb/Terrain_{key}.png"
                terrain = _asset_uri(terrain_file)
            if not adapt:
                adapt_file = {
                    0: "Adaptresult0.png",
                    1: "Adaptresult0.png",
                    2: "Adaptresult2.png",
                    3: "Adaptresult2.png",
                    4: "Adaptresult4.png",
                    5: "Adaptresult4.png",
                }.get(level)
                if adapt_file:
                    adapt = _asset_uri(f"img/schaledb/{adapt_file}")
            terrain_markup = (
                f'<div class="terrain-tile" title="{label}">'
                f'<img class="terrain-icon" src="{terrain}" alt="{label}"></div>'
                if terrain
                else f'<div class="terrain-tile terrain-fallback" title="{label}">'
                f'{label}</div>'
            )
            adapt_markup = (
                f'<img class="adapt-icon" src="{adapt}" alt="{_text(_ADAPTATION_NAMES.get(level, level))}">'
                if adapt
                else f"<b>{_text(_ADAPTATION_NAMES.get(level, level))}</b>"
            )
            cells.append(
                f'<div class="adapt-cell" title="{label}">{terrain_markup}'
                f'{adapt_markup}</div>'
            )
        if not cells:
            return ""
        return (
            '<section class="adapt-panel"><h2>适应性</h2>'
            f'<div class="adapt-row">{"".join(cells)}</div></section>'
        )

    def _stats(self) -> str:
        rows = []
        for label, low_key, high_key in _STAT_FIELDS:
            low = self.data.get(low_key)
            high = (
                _level_stat(low, self.data.get(high_key), 90) if high_key else None
            )
            if low is None and high is None:
                continue
            if low_key.endswith("Rate"):
                low = f"{float(low) / 100:g}%" if low is not None else None
                high = f"{float(high) / 100:g}%" if high is not None else None
            if high_key:
                value = f"{low if low is not None else '—'} / {high if high is not None else '—'}"
            else:
                value = str(low if low is not None else high)
            icon_name = _STAT_ICON_FILES.get(low_key)
            icon_uri = (
                _file_uri(
                    Path(__file__).resolve().parent.parent
                    / "assets"
                    / "img"
                    / "schaledb"
                    / "stats"
                    / f"{icon_name}.svg"
                )
                if icon_name
                else ""
            )
            label_markup = (
                f'<img class="stat-icon" src="{icon_uri}" alt="">'
                if icon_uri
                else ""
            )
            rows.append(
                f'<div class="stat"><span class="stat-label">{label_markup}'
                f'{_text(label)}</span><b>{_text(value)}</b></div>'
            )
        if not rows:
            return ""
        return (
            '<section class="panel stats-panel"><h2>基础属性 <small>Lv.1 / Lv.90</small></h2>'
            f'<div class="stats-grid">{"".join(rows)}</div></section>'
        )

    def _materials(self, material_key: str, amount_key: str, max_steps: int) -> str:
        material_rows = self.data.get(material_key, [])
        amount_rows = self.data.get(amount_key, [])
        item_map = self.data.get("_render_items_by_id", {})
        if not isinstance(material_rows, list) or not material_rows:
            return ""
        sections: list[str] = []
        for index, ids in enumerate(material_rows[:max_steps]):
            if not isinstance(ids, list):
                continue
            amounts = (
                amount_rows[index]
                if isinstance(amount_rows, list) and index < len(amount_rows)
                else []
            )
            icons: list[str] = []
            for item_index, item_id in enumerate(ids):
                metadata = (
                    item_map.get(item_id, {}) if isinstance(item_map, dict) else {}
                )
                icon_name = metadata.get("Icon", "")
                uri = self._asset(f"item:{icon_name}") if icon_name else ""
                amount = (
                    amounts[item_index]
                    if isinstance(amounts, list) and item_index < len(amounts)
                    else ""
                )
                if uri:
                    icons.append(
                        f'<span class="material"><img src="{uri}" alt="">'
                        f"<small>×{_text(amount)}</small></span>"
                    )
                else:
                    label = metadata.get("Name") or item_id
                    icons.append(
                        f'<span class="material-name">{_text(label)} ×{_text(amount)}</span>'
                    )
            if icons:
                sections.append(
                    f'<div class="material-step"><b>{index + 1}→{index + 2}</b>'
                    f'<div class="material-items">{"".join(icons)}</div></div>'
                )
        if not sections:
            return ""
        return (
            '<div class="materials"><h3>技能升级材料</h3>'
            f'<div class="material-grid">{"".join(sections)}</div></div>'
        )

    def _skills(self) -> str:
        skills = self.data.get("_render_skills")
        if not isinstance(skills, list):
            raw_skills = self.data.get("Skills", [])
            if isinstance(raw_skills, list):
                skills = [skill for skill in raw_skills if isinstance(skill, dict)]
            elif isinstance(raw_skills, dict):
                type_names = {
                    "normal": "autoattack",
                    "ex": "ex",
                    "public": "normal",
                    "gearpublic": "gearnormal",
                    "passive": "passive",
                    "weaponpassive": "weaponpassive",
                    "extrapassive": "extrapassive",
                }
                skills = [
                    {
                        **skill,
                        "SkillType": type_names.get(str(key).lower(), str(key).lower()),
                    }
                    for key, skill in raw_skills.items()
                    if isinstance(skill, dict)
                ]
            else:
                return ""
        skill_references: dict[str, str] = {}
        raw_skills = self.data.get("Skills")
        dev_name = str(self.data.get("DevName") or "")
        if isinstance(raw_skills, dict):
            for key, skill in raw_skills.items():
                if isinstance(skill, dict) and skill.get("Name"):
                    skill_references[f"{dev_name}_{key}"] = str(skill["Name"])
        elif isinstance(raw_skills, list):
            reference_types = {
                "autoattack": "Normal",
                "ex": "Ex",
                "normal": "Public",
                "gearnormal": "GearPublic",
                "passive": "Passive",
                "weaponpassive": "WeaponPassive",
                "extrapassive": "ExtraPassive",
            }
            for skill in raw_skills:
                if not isinstance(skill, dict) or not skill.get("Name"):
                    continue
                skill_key = reference_types.get(
                    str(skill.get("SkillType") or "").lower()
                )
                if skill_key:
                    skill_references[f"{dev_name}_{skill_key}"] = str(skill["Name"])
        rendered: list[str] = []
        ordered_types = (
            "ex",
            "normal",
            "gearnormal",
            "passive",
            "weaponpassive",
            "sub",
            "extrapassive",
        )
        skills = sorted(
            (
                skill
                for skill in skills
                if isinstance(skill, dict)
                and skill.get("SkillType", "").lower() in ordered_types
            ),
            key=lambda skill: ordered_types.index(skill.get("SkillType", "").lower()),
        )
        for skill in skills:
            skill_type = skill.get("SkillType", "").lower()
            name = skill.get("Name") or _SKILL_NAMES.get(skill_type, "技能")
            icon = self._asset(f"skill:{skill.get('Icon', '')}")
            icon_markup = (
                f'<img class="skill-icon" src="{icon}" alt="">'
                if icon
                else '<span class="skill-icon"></span>'
            )
            skill_color = _BULLET_COLORS.get(
                str(self.data.get("BulletType") or ""), "#8a9aab"
            )
            cost = skill.get("Cost")
            cost_label = f"费用 {cost[0]}" if isinstance(cost, list) and cost else ""
            desc = _format_description(
                str(skill.get("Desc") or ""),
                skill.get("Parameters"),
                skill_references,
            )
            rendered.append(
                f'<article class="skill" style="--skill-color:{skill_color}">'
                f'<div class="skill-heading">{icon_markup}'
                f'<div class="skill-title"><b>{_text(name)}</b>'
                f"<span>{_text(_SKILL_NAMES.get(skill_type, skill_type))}</span></div>"
                f"<small>{_text(cost_label)}</small></div>"
                f'<div class="skill-description">{desc}</div></article>'
            )
            if skill_type == "ex":
                rendered.append(
                    self._materials("SkillExMaterial", "SkillExMaterialAmount", 4)
                )
        normal_skills = any(
            skill.get("SkillType", "").lower()
            in {
                "normal",
                "gearnormal",
                "passive",
                "sub",
                "weaponpassive",
                "extrapassive",
            }
            for skill in skills
        )
        if normal_skills:
            materials = self._materials("SkillMaterial", "SkillMaterialAmount", 8)
            if materials:
                rendered.append(materials)
        if not rendered:
            return ""
        return (
            f'<section class="skills-panel"><h2>技能</h2>{"".join(rendered)}</section>'
        )

    def _build_html(self) -> str:
        student = self.data
        name = student.get("Name") or student.get("PathName") or "未知学生"
        school = student.get("School", "")
        school_uri = self._asset("school")
        if not school_uri and school:
            school_uri = _asset_uri("img/schaledb/Terrain_Street.png")
        portrait_uri = self._asset("portrait") or self._asset("student_icon")
        portrait_markup = (
            f'<img class="portrait" src="{portrait_uri}" alt="{_text(name)}">'
            if portrait_uri
            else '<div class="portrait-placeholder">暂无立绘</div>'
        )

        tags = "".join(
            (
                self._tag(
                    _ROLE_NAMES.get(
                        student.get("TacticRole"), student.get("TacticRole", "")
                    ),
                    "role-tag",
                    _ROLE_ICONS.get(
                        student.get("TacticRole"),
                        "img/schaledb/Role_Vehicle.png"
                        if student.get("TacticRole") == "Vehicle"
                        else "",
                    ),
                    "#4a9bc4",
                ),
                self._tag(
                    _BULLET_NAMES.get(
                        student.get("BulletType"), student.get("BulletType", "")
                    ),
                    "damage-tag",
                    "img/schaledb/Type_Attack.png",
                    _BULLET_COLORS.get(student.get("BulletType"), "#8a9aab"),
                ),
                self._tag(
                    _ARMOR_NAMES.get(
                        student.get("ArmorType"), student.get("ArmorType", "")
                    ),
                    "armor-tag",
                    "img/schaledb/Type_Defense.png",
                    _ARMOR_COLORS.get(student.get("ArmorType"), "#8a9aab"),
                ),
                self._tag(
                    _POSITION_NAMES.get(
                        student.get("Position"), student.get("Position", "")
                    ),
                    "position-tag",
                    _POSITION_ICONS.get(student.get("Position"), ""),
                ),
                self._tag(
                    "突击"
                    if student.get("SquadType") == "Main"
                    else "支援"
                    if student.get("SquadType") == "Support"
                    else "",
                    "squad-tag",
                    (
                        "img/ui/striker.webp"
                        if student.get("SquadType") == "Main"
                        else "img/ui/special.webp"
                        if student.get("SquadType") == "Support"
                        else ""
                    ),
                ),
            )
        )
        club_value = student.get("Club", "")
        club = _text(self._localized("Club", club_value, {}))
        squad = (
            "突击"
            if student.get("SquadType") == "Main"
            else "支援"
            if student.get("SquadType") == "Support"
            else ""
        )
        header_school = (
            f'<img class="school-mark" src="{school_uri}" alt="">' if school_uri else ""
        )
        school_label = _text(self._localized("School", school, _SCHOOL_NAMES))
        school_inline = (
            f'<span class="school-inline">'
            f'<img src="{school_uri}" alt="">'
            f'{_subtitle_emphasis(school_label)}</span>'
            if school_uri
            else f"<span>{_subtitle_emphasis(school_label)}</span>"
        )
        birthday = student.get("Birthday") or student.get("BirthDay") or ""

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
{FONT_CSS}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; width: {self.CARD_W}px; min-height: 100%; font-family: 'ResourceHanRounded', 'Blueaka', 'Microsoft YaHei', sans-serif; color: #2a323e; }}
body {{ background: #eaf5fa; }}
.card {{ position: relative; width: {self.CARD_W}px; min-height: 400px; padding: 18px 22px 24px; overflow: hidden; }}
.card-bg {{ position: absolute; inset: 0; background: url('{_asset_uri("img/ui/cards/Card_Bg.png")}') center/cover; opacity: .20; }}
.content {{ position: relative; z-index: 1; }}
.header {{ display: flex; align-items: flex-start; gap: 12px; }}
.title-area {{ flex: 1; min-width: 0; }}
h1 {{ margin: 0; font-size: 34px; line-height: 1.2; color: #28364b; overflow-wrap: anywhere; }}
.subtitle {{ display: flex; flex-wrap: nowrap; gap: 10px; align-items: center; margin-top: 5px; overflow: hidden; color: #738195; font-size: 14px; font-style: italic; white-space: nowrap; }}
.subtitle > span {{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.subtitle strong {{ font-weight: 800; }}
.school-inline {{ display: inline-flex; min-width: 0; align-items: center; gap: 5px; }}
.school-inline img {{ width: 22px; height: 22px; padding: 3px; border-radius: 5px; background: #303037; object-fit: contain; }}
.stars {{ display: flex; gap: 3px; margin-top: 9px; }}
.star {{ width: 23px; height: 23px; }}
.school-mark {{ width: 72px; height: 72px; object-fit: contain; opacity: .9; }}
.tags {{ display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin: 12px 0; }}
.tag {{ display: inline-flex; align-items: center; min-height: 30px; padding: 4px 11px 4px 8px; clip-path: polygon(7px 0, 100% 0, calc(100% - 7px) 100%, 0 100%); background: #f3f7fa; color: #344356; font-size: 13px; font-weight: 700; filter: drop-shadow(0 2px 3px rgba(48, 77, 97, .18)); }}
.damage-tag, .armor-tag {{ color: #263547; }}
.attribute-icon {{ width: 18px; height: 18px; margin-right: 6px; padding: 3px; border-radius: 50%; background: var(--icon-color, #4a9bc4); object-fit: contain; }}
.damage-tag .attribute-icon, .armor-tag .attribute-icon {{ background: var(--icon-color, #8a9aab); }}
.position-tag .attribute-icon, .squad-tag .attribute-icon {{ width: 48px; height: 14px; margin-right: 5px; padding: 0; border-radius: 0; background: transparent; }}
.panel {{ margin: 12px 0; padding: 13px 15px; border-radius: 14px; background: rgba(255,255,255,.77); box-shadow: 0 2px 6px rgba(58,94,117,.08); }}
h2 {{ margin: 0 0 10px; color: #3c9acb; font-size: 19px; }}
h2 small {{ margin-left: 8px; font-size: 12px; font-weight: 400; color: #8190a1; }}
.equipment-row {{ display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }}
.equipment {{ display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; min-width: 76px; color: #687789; font-size: 11px; text-align: center; }}
.equipment img {{ width: 60px; height: 60px; object-fit: contain; }}
.equipment.weapon {{ flex: 0 0 190px; min-width: 190px; max-width: 210px; padding: 7px 9px; border-radius: 8px; background: #fff; color: #334459; font-size: 14px; text-align: left; }}
.weapon-title {{ align-self: flex-start; font-size: 13px; font-style: italic; font-weight: 800; }}
.weapon-icon {{ width: 170px !important; height: 76px !important; }}
.equipment-fallback {{ display: grid; width: 52px; height: 52px; place-items: center; border-radius: 10px; background: #e1e9ef; color: #607387; font-weight: 700; }}
.hero-section {{ display: grid; grid-template-columns: minmax(0, 1.12fr) minmax(0, .98fr); align-items: center; gap: 12px; margin: 10px 0 13px; }}
.profile-column {{ display: flex; flex-direction: column; gap: 10px; min-width: 0; }}
.profile-panel, .memory-panel {{ padding: 13px 14px; border-radius: 9px; background: #fff; color: #2a323e; box-shadow: 0 2px 6px rgba(58,94,117,.08); }}
.profile-panel {{ position: relative; padding: 20px 12px 12px; border: 1px solid #ccd7df; border-radius: 5px; background: #f8fcff; }}
.notebook-holes {{ position: absolute; top: 6px; left: 10px; right: 10px; display: flex; justify-content: space-between; padding-right: 27px; }}
.notebook-holes span {{ width: 10px; height: 10px; flex: 0 0 10px; border: 1px solid #c3cdd3; border-radius: 50%; background: linear-gradient(#e9eef1, #fff 60%); box-shadow: inset 0 1px 2px rgba(54,75,89,.16); }}
.profile-heading {{ display: flex; align-items: center; justify-content: space-between; min-height: 28px; margin-bottom: 5px; }}
.profile-heading h2 {{ margin: 0; }}
.school-badge {{ width: 30px; height: 30px; padding: 3px; border-radius: 5px; background: #303037; object-fit: contain; }}
.profile-panel h2, .memory-panel h2 {{ margin: 0 0 7px; color: #344c65; font-size: 17px; }}
.profile-grid {{ display: grid; grid-template-columns: 1fr; gap: 0; }}
.profile-entry {{ display: grid; grid-template-columns: 60px minmax(0, 1fr); align-items: start; gap: 6px; min-width: 0; padding: 5px 2px; border-bottom: 1px solid #cbd4da; font-size: 12px; line-height: 1.45; }}
.profile-entry span {{ color: #344c65; font-weight: 700; }}
.profile-entry strong {{ font-weight: 500; overflow-wrap: anywhere; }}
.profile-introduction {{ margin: 7px 0 0; font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }}
.profile-quote {{ margin: 9px 0 0; font-size: 13px; font-weight: 700; font-style: italic; }}
.hero-bottom {{ display: flex; align-items: flex-end; gap: 7px; }}
.adapt-panel {{ flex: 0 0 76px; min-width: 0; padding: 8px 6px; border-radius: 9px; background: #fff; box-shadow: 0 2px 6px rgba(58,94,117,.08); }}
.adapt-panel h2 {{ margin: 0 0 5px; font-size: 12px; text-align: center; }}
.memory-panel {{ display: flex; flex: 1 1 0; align-items: center; gap: 8px; min-width: 0; min-height: 88px; padding: 9px; }}
.lobby-art {{ position: relative; flex: 0 0 44px; width: 44px; height: 44px; }}
.lobby-icon {{ width: 44px; height: 44px; object-fit: cover; border-radius: 50%; }}
.lobby-placeholder {{ display: block; background: #e1e9ef; }}
.lobby-unlock {{ position: absolute; right: -3px; bottom: -2px; display: grid; width: 25px; height: 25px; place-items: center; border: 2px solid #fff; border-radius: 50%; background: #ee5b91; color: #fff; font-size: 13px; font-weight: 800; }}
.lobby-copy {{ min-width: 0; }}
.memory-panel h2 {{ margin: 0 0 3px; font-size: 16px; }}
.lobby-copy p {{ margin: 0; font-size: 11px; line-height: 1.4; overflow-wrap: anywhere; }}
.lobby-music {{ display: block; margin-top: 6px; color: #738195; font-size: 10px; font-style: italic; }}
.gift-panel {{ display: flex; gap: 20px; }}
.gift-column {{ flex: 1; min-width: 0; }}
.gift-column h3, .materials h3 {{ margin: 0 0 8px; color: #738195; font-size: 13px; }}
.icon-grid {{ display: flex; flex-wrap: wrap; gap: 7px; }}
.icon-grid img {{ width: 48px; height: 48px; object-fit: contain; border-radius: 7px; background: rgba(214,227,236,.45); }}
.portrait-wrap {{ display: flex; justify-content: center; margin: 0; min-height: 280px; }}
.portrait {{ display: block; width: 100%; max-width: 100%; max-height: 520px; object-fit: contain; }}
.portrait-placeholder {{ display: grid; width: 200px; height: 240px; place-items: center; background: #dce4e9; color: #768696; border-radius: 12px; }}
.adapt-row {{ display: flex; flex-direction: column; gap: 3px; }}
.adapt-cell {{ display: flex; align-items: center; gap: 3px; color: #657587; font-size: 10px; }}
.terrain-tile {{ display: grid; flex: 0 0 25px; width: 25px; height: 22px; place-items: center; border-radius: 4px; background: #fff; }}
.terrain-icon {{ width: 20px; height: 18px; object-fit: contain; filter: brightness(0) saturate(100%) invert(20%) sepia(10%) saturate(1046%) hue-rotate(176deg) brightness(94%) contrast(88%); }}
.terrain-fallback {{ color: #657587; font-size: 8px; }}
.adapt-icon {{ width: 22px; height: 22px; object-fit: contain; }}
.adapt-cell b {{ color: #3b92c2; font-size: 18px; }}
.stats-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 5px 12px; }}
.stat {{ display: flex; justify-content: space-between; gap: 8px; padding: 6px 9px; border-radius: 7px; background: rgba(224,237,244,.58); font-size: 13px; }}
.stat-label {{ display: inline-flex; align-items: center; gap: 6px; color: #718094; }}
.stat-icon {{ width: 18px; height: 18px; flex: 0 0 18px; object-fit: contain; opacity: .9; }}
.stat b {{ color: #344356; font-weight: 700; text-align: right; }}
.skills-panel {{ margin-top: 15px; }}
.skills-panel > h2 {{ margin-left: 2px; }}
.skill {{ margin: 7px 0; padding: 12px 14px; border-radius: 13px; background: rgba(255,255,255,.82); }}
.skill-heading {{ display: flex; align-items: center; gap: 11px; }}
.skill-icon {{ width: 48px; height: 48px; flex: 0 0 48px; object-fit: contain; border-radius: 50%; background: var(--skill-color, #8a9aab); padding: 5px; box-shadow: inset 0 0 0 2px rgba(255,255,255,.55); }}
.skill-title {{ display: flex; flex: 1; flex-direction: column; gap: 2px; }}
.skill-title b {{ color: #29384b; font-size: 17px; }}
.skill-title span, .skill-heading > small {{ color: #8794a2; font-size: 12px; }}
.skill-heading > small {{ color: #3296c8; }}
.skill-description {{ margin: 8px 0 0 59px; color: #536276; font-size: 13px; line-height: 1.55; overflow-wrap: anywhere; }}
.param {{ color: #188dc7; font-weight: 700; }}
.effect {{ color: #e18b28; font-weight: 700; }}
.materials {{ margin: 5px 0 10px; padding: 10px 12px; border-radius: 10px; background: rgba(213,226,235,.68); }}
.material-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 5px 8px; }}
.material-step {{ display: flex; align-items: center; gap: 8px; min-height: 42px; padding: 4px 8px; border-radius: 7px; background: rgba(255,255,255,.56); }}
.material-step > b {{ min-width: 38px; color: #728397; font-size: 11px; }}
.material-items {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.material {{ position: relative; display: inline-flex; width: 34px; height: 34px; }}
.material img {{ width: 34px; height: 34px; object-fit: contain; }}
.material small {{ position: absolute; right: -5px; bottom: -3px; padding: 1px 3px; border-radius: 5px; background: #fff; color: #46576a; font-size: 9px; }}
.material-name {{ color: #627286; font-size: 10px; }}
</style></head>
<body><main class="card"><div class="card-bg"></div><div class="content">
<header class="header"><div class="title-area"><h1>{_text(name)}</h1>
<div class="subtitle">{school_inline}{f"<span>· {_subtitle_emphasis(club)}</span>" if club else ""}{f"<span>· {_subtitle_emphasis(_text(squad))}</span>" if squad else ""}{f"<span>· {_subtitle_emphasis(_text(birthday))}</span>" if birthday else ""}</div>
<div class="stars">{self._stars()}</div></div>{header_school}</header>
<div class="tags">{tags}</div>
{self._equipment()}
{self._gifts_and_furniture()}
<section class="hero-section"><div class="portrait-wrap">{portrait_markup}</div>
<div class="profile-column">{self._profile()}
<div class="hero-bottom">{self._adaptation()}{self._memory_lobby()}</div></div></section>
{self._stats()}
{self._skills()}
</div></main></body></html>"""

    def render(self) -> bytes:
        html_content = self._build_html()
        return self._renderer._render_html(html_content, self.CARD_W, None)

    def render_bytes(self) -> bytes:
        return self.render()


def render_student_detail(
    student_data: dict[str, Any],
) -> bytes:
    return StudentDetailCard(student_data).render_bytes()
