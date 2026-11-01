"""GameKee Wiki 活动与卡池数据客户端。

仅用于：
  - 活动周历：https://www.gamekee.com/v1/wiki/indexV2
  - 卡池列表：https://www.gamekee.com/v1/cardPool/query-list

"""

from __future__ import annotations

import time
from typing import Any

import aiohttp


class GameKeeClient:
    """GameKee Wiki 活动与卡池客户端。"""

    DEFAULT_BASE_URL = "https://www.gamekee.com"

    # 服务器 ID 映射
    SERVER_IDS = {
        "jp": "15",
        "cn": "16",
        "global": "17",
    }
    SERVER_NAMES = {
        "jp": "日服",
        "cn": "国服",
        "global": "国际服",
    }
    REGION_ORDER = ("cn", "global", "jp")

    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int = 30,
    ):
        self.base_url = base_url or self.DEFAULT_BASE_URL
        self.timeout = timeout
        self._session: aiohttp.ClientSession | None = None
        self.last_error: str | None = None

    @property
    def WIKI_INDEX(self) -> str:
        return f"{self.base_url}/v1/wiki/indexV2"

    @property
    def CARD_POOL_URL(self) -> str:
        return f"{self.base_url}/v1/cardPool/query-list"

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

    # ------------------------------------------------------------------
    # 活动周历
    # ------------------------------------------------------------------

    async def get_activity_calendar(
        self, server_region: str = "cn"
    ) -> list[dict[str, Any]]:
        """获取指定服务器或全服合并后的活动周历。"""
        self.last_error = None
        if server_region == "all":
            events: list[dict[str, Any]] = []
            errors: list[str] = []
            for region in self.REGION_ORDER:
                region_events = await self.get_activity_calendar(region)
                if self.last_error:
                    errors.append(f"{self.SERVER_NAMES[region]}: {self.last_error}")
                else:
                    events.extend(region_events)
            self.last_error = "；".join(errors) if errors else None
            return events

        sid = self.SERVER_IDS.get(server_region, "16")
        server_cn = self.SERVER_NAMES.get(server_region, "国服")

        try:
            session = await self._get_session()
            session.headers["Game-Alias"] = "ba"

            async with session.get(self.WIKI_INDEX) as resp:
                if resp.status != 200:
                    self.last_error = f"indexV2 HTTP {resp.status}"
                    return []
                res = await resp.json(content_type=None)

            events = []
            modules = res.get("data", [])
            if isinstance(modules, list):
                for module in modules:
                    if not isinstance(module, dict):
                        continue
                    m = module.get("module", {})
                    if m.get("name") == "活动周历":
                        raw = module.get("list", [])
                        if isinstance(raw, dict):
                            items = raw.get(sid, [])
                        elif isinstance(raw, list):
                            items = [
                                i for i in raw if server_cn in i.get("pub_area", "")
                            ]
                        else:
                            items = []
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            title = item.get("title", "")
                            if "卡池" in title:
                                title = title.replace(server_cn, "")
                            elif "维护" in title:
                                st = item.get("begin_at", 0)
                                et = item.get("end_at", 0)
                                title = title.replace(server_cn, "") + f" {st}~{et}"
                            else:
                                import re

                                title = re.sub(r"【.*?】", "", title)
                            events.append(
                                {
                                    "title": title,
                                    "start_at": item.get("begin_at", 0),
                                    "end_at": item.get("end_at", 0),
                                    "pub_area": server_cn,
                                }
                            )
                        break
        except Exception as e:
            self.last_error = f"获取活动周历失败: {e}"
            return []

        return events

    # ------------------------------------------------------------------
    # 卡池列表
    # ------------------------------------------------------------------

    async def get_card_pools(self, server_region: str = "cn") -> list[dict[str, Any]]:
        """获取指定服务器或全服合并后的卡池列表（仅未结束的）。"""
        self.last_error = None
        if server_region == "all":
            pools: list[dict[str, Any]] = []
            errors: list[str] = []
            for region in self.REGION_ORDER:
                region_pools = await self.get_card_pools(region)
                if self.last_error:
                    errors.append(f"{self.SERVER_NAMES[region]}: {self.last_error}")
                else:
                    pools.extend(region_pools)
            self.last_error = "；".join(errors) if errors else None
            return pools

        sid = self.SERVER_IDS.get(server_region, "16")
        server_cn = self.SERVER_NAMES.get(server_region, "国服")

        url = f"{self.CARD_POOL_URL}?order_by=-1&card_tag_id=&keyword=&kind_id=6&status=0&serverId={sid}"
        try:
            session = await self._get_session()
            session.headers["Game-Alias"] = "ba"

            async with session.get(url) as resp:
                if resp.status != 200:
                    self.last_error = f"cardPool HTTP {resp.status}"
                    return []
                res = await resp.json(content_type=None)

            pools = []
            data = res.get("data", [])
            if isinstance(data, list):
                pool_dic: dict[str, list[dict]] = {}
                now = int(time.time())
                for pool in data:
                    if not isinstance(pool, dict):
                        continue
                    end_at = pool.get("end_at", 0)
                    if end_at > now:
                        pool_dic.setdefault(str(end_at), []).append(pool)
                    else:
                        break
                for pools_group in pool_dic.values():
                    if not pools_group:
                        continue
                    names = [p.get("name", "") for p in pools_group if p.get("name")]
                    pools.append(
                        {
                            "title": f"卡池：{'、'.join(names)}",
                            "students": names,
                            "start_at": pools_group[0].get("start_at", 0),
                            "end_at": pools_group[0].get("end_at", 0),
                            "pub_area": server_cn,
                        }
                    )
        except Exception as e:
            self.last_error = f"获取卡池列表失败: {e}"
            return []

        return pools
