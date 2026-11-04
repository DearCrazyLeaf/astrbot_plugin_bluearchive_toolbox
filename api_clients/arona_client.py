"""
Arona 攻略图客户端。

数据来源：
  - https://arona.diyigemt.com/api/v2/image?name=<学生名>&size=8&method=2
返回攻略图片 CDN URL。
"""

from __future__ import annotations

import re

import aiohttp


class AronaAmbiguousError(RuntimeError):
    """Raised when a guide query matches multiple approximate results."""


class AronaClient:
    """Arona 攻略图查询客户端。"""

    API_BASE = "https://arona.diyigemt.com/api/v2"
    CDN_PREFIX = "https://arona.cdn.diyigemt.com/image"
    GUIDE_NAME_ALIASES = {
        "aris_maid": "女仆爱丽丝",
        "hifumi_racing": "赛车日富美",
    }
    GUIDE_FORM_ALIASES = {
        "新年": "正月",
        "武装": "临战",
        "邦妮": "兔女郎",
        "运动服": "体操服",
        "自行车": "骑行",
        "单车": "骑行",
        "野营": "露营",
        "魔法少女": "魔法",
        "レーシング": "赛车",
    }

    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self._session: aiohttp.ClientSession | None = None
        self.last_error: str | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers=self.HEADERS,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def _candidate_names(items: list[dict]) -> str:
        names = list(
            dict.fromkeys(
                str(item["name"]).strip()
                for item in items
                if isinstance(item, dict) and item.get("name")
            )
        )
        if not names:
            return ""
        shown = "、".join(names[:5])
        remaining = len(names) - 5
        if remaining > 0:
            shown += f" 等 {len(names)} 项"
        return f"候选：{shown}。"

    @classmethod
    def guide_name_for_student(
        cls,
        student: dict,
        form_aliases: dict[str, str] | None = None,
    ) -> str:
        """Return the Arona catalog name for a SchaleDB student record."""
        path_name = str(student.get("PathName") or "").strip().lower()
        if path_name in cls.GUIDE_NAME_ALIASES:
            return cls.GUIDE_NAME_ALIASES[path_name]

        name = str(student.get("Name") or student.get("PathName") or "").strip()
        match = re.fullmatch(r"(.+?)[（(]([^（）()]+)[）)]", name)
        if not match:
            return name

        base_name, form_name = match.groups()
        aliases = cls.GUIDE_FORM_ALIASES if form_aliases is None else form_aliases
        form_name = aliases.get(form_name, form_name)
        return f"{form_name}{base_name}"

    async def get_guide_image(self, name: str) -> str | None:
        """查询学生攻略图片 URL。

        Returns:
            攻略图片完整 CDN URL，未找到返回 None。
        """
        self.last_error = None
        name = name.strip()
        if not name:
            return None

        try:
            session = await self._get_session()
            params = {"name": name, "size": 8, "method": 2}
            async with session.get(f"{self.API_BASE}/image", params=params) as resp:
                if resp.status != 200:
                    self.last_error = f"Arona API HTTP {resp.status}"
                    return None
                data = await resp.json(content_type=None)
        except Exception as e:
            self.last_error = f"Arona 请求异常: {e}"
            return None

        code = data.get("code")
        items = data.get("data")

        if code == 200 and isinstance(items, list) and len(items) >= 1:
            if len(items) > 1:
                raise AronaAmbiguousError(
                    f"「{name}」匹配到多个攻略结果。"
                    f"{self._candidate_names(items)}请提供更完整的学生名称。"
                )
            item = items[0]
            content = item.get("content")
            if content:
                return f"{self.CDN_PREFIX}/s{content}"
            return None

        if code == 101 and isinstance(items, list) and len(items) >= 1:
            raise AronaAmbiguousError(
                f"「{name}」未精确匹配攻略。"
                f"{self._candidate_names(items)}请核对学生名称后重试。"
            )

        self.last_error = None
        return None
