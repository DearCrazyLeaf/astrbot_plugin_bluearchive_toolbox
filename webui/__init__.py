from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api.web import error_response, file_response, json_response, request


_PLUGIN_ID = "astrbot_plugin_bluearchive_toolbox"


class BAToolboxWebUI:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"

    def register(self) -> None:
        self.plugin.context.register_web_api(
            f"/{_PLUGIN_ID}/settings",
            self.settings,
            ["GET"],
            "BA Toolbox plugin settings",
        )
        self.plugin.context.register_web_api(
            f"/{_PLUGIN_ID}/settings",
            self.save_settings,
            ["POST"],
            "Save BA Toolbox plugin settings",
        )
        self.plugin.context.register_web_api(
            f"/{_PLUGIN_ID}/cache-usage",
            self.cache_usage,
            ["GET"],
            "BA Toolbox cache usage",
        )
        self.plugin.context.register_web_api(
            f"/{_PLUGIN_ID}/poll-archive",
            self.poll_archive_days,
            ["GET"],
            "BA Toolbox daily poll archive",
        )
        self.plugin.context.register_web_api(
            f"/{_PLUGIN_ID}/poll-archive/<archive_date>",
            self.poll_archive_day,
            ["GET"],
            "BA Toolbox poll archive day",
        )
        self.plugin.context.register_web_api(
            f"/{_PLUGIN_ID}/poll-archive/<archive_date>/<category>/<run_id>",
            self.poll_archive_image,
            ["GET"],
            "BA Toolbox archived poll card",
        )

    @staticmethod
    def _directory_usage(directory: Path) -> dict[str, int]:
        total_bytes = 0
        files = 0
        pending = [directory]
        while pending:
            current = pending.pop()
            try:
                entries = os.scandir(current)
            except FileNotFoundError:
                continue
            with entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total_bytes += entry.stat(follow_symlinks=False).st_size
                        files += 1
        return {"bytes": total_bytes, "files": files}

    def _cache_usage(self) -> dict[str, Any]:
        cache_dir = Path(self.plugin._cache_dir)
        data_dir = Path(self.plugin._data_dir)
        categories = [
            ("card_images", "卡片图片", cache_dir / "images"),
            ("card_metadata", "卡片索引", cache_dir / "meta"),
            ("schaledb_data", "SchaleDB 数据", cache_dir / "schaledb" / "data"),
            ("schaledb_assets", "SchaleDB 图片资源", cache_dir / "schaledb" / "assets"),
            ("downloaded_fonts", "下载字体", data_dir / "fonts"),
        ]
        usage = [
            {"id": key, "label": label, **self._directory_usage(path)}
            for key, label, path in categories
        ]
        image_cache = self.plugin.image_cache
        return {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "total_bytes": sum(item["bytes"] for item in usage),
            "total_files": sum(item["files"] for item in usage),
            "card_cache_max_items": image_cache.max_items,
            "card_cache_max_bytes": image_cache.max_size_bytes,
            "categories": usage,
        }

    async def cache_usage(self):
        try:
            return json_response({"data": self._cache_usage()})
        except OSError as exc:
            self.plugin._log_warn("webui", "cache usage read failed", error=exc)
            return error_response("读取缓存用量失败", status_code=500)

    async def poll_archive_days(self):
        try:
            return json_response({"data": {"days": self.plugin.poll_archive.list_days()}})
        except (OSError, RuntimeError) as exc:
            self.plugin._log_warn("webui", "poll archive listing failed", error=exc)
            return error_response("读取轮询记录失败", status_code=500)

    async def poll_archive_day(self, archive_date: str):
        try:
            entries = self.plugin.poll_archive.get_day(archive_date)
        except ValueError:
            return error_response("日期格式无效", status_code=400)
        except (OSError, RuntimeError) as exc:
            self.plugin._log_warn("webui", "poll archive day read failed", error=exc)
            return error_response("读取当日轮询记录失败", status_code=500)
        return json_response({"data": {"date": archive_date, "entries": entries}})

    async def poll_archive_image(
        self, archive_date: str, category: str, run_id: str
    ):
        try:
            path = self.plugin.poll_archive.image_path(
                archive_date, category, run_id
            )
        except ValueError:
            return error_response("轮询卡片地址无效", status_code=400)
        except FileNotFoundError:
            return error_response("轮询卡片不存在", status_code=404)
        except OSError as exc:
            self.plugin._log_warn("webui", "poll archive image read failed", error=exc)
            return error_response("读取轮询卡片失败", status_code=500)
        return file_response(path, content_type="image/png")

    def _schema(self) -> dict[str, Any]:
        try:
            data = json.loads(self.schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to load plugin configuration schema: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Plugin configuration schema must be a JSON object")
        return data

    def _groups(self) -> dict[str, Any]:
        groups: dict[str, Any] = {}
        for group_name, group_schema in self._schema().items():
            if not isinstance(group_schema, dict) or not isinstance(
                group_schema.get("items"), dict
            ):
                continue
            values: dict[str, Any] = {}
            configured_group = self.plugin.config.get(group_name, {})
            if not isinstance(configured_group, dict):
                configured_group = {}
            for key, field_schema in group_schema["items"].items():
                if not isinstance(field_schema, dict) or field_schema.get("invisible"):
                    continue
                if key in configured_group:
                    value = configured_group[key]
                else:
                    value = self.plugin.config.get(key, field_schema.get("default"))
                values[key] = deepcopy(value)
            groups[group_name] = {
                "description": group_schema.get("description", group_name),
                "hint": group_schema.get("hint", ""),
                "items": group_schema["items"],
                "values": values,
            }
        return groups

    async def settings(self):
        try:
            return json_response({"data": {"groups": self._groups()}})
        except RuntimeError as exc:
            self.plugin._log_warn("webui", "configuration schema unavailable", error=exc)
            return error_response(str(exc), status_code=500)

    async def save_settings(self):
        body = await request.json(default={})
        if not isinstance(body, dict) or not isinstance(body.get("groups"), dict):
            return error_response("请求配置格式无效")

        snapshot = deepcopy(dict(self.plugin.config))
        try:
            schema = self._schema()
            known_groups = set(schema)
            groups = body["groups"]
            unknown_groups = set(groups) - known_groups
            if unknown_groups:
                raise ValueError("包含未知配置组")

            validated_groups: dict[str, dict[str, Any]] = {}
            for group_name, values in groups.items():
                group_schema = schema.get(group_name)
                if (
                    not isinstance(values, dict)
                    or not isinstance(group_schema, dict)
                    or not isinstance(group_schema.get("items"), dict)
                ):
                    raise ValueError(f"{group_name} 配置格式无效")
                item_schemas = group_schema["items"]
                allowed_keys = {
                    key
                    for key, item in item_schemas.items()
                    if isinstance(item, dict) and not item.get("invisible")
                }
                if set(values) - allowed_keys:
                    raise ValueError(f"{group_name} 包含未知配置项")

                validated_values = {}
                for key, value in values.items():
                    validated_values[key] = self._validate_value(
                        key, value, item_schemas[key]
                    )
                validated_groups[group_name] = validated_values

            for group_name, values in validated_groups.items():
                current_group = self.plugin.config.get(group_name, {})
                merged = dict(current_group) if isinstance(current_group, dict) else {}
                merged.update(deepcopy(values))
                for key, value in values.items():
                    if key in self.plugin.config:
                        self.plugin.config[key] = deepcopy(value)
                self.plugin.config[group_name] = merged
            self.plugin.config.save_config()
        except (ValueError, TypeError) as exc:
            self._restore_config(snapshot)
            return error_response(str(exc))
        except Exception as exc:
            self._restore_config(snapshot)
            self.plugin._log_warn("webui", "configuration save failed", error=exc)
            return error_response("配置保存失败，原配置已恢复", status_code=500)
        return json_response({"data": {"saved": True}})

    def _validate_value(self, key: str, value: Any, schema: dict[str, Any]) -> Any:
        kind = schema.get("type")
        label = schema.get("description", key)
        if kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{label} 必须为布尔值")
            return value
        if kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{label} 必须为整数")
            if key == "retention_days" and not 1 <= value <= 365:
                raise ValueError(f"{label} 必须在 1 到 365 天之间")
            return value
        if kind == "string":
            if not isinstance(value, str):
                raise ValueError(f"{label} 必须为文本")
            options = schema.get("options")
            if isinstance(options, list) and value not in options:
                raise ValueError(f"{label} 的选项无效")
            if key == "poll_time" and not re.fullmatch(
                r"(?:[01]\d|2[0-3]):[0-5]\d", value
            ):
                raise ValueError(f"{label} 格式应为 HH:MM")
            return value
        if kind == "list":
            if not isinstance(value, list):
                raise ValueError(f"{label} 必须为列表")
            item_schema = schema.get("items", {})
            item_type = item_schema.get("type") if isinstance(item_schema, dict) else None
            if item_type == "string":
                if any(not isinstance(item, str) for item in value):
                    raise ValueError(f"{label} 中的值必须为文本")
                options = item_schema.get("options")
                if isinstance(options, list) and any(
                    item not in options for item in value
                ):
                    raise ValueError(f"{label} 中包含无效选项")
                if key == "poll_categories":
                    if not value:
                        raise ValueError(f"{label} 至少需要一个类别")
                    if len(set(value)) != len(value):
                        raise ValueError(f"{label} 不能包含重复类别")
            if item_type == "int" and any(
                isinstance(item, bool) or not isinstance(item, int) for item in value
            ):
                raise ValueError(f"{label} 中的值必须为整数")
            if item_type == "bool" and any(not isinstance(item, bool) for item in value):
                raise ValueError(f"{label} 中的值必须为布尔值")
            nested_schema = (
                {
                    child_key: child
                    for child_key, child in item_schema.items()
                    if isinstance(child, dict)
                }
                if isinstance(item_schema, dict) and item_type is None
                else {}
            )
            if nested_schema:
                for index, item in enumerate(value):
                    if isinstance(item, str):
                        continue
                    if not isinstance(item, dict):
                        raise ValueError(f"{label} 第 {index + 1} 项必须为文本或对象")
                    unknown = set(item) - set(nested_schema)
                    if unknown:
                        raise ValueError(f"{label} 第 {index + 1} 项包含未知字段")
                    for child_key, child_value in item.items():
                        self._validate_value(
                            child_key, child_value, nested_schema[child_key]
                        )
            return deepcopy(value)
        raise ValueError(f"配置项 {label} 的类型暂不支持")

    def _restore_config(self, snapshot: dict[str, Any]) -> None:
        try:
            self.plugin.config.clear()
            self.plugin.config.update(snapshot)
        except Exception as exc:
            self.plugin._log_warn("webui", "configuration rollback failed", error=exc)
