from __future__ import annotations

import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any


_CATEGORIES = {
    "schedule": "日程",
    "birthday": "生日",
    "pool": "千里眼",
}
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_RUN_ID_PATTERN = re.compile(r"\d{8}T\d{6}\Z")


class PollArchive:
    """Persist daily polling cards separately from the bounded image cache."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _date_path(value: str) -> date:
        if not _DATE_PATTERN.fullmatch(value):
            raise ValueError("Invalid archive date")
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Invalid archive date") from exc

    def save_result(
        self,
        *,
        archive_date: str,
        run_id: str,
        category: str,
        region: str,
        item_count: int,
        image: bytes | None,
        error: str | None = None,
        delivery_results: list[dict[str, str]] | None = None,
        has_push_targets: bool = False,
    ) -> dict[str, Any]:
        self._date_path(archive_date)
        if not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("Invalid polling run ID")
        if category not in _CATEGORIES:
            raise ValueError("Invalid archive category")
        if item_count < 0:
            raise ValueError("Invalid item count")

        day_dir = self.directory / archive_date
        day_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{category}_{run_id}.png" if image is not None else None
        if filename is not None:
            image_path = day_dir / filename
            temp_path = image_path.with_suffix(".png.tmp")
            try:
                temp_path.write_bytes(image)
                temp_path.replace(image_path)
            finally:
                temp_path.unlink(missing_ok=True)

        entry = {
            "id": f"{category}-{run_id}",
            "run_id": run_id,
            "category": category,
            "label": _CATEGORIES[category],
            "region": region,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": "failed" if error else "success",
            "item_count": item_count,
            "error": error[:500] if error else "",
            "image": filename,
            "delivery_status": self._delivery_status(
                delivery_results or [],
                has_push_targets,
                image is not None,
                pending=has_push_targets and image is not None and not delivery_results,
            ),
            "delivery_results": delivery_results or [],
        }
        manifest_path = day_dir / "index.json"
        entries = self._read_manifest(manifest_path)
        entries.append(entry)
        temp_manifest = manifest_path.with_suffix(".json.tmp")
        try:
            temp_manifest.write_text(
                json.dumps(
                    {"date": archive_date, "entries": entries},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temp_manifest.replace(manifest_path)
        finally:
            temp_manifest.unlink(missing_ok=True)
        return entry

    @staticmethod
    def _delivery_status(
        results: list[dict[str, str]],
        has_push_targets: bool,
        has_image: bool,
        pending: bool = False,
    ) -> str:
        if not has_image:
            return "skipped"
        if not has_push_targets:
            return "unconfigured"
        if pending:
            return "pending"
        if not results:
            return "failed"
        sent_count = sum(result.get("status") == "sent" for result in results)
        if sent_count == len(results):
            return "sent"
        if sent_count:
            return "partial"
        return "failed"

    def update_delivery(
        self,
        *,
        archive_date: str,
        category: str,
        run_id: str,
        delivery_results: list[dict[str, str]],
    ) -> dict[str, Any]:
        self._date_path(archive_date)
        if not _RUN_ID_PATTERN.fullmatch(run_id) or category not in _CATEGORIES:
            raise ValueError("Invalid archive entry identifier")

        manifest_path = self.directory / archive_date / "index.json"
        entries = self._read_manifest(manifest_path)
        entry_id = f"{category}-{run_id}"
        entry = next((item for item in entries if item.get("id") == entry_id), None)
        if entry is None:
            raise FileNotFoundError("Poll archive entry not found")
        entry["delivery_results"] = delivery_results
        entry["delivery_status"] = self._delivery_status(
            delivery_results, True, entry.get("image") is not None
        )
        temp_manifest = manifest_path.with_suffix(".json.tmp")
        try:
            temp_manifest.write_text(
                json.dumps(
                    {"date": archive_date, "entries": entries},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temp_manifest.replace(manifest_path)
        finally:
            temp_manifest.unlink(missing_ok=True)
        return entry

    @staticmethod
    def _read_manifest(path: Path) -> list[dict[str, Any]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to read poll archive index: {exc}") from exc
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, list) or any(
            not isinstance(entry, dict) for entry in entries
        ):
            raise RuntimeError("Poll archive index has an invalid format")
        return entries

    def has_run(self, archive_date: str, run_id: str) -> bool:
        self._date_path(archive_date)
        entries = self._read_manifest(self.directory / archive_date / "index.json")
        return any(entry.get("id", "").endswith(f"-{run_id}") for entry in entries)

    def has_entries(self, archive_date: str) -> bool:
        self._date_path(archive_date)
        return bool(
            self._read_manifest(self.directory / archive_date / "index.json")
        )

    def list_days(self) -> list[dict[str, Any]]:
        days = []
        for day_dir in self.directory.iterdir():
            if not day_dir.is_dir() or not _DATE_PATTERN.fullmatch(day_dir.name):
                continue
            try:
                self._date_path(day_dir.name)
                entries = self._read_manifest(day_dir / "index.json")
            except (ValueError, RuntimeError):
                continue
            days.append(
                {
                    "date": day_dir.name,
                    "entry_count": len(entries),
                    "success_count": sum(
                        entry.get("status") == "success" for entry in entries
                    ),
                    "failure_count": sum(
                        entry.get("status") == "failed" for entry in entries
                    ),
                }
            )
        return sorted(days, key=lambda item: item["date"], reverse=True)

    def get_day(self, archive_date: str) -> list[dict[str, Any]]:
        self._date_path(archive_date)
        return self._read_manifest(self.directory / archive_date / "index.json")

    def image_path(self, archive_date: str, category: str, run_id: str) -> Path:
        self._date_path(archive_date)
        if category not in _CATEGORIES or not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("Invalid archive image identifier")
        directory = (self.directory / archive_date).resolve()
        root = self.directory.resolve()
        if directory.parent != root:
            raise ValueError("Invalid archive image path")
        path = (directory / f"{category}_{run_id}.png").resolve()
        if path.parent != directory or not path.is_file():
            raise FileNotFoundError("Poll archive image not found")
        return path

    def cleanup(self, retention_days: int, today: date | None = None) -> int:
        if retention_days < 1:
            raise ValueError("Retention must be at least one day")
        cutoff = (today or date.today()).toordinal() - retention_days
        removed = 0
        root = self.directory.resolve()
        for day_dir in self.directory.iterdir():
            if not day_dir.is_dir() or not _DATE_PATTERN.fullmatch(day_dir.name):
                continue
            try:
                day = self._date_path(day_dir.name)
            except ValueError:
                continue
            resolved = day_dir.resolve()
            if resolved.parent != root or day.toordinal() >= cutoff:
                continue
            shutil.rmtree(resolved)
            removed += 1
        return removed
