"""
生成卡片图片缓存管理器 — 可配置容量、过期时间和 LRU 清理策略。

缓存结构：
  cache/
    images/          已生成并发送的 PNG 卡片
    meta/            卡片缓存元数据（访问时间、大小、过期时间）
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class ImageCacheManager:
    """LRU 图片缓存管理器。"""

    def __init__(
        self,
        cache_dir: str | Path = "cache/plugin_images",
        max_items: int = 200,
        max_size_mb: int = 500,
        ttl_seconds: int = 86400 * 7,  # 7 天
    ):
        self.cache_dir = Path(cache_dir)
        self.images_dir = self.cache_dir / "images"
        self.meta_dir = self.cache_dir / "meta"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)

        self.max_items = max_items
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.ttl_seconds = ttl_seconds

    def _key_path(self, key: str) -> tuple[Path, Path]:
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.images_dir / f"{h}.png", self.meta_dir / f"{h}.json"

    def get(self, key: str) -> bytes | None:
        """获取缓存的图片数据，并更新访问时间。"""
        img_path, meta_path = self._key_path(key)
        if not img_path.exists() or not meta_path.exists():
            return None

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

            # 检查过期
            if time.time() - meta.get("_ts", 0) > self.ttl_seconds:
                self._remove(key)
                return None

            # 更新访问时间（LRU）
            meta["_at"] = time.time()
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)

            with open(img_path, "rb") as f:
                return f.read()
        except Exception:
            self._remove(key)
            return None

    def put(self, key: str, data: bytes) -> Path:
        """保存 PNG 图片并返回其路径，同时清理过期或超限缓存。"""
        img_path, meta_path = self._key_path(key)

        now = time.time()
        image_temp = img_path.with_suffix(".png.tmp")
        meta_temp = meta_path.with_suffix(".json.tmp")
        try:
            image_temp.write_bytes(data)
            image_temp.replace(img_path)
            meta_temp.write_text(
                json.dumps(
                    {"_ts": now, "_at": now, "_size": len(data)},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            meta_temp.replace(meta_path)
        finally:
            image_temp.unlink(missing_ok=True)
            meta_temp.unlink(missing_ok=True)

        self._enforce_limits(protected_key=meta_path.stem)
        return img_path

    def _remove(self, key: str) -> None:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        self._remove_digest(digest)

    def _remove_digest(self, digest: str) -> None:
        img_path = self.images_dir / f"{digest}.png"
        meta_path = self.meta_dir / f"{digest}.json"
        for p in (img_path, meta_path):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass

    def _enforce_limits(self, protected_key: str | None = None) -> None:
        """按过期时间、LRU、数量和大小限制清理缓存。"""
        metas: list[tuple[str, dict[str, Any]]] = []
        for meta_path in self.meta_dir.glob("*.json"):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                key = meta_path.stem
                metas.append((key, meta))
            except Exception:
                continue

        metas.sort(key=lambda x: x[1].get("_at", 0))
        total_size = sum(m.get("_size", 0) for _, m in metas)
        remaining_items = len(metas)
        now = time.time()

        for key, meta in metas:
            expired = now - meta.get("_ts", 0) > self.ttl_seconds
            over_limit = (
                remaining_items > self.max_items
                or total_size > self.max_size_bytes
            )
            if (expired or over_limit) and key != protected_key:
                self._remove_digest(key)
                remaining_items -= 1
                total_size -= meta.get("_size", 0)

    def cleanup_expired(self) -> int:
        """清理过期缓存，返回清理数量。"""
        count = 0
        now = time.time()
        for meta_path in list(self.meta_dir.glob("*.json")):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                if now - meta.get("_ts", 0) > self.ttl_seconds:
                    key = meta_path.stem
                    self._remove_digest(key)
                    count += 1
            except Exception:
                continue
        self._enforce_limits()
        return count

    def stats(self) -> dict[str, Any]:
        """返回缓存统计信息。"""
        items = 0
        total_size = 0
        for meta_path in self.meta_dir.glob("*.json"):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                items += 1
                total_size += meta.get("_size", 0)
            except Exception:
                continue
        return {
            "items": items,
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "max_items": self.max_items,
            "max_size_mb": self.max_size_bytes / 1024 / 1024,
        }
