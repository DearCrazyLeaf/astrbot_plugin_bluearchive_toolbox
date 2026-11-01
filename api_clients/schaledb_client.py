from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from uuid import uuid4

import aiohttp
from PIL import Image


class SchaleDBError(RuntimeError):
    """Raised when SchaleDB data cannot be retrieved or matched."""


class StudentAmbiguousError(SchaleDBError):
    """Raised when a student query matches multiple selectable records."""

    def __init__(self, query: str, candidates: list[dict[str, Any]]):
        self.query = query
        self.candidates = candidates
        super().__init__(f"「{query}」匹配到多个学生。")


class SchaleDBClient:
    """Fetch and cache SchaleDB student records and their render assets."""

    API_BASE = "https://schaledb.com"
    LANGUAGES = {"cn", "jp", "en", "tw", "kr", "th", "vi"}
    VARIANT_LABELS = {
        "racing": "赛车",
        "swimsuit": "泳装",
        "newyear": "新年",
        "bunny": "兔女郎",
        "maid": "女仆",
        "christmas": "圣诞",
        "cheerleader": "应援团",
        "track": "运动服",
    }
    SCHOOLS_WITH_GENERIC_ICON = {"Sakugawa"}
    HEADERS = {
        "Accept": "application/json, image/avif, image/webp, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    def __init__(
        self,
        cache_dir: str | Path,
        base_url: str | None = None,
        timeout: int = 30,
        data_ttl_seconds: int = 86400,
    ):
        self.cache_dir = Path(cache_dir)
        self.data_dir = self.cache_dir / "data"
        self.assets_dir = self.cache_dir / "assets"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = (base_url or self.API_BASE).rstrip("/")
        self.timeout = timeout
        self.data_ttl_seconds = data_ttl_seconds
        self._session: aiohttp.ClientSession | None = None
        self.last_error: str | None = None
        self.asset_errors: dict[str, str] = {}

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
    def normalize_language(language: str) -> str:
        language = language.strip().lower()
        if language == "global":
            language = "en"
        if language not in SchaleDBClient.LANGUAGES:
            return "cn"
        return language

    @staticmethod
    def _records(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            return [record for record in payload.values() if isinstance(record, dict)]
        if isinstance(payload, list):
            return [record for record in payload if isinstance(record, dict)]
        raise SchaleDBError("SchaleDB returned an unsupported data structure.")

    @staticmethod
    def _skill_records(student: dict[str, Any]) -> list[dict[str, Any]]:
        raw_skills = student.get("Skills", [])
        if isinstance(raw_skills, list):
            return [skill for skill in raw_skills if isinstance(skill, dict)]
        if not isinstance(raw_skills, dict):
            return []

        type_names = {
            "normal": "autoattack",
            "ex": "ex",
            "public": "normal",
            "gearpublic": "gearnormal",
            "passive": "passive",
            "weaponpassive": "weaponpassive",
            "extrapassive": "extrapassive",
        }
        result = []
        for key, skill in raw_skills.items():
            if not isinstance(skill, dict):
                continue
            result.append(
                {
                    **skill,
                    "SkillType": type_names.get(str(key).lower(), str(key).lower()),
                }
            )
        return result

    def _cache_path(self, language: str, resource: str) -> Path:
        return self.data_dir / f"{language}_{resource}.json"

    def _read_dataset_cache(self, path: Path) -> Any | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    async def _fetch_dataset(self, resource: str, language: str) -> Any:
        path = self._cache_path(language, resource)
        cached = self._read_dataset_cache(path)
        if cached is not None:
            try:
                if path.stat().st_mtime + self.data_ttl_seconds > time.time():
                    return cached
            except OSError:
                cached = None

        url = f"{self.base_url}/data/{language}/{resource}.min.json"
        try:
            session = await self._get_session()
            async with session.get(url) as response:
                if response.status != 200:
                    raise SchaleDBError(
                        f"SchaleDB {resource} request failed: HTTP {response.status}."
                    )
                payload = await response.json(content_type=None)
            self._records(payload)
            temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            return payload
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            SchaleDBError,
            OSError,
            ValueError,
        ) as exc:
            if cached is not None:
                self.last_error = f"{exc}; using cached {resource} data"
                return cached
            if isinstance(exc, SchaleDBError):
                raise
            raise SchaleDBError(f"Unable to fetch SchaleDB {resource}: {exc}") from exc

    async def get_students(self, language: str = "cn") -> list[dict[str, Any]]:
        self.last_error = None
        language = self.normalize_language(language)
        payload = await self._fetch_dataset("students", language)
        return self._records(payload)

    @staticmethod
    def _normalize_name(value: str) -> str:
        value = value.strip().lower().replace("（", "(").replace("）", ")")
        return re.sub(r"\s+", "", value)

    @staticmethod
    def _search_names(student: dict[str, Any]) -> list[str]:
        fields = (
            "Name",
            "PathName",
            "DevName",
            "FamilyName",
            "PersonalName",
            "SearchTags",
        )
        names: list[str] = []
        for field in fields:
            value = student.get(field)
            if isinstance(value, str) and value:
                names.append(value)
            elif isinstance(value, list):
                names.extend(item for item in value if isinstance(item, str) and item)

        family = student.get("FamilyName")
        personal = student.get("PersonalName")
        if family and personal:
            names.extend((f"{family}{personal}", f"{family} {personal}"))
        return names

    @staticmethod
    def _merge_localized_student(
        latest: dict[str, Any], localized: dict[str, Any] | None
    ) -> dict[str, Any]:
        if not localized:
            return dict(latest)

        text_fields = {
            "Name",
            "FamilyName",
            "PersonalName",
            "Desc",
            "Description",
            "ProfileIntroduction",
            "Hobby",
            "CharacterSSRNew",
            "CharacterVoice",
            "SchoolYear",
            "CharacterAge",
            "CharHeightMetric",
            "CharHeightImperial",
            "Birthday",
            "BirthDay",
        }

        def merge(target: Any, source: Any) -> Any:
            if not isinstance(target, dict) or not isinstance(source, dict):
                return target
            result = dict(target)
            for key, value in source.items():
                if key in text_fields and isinstance(value, str) and value:
                    result[key] = value
                elif isinstance(value, dict) and isinstance(result.get(key), dict):
                    result[key] = merge(result[key], value)
            return result

        return merge(latest, localized)

    @classmethod
    def _localized_variant_name(
        cls,
        student: dict[str, Any],
        localized_students: list[dict[str, Any]],
        language: str,
    ) -> str | None:
        if language != "cn":
            return None
        path_name = str(student.get("PathName") or "").lower()
        for suffix, label in cls.VARIANT_LABELS.items():
            marker = f"_{suffix}"
            if not path_name.endswith(marker):
                continue
            base_path = path_name[: -len(marker)]
            base_student = next(
                (
                    candidate
                    for candidate in localized_students
                    if str(candidate.get("PathName") or "").lower() == base_path
                    and candidate.get("Name")
                ),
                None,
            )
            if base_student:
                return f"{base_student['Name']}（{label}）"
        return None

    @classmethod
    def _merge_student_for_display(
        cls,
        latest: dict[str, Any],
        localized: dict[str, Any] | None,
        localized_students: list[dict[str, Any]],
        language: str,
    ) -> dict[str, Any]:
        record = cls._merge_localized_student(latest, localized)
        if localized and cls._normalize_name(
            str(localized.get("Name") or "")
        ) == cls._normalize_name(str(latest.get("Name") or "")):
            variant_name = cls._localized_variant_name(
                latest, localized_students, language
            )
            if variant_name:
                record["Name"] = variant_name
        return record

    async def search_student_candidates(
        self, name: str, language: str = "cn"
    ) -> list[dict[str, Any]]:
        query = self._normalize_name(name)
        if not query:
            raise SchaleDBError("请输入学生名称。")

        language = self.normalize_language(language)
        source_languages = ["cn", "en", "jp"]
        if language not in source_languages:
            source_languages.append(language)

        students_by_language: dict[str, list[dict[str, Any]]] = {}
        errors: list[str] = []
        fallback_notices: list[str] = []
        for source_language in source_languages:
            try:
                students_by_language[source_language] = await self.get_students(
                    source_language
                )
                if self.last_error:
                    fallback_notices.append(f"{source_language}: {self.last_error}")
            except SchaleDBError as exc:
                errors.append(f"{source_language}: {exc}")

        if not any(students_by_language.values()):
            detail = "；".join(errors)
            raise SchaleDBError(
                f"无法获取 SchaleDB 学生数据。{f' {detail}' if detail else ''}"
            )
        self.last_error = "；".join((*fallback_notices, *errors)) or None

        students_by_id: dict[str, dict[int, dict[str, Any]]] = {
            source_language: {
                int(student["Id"]): student
                for student in students
                if student.get("Id") is not None
            }
            for source_language, students in students_by_language.items()
        }
        names_by_id: dict[int, set[str]] = {}
        for students in students_by_language.values():
            for student in students:
                if student.get("Id") is None:
                    continue
                student_id = int(student["Id"])
                names_by_id.setdefault(student_id, set()).update(
                    self._normalize_name(candidate)
                    for candidate in self._search_names(student)
                    if candidate
                )
        localized_students = students_by_language.get(language, [])
        if language == "cn":
            for student in students_by_language.get("jp", []):
                if student.get("Id") is None:
                    continue
                variant_name = self._localized_variant_name(
                    student, localized_students, language
                )
                if variant_name:
                    names_by_id.setdefault(int(student["Id"]), set()).add(
                        self._normalize_name(variant_name)
                    )

        exact: set[int] = set()
        scored: list[tuple[int, int, int]] = []
        base_query = re.sub(r"\([^)]*\)", "", query)
        for student_id, names in names_by_id.items():
            if query in names:
                exact.add(student_id)
                continue
            score = (
                max(
                    (
                        700
                        if candidate.startswith(query)
                        else 600
                        if query in candidate
                        else 400
                        if base_query and candidate == base_query
                        else 0
                    )
                    for candidate in names
                )
                if names
                else 0
            )
            if score:
                scored.append((score, -min(map(len, names)), student_id))

        matches = exact
        if not matches and scored:
            best_score = max((score, length) for score, length, _ in scored)
            matches = {
                student_id
                for score, length, student_id in scored
                if (score, length) == best_score
            }

        if not matches:
            raise SchaleDBError(f"SchaleDB 中没有找到学生「{name}」。")

        display_languages = list(dict.fromkeys((language, "cn", "en", "jp")))
        candidates = []
        for student_id in matches:
            source_language = next(
                (
                    candidate_language
                    for candidate_language in ("cn", "en", "jp", *source_languages)
                    if student_id in students_by_id.get(candidate_language, {})
                ),
                None,
            )
            if source_language is None:
                continue
            source = students_by_id[source_language][student_id]
            display_language = next(
                (
                    candidate_language
                    for candidate_language in display_languages
                    if student_id in students_by_id.get(candidate_language, {})
                ),
                source_language,
            )
            localized = students_by_id.get(display_language, {}).get(student_id)
            display_students = students_by_language.get(display_language, [])
            record = self._merge_localized_student(source, localized)
            if (
                language == "cn"
                and localized
                and self._normalize_name(str(localized.get("Name") or ""))
                == self._normalize_name(str(source.get("Name") or ""))
            ):
                variant_name = self._localized_variant_name(
                    source, display_students, language
                )
                if variant_name:
                    record["Name"] = variant_name
            record["_data_source_language"] = source_language
            record["_display_language"] = display_language
            candidates.append(record)

        source_priority = {value: index for index, value in enumerate(source_languages)}
        candidates.sort(
            key=lambda student: (
                source_priority.get(
                    student["_data_source_language"], len(source_priority)
                ),
                str(student.get("Name") or student.get("PathName") or "").casefold(),
                int(student.get("Id") or 0),
            )
        )
        return candidates

    async def search_student(self, name: str, language: str = "cn") -> dict[str, Any]:
        candidates = await self.search_student_candidates(name, language)
        if len(candidates) > 1:
            raise StudentAmbiguousError(name, candidates)
        return candidates[0]

    async def _download_asset(self, key: str, url: str) -> tuple[str, str | None]:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        suffix = Path(urlparse(url).path).suffix.lower() or ".img"
        target = self.assets_dir / f"{digest}{suffix}"
        if target.is_file() and target.stat().st_size:
            try:
                with Image.open(target) as image:
                    image.verify()
                return key, str(target)
            except (OSError, ValueError):
                target.unlink(missing_ok=True)

        try:
            session = await self._get_session()
            async with session.get(url) as response:
                if response.status != 200:
                    raise SchaleDBError(f"HTTP {response.status} for {url}")
                content_type = response.headers.get("Content-Type", "").lower()
                if not content_type.startswith("image/"):
                    raise SchaleDBError(
                        f"Non-image response for {url}: {content_type or 'unknown type'}"
                    )
                data = await response.read()
            if not data or len(data) > 25 * 1024 * 1024:
                raise SchaleDBError(f"Invalid image size for {url}")
            with Image.open(BytesIO(data)) as image:
                image.verify()
            temporary = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(data)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            return key, str(target)
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            OSError,
            ValueError,
            SchaleDBError,
        ) as exc:
            self.asset_errors[key] = str(exc)
            return key, None

    async def _download_assets(self, specs: dict[str, str]) -> dict[str, str]:
        self.asset_errors.clear()
        semaphore = asyncio.Semaphore(8)

        async def limited_download(key: str, url: str) -> tuple[str, str | None]:
            async with semaphore:
                return await self._download_asset(key, url)

        results = await asyncio.gather(
            *(limited_download(key, url) for key, url in specs.items())
        )
        return {key: path for key, path in results if path is not None}

    async def prepare_student(
        self, student: dict[str, Any], language: str = "cn"
    ) -> dict[str, Any]:
        """Return the raw SchaleDB record with validated local card assets."""
        language = self.normalize_language(language)
        prepared = dict(student)
        specs: dict[str, str] = {}
        base = self.base_url + "/images"

        student_id = student.get("Id")
        if student_id:
            specs["portrait"] = f"{base}/student/portrait/{quote(str(student_id))}.webp"
            specs["student_icon"] = f"{base}/student/icon/{quote(str(student_id))}.webp"
            weapon_image = student.get("WeaponImg")
            if isinstance(weapon_image, str) and weapon_image:
                specs["weapon"] = f"{base}/weapon/{quote(weapon_image)}.webp"
            else:
                weapon_owner_id = student_id
                family_name = student.get("FamilyName")
                personal_name = student.get("PersonalName")
                path_name = str(student.get("PathName") or "").lower()
                if path_name or (family_name and personal_name):
                    try:
                        localized_students = await self.get_students(language)
                    except SchaleDBError as exc:
                        self.last_error = (
                            f"Unable to resolve the student's base weapon: {exc}"
                        )
                    else:
                        base_student = None
                        if path_name:
                            by_path = {
                                str(candidate.get("PathName") or "").lower(): candidate
                                for candidate in localized_students
                                if candidate.get("Id") is not None
                            }
                            path_parts = path_name.split("_")
                            for part_count in range(len(path_parts) - 1, 0, -1):
                                base_student = by_path.get(
                                    "_".join(path_parts[:part_count])
                                )
                                if base_student:
                                    break
                        if (
                            base_student is None
                            and isinstance(family_name, str)
                            and isinstance(personal_name, str)
                        ):
                            matching_students = [
                                candidate
                                for candidate in localized_students
                                if candidate.get("Id") is not None
                                and self._normalize_name(
                                    str(candidate.get("FamilyName") or "")
                                )
                                == self._normalize_name(family_name)
                                and self._normalize_name(
                                    str(candidate.get("PersonalName") or "")
                                )
                                == self._normalize_name(personal_name)
                            ]
                            if matching_students:
                                base_student = min(
                                    matching_students,
                                    key=lambda candidate: int(candidate["Id"]),
                                )
                        if base_student:
                            weapon_owner_id = base_student["Id"]
                specs["weapon"] = (
                    f"{base}/weapon/weapon_icon_{quote(str(weapon_owner_id))}.webp"
                )
            memory_lobby = student.get("MemoryLobby")
            if isinstance(memory_lobby, list) and any(
                isinstance(level, int) and level > 0 for level in memory_lobby
            ):
                specs["lobby"] = f"{base}/student/lobby/{quote(str(student_id))}.webp"
            if isinstance(student.get("Gear"), dict) and student["Gear"].get("Name"):
                specs["gear"] = f"{base}/gear/icon/{quote(str(student_id))}.webp"

        school = student.get("School")
        if (
            isinstance(school, str)
            and school
            and school not in self.SCHOOLS_WITH_GENERIC_ICON
        ):
            specs["school"] = f"{base}/schoolicon/{quote(school)}.png"

        for equipment in student.get("Equipment", []):
            if isinstance(equipment, str):
                specs[f"equipment:{equipment}"] = (
                    f"{base}/equipment/icon/equipment_icon_{quote(equipment.lower())}_tier9.webp"
                )

        skills = self._skill_records(student)
        for skill in skills:
            if skill.get("Icon"):
                icon = str(skill["Icon"])
                specs[f"skill:{icon}"] = f"{base}/skill/{quote(icon)}.webp"

        for terrain in ("Street", "Outdoor", "Indoor"):
            icon = {
                "Street": "Terrain_Street",
                "Outdoor": "Terrain_Outdoor",
                "Indoor": "Terrain_Indoor",
            }[terrain]
            specs[f"terrain:{terrain}"] = f"{base}/ui/{icon}.png"

        items_by_id: dict[int, dict[str, Any]] = {}
        furniture_by_id: dict[int, dict[str, Any]] = {}
        localization: dict[str, Any] = {}
        try:
            item_payload, furniture_payload = await asyncio.gather(
                self._fetch_dataset("items", language),
                self._fetch_dataset("furniture", language),
            )
            items_by_id = {
                int(item["Id"]): item
                for item in self._records(item_payload)
                if item.get("Id") is not None
            }
            furniture_by_id = {
                int(item["Id"]): item
                for item in self._records(furniture_payload)
                if item.get("Id") is not None
            }
        except (SchaleDBError, TypeError, ValueError) as exc:
            self.last_error = f"Unable to load card item metadata: {exc}"
        try:
            localization_payload = await self._fetch_dataset("localization", language)
            if isinstance(localization_payload, dict):
                localization = localization_payload
        except SchaleDBError as exc:
            self.last_error = f"Unable to load card localization: {exc}"

        favor_tags = set(student.get("FavorItemTags") or [])
        favor_tags.update(student.get("FavorItemUniqueTags") or [])
        favorites = [
            item
            for item in items_by_id.values()
            if item.get("Category") == "Favor"
            and favor_tags.intersection(item.get("Tags") or [])
        ]
        favorites.sort(
            key=lambda item: (item.get("Rarity", 0), item.get("Id", 0)),
            reverse=True,
        )
        for item in favorites:
            if item.get("Icon"):
                icon = str(item["Icon"])
                specs[f"item:{icon}"] = f"{base}/item/icon/{quote(icon)}.webp"

        furniture_ids: set[int] = set()

        def collect_ids(value: Any) -> None:
            if isinstance(value, list):
                for child in value:
                    collect_ids(child)
            elif isinstance(value, int):
                furniture_ids.add(value)

        collect_ids(student.get("FurnitureInteraction") or [])
        unique_tags = set(student.get("FavorItemUniqueTags") or [])
        furnitures = [
            furniture
            for furniture_id, furniture in furniture_by_id.items()
            if furniture_id in furniture_ids
            and unique_tags.intersection(furniture.get("Tags") or [])
        ]
        for furniture in furnitures:
            if furniture.get("Icon"):
                icon = str(furniture["Icon"])
                specs[f"furniture:{icon}"] = f"{base}/furniture/icon/{quote(icon)}.webp"

        material_ids: set[int] = set()
        material_ids.update(
            item_id
            for key in ("SkillExMaterial", "SkillMaterial")
            for row in (student.get(key) or [])
            if isinstance(row, list)
            for item_id in row
            if isinstance(item_id, int)
        )
        for item_id in material_ids:
            item = items_by_id.get(item_id)
            if item and item.get("Icon"):
                icon = str(item["Icon"])
                specs[f"item:{icon}"] = f"{base}/item/icon/{quote(icon)}.webp"

        prepared["_render_assets"] = await self._download_assets(specs)
        prepared["_render_skills"] = skills
        prepared["_render_favorites"] = favorites
        prepared["_render_furnitures"] = furnitures
        prepared["_render_items_by_id"] = items_by_id
        prepared["_render_localization"] = localization
        return prepared

    async def get_birthdays_with_ids(
        self, language: str = "cn"
    ) -> list[tuple[str, str, int | None]]:
        language = self.normalize_language(language)
        try:
            latest = await self.get_students("jp")
        except SchaleDBError:
            latest = []
        localized = latest
        if language != "jp":
            try:
                localized = await self.get_students(language)
            except SchaleDBError:
                localized = []
        latest_by_id = {
            int(student["Id"]): student
            for student in latest
            if student.get("Id") is not None
        }
        localized_by_id = {
            int(student["Id"]): student
            for student in localized
            if student.get("Id") is not None
        }
        if latest_by_id:
            students = [
                self._merge_student_for_display(
                    student,
                    localized_by_id.get(student_id),
                    localized,
                    language,
                )
                for student_id, student in latest_by_id.items()
            ]
        else:
            students = localized
        unique_birthdays: dict[str, tuple[str, str, int | None]] = {}
        for student in students:
            birthday = student.get("Birthday") or student.get("BirthDay")
            if not birthday:
                continue
            name = str(student.get("Name") or student.get("PathName") or "未知")
            path_name = str(student.get("PathName") or "").lower()
            for suffix in self.VARIANT_LABELS:
                marker = f"_{suffix}"
                if path_name.endswith(marker):
                    path_name = path_name[: -len(marker)]
                    break
            family = str(student.get("FamilyName") or "")
            personal = str(student.get("PersonalName") or "")
            base_name = re.sub(r"[\(（][^()（）]*[\)）]$", "", name).strip()
            identity = self._normalize_name(
                base_name
                or path_name
                or (f"{family}{personal}" if family and personal else name)
            )
            student_id = student.get("Id")
            unique_birthdays.setdefault(
                identity,
                (
                    name,
                    str(birthday),
                    int(student_id) if student_id is not None else None,
                ),
            )
        birthdays = list(unique_birthdays.values())

        def sort_key(record: tuple[str, str, int | None]) -> tuple[int, int, str]:
            match = re.search(r"(\d{1,2})\s*(?:/|月|-)\s*(\d{1,2})", record[1])
            if not match:
                return 13, 32, record[0]
            return int(match.group(1)), int(match.group(2)), record[0]

        return sorted(birthdays, key=sort_key)

    async def get_birthdays(self, language: str = "cn") -> list[tuple[str, str]]:
        return [
            (name, birthday)
            for name, birthday, _ in await self.get_birthdays_with_ids(language)
        ]

    async def get_student_icons_by_id(
        self, student_ids: list[int | str]
    ) -> dict[str, str]:
        requested = list(dict.fromkeys(str(student_id) for student_id in student_ids))
        if not requested:
            return {}
        base = f"{self.base_url}/images/student/icon"
        specs = {
            f"student_icon_id:{student_id}": f"{base}/{quote(student_id)}.webp"
            for student_id in requested
        }
        assets = await self._download_assets(specs)
        return {
            student_id: assets[f"student_icon_id:{student_id}"]
            for student_id in requested
            if f"student_icon_id:{student_id}" in assets
        }

    async def get_student_icon_map(
        self, names: list[str], language: str = "cn"
    ) -> dict[str, str]:
        """Resolve localized student names to cached SchaleDB icon image paths."""
        requested = {
            self._normalize_name(name): name.strip()
            for name in names
            if isinstance(name, str) and name.strip()
        }
        if not requested:
            return {}

        language = self.normalize_language(language)
        try:
            latest = await self.get_students("jp")
        except SchaleDBError:
            latest = []
        localized = latest
        if language != "jp":
            try:
                localized = await self.get_students(language)
            except SchaleDBError:
                localized = []
        if not latest and not localized:
            raise SchaleDBError("无法获取 SchaleDB 学生数据。")

        students_by_id = {
            int(student["Id"]): student
            for student in (*latest, *localized)
            if student.get("Id") is not None
        }
        name_ids: dict[str, set[int]] = {}
        for student_id, student in students_by_id.items():
            for alias in self._search_names(student):
                name_ids.setdefault(self._normalize_name(alias), set()).add(student_id)
        if language != "jp":
            for student in latest:
                if student.get("Id") is None:
                    continue
                variant_name = self._localized_variant_name(
                    student, localized, language
                )
                if variant_name:
                    name_ids.setdefault(self._normalize_name(variant_name), set()).add(
                        int(student["Id"])
                    )

        matches = {
            normalized: next(iter(student_ids))
            for normalized in requested
            if len(student_ids := name_ids.get(normalized, set())) == 1
        }
        for normalized, name in requested.items():
            if normalized in matches:
                continue
            try:
                student = await self.search_student(name, language)
            except SchaleDBError as exc:
                if "没有找到学生" in str(exc) or "匹配到多个" in str(exc):
                    continue
                raise
            student_id = student.get("Id")
            if student_id is not None:
                matches[normalized] = int(student_id)

        base = f"{self.base_url}/images/student/icon"
        specs = {
            f"student_icon:{normalized}": f"{base}/{quote(str(student_id))}.webp"
            for normalized, student_id in matches.items()
        }
        assets = await self._download_assets(specs)
        return {
            requested[normalized]: path
            for normalized in matches
            if (path := assets.get(f"student_icon:{normalized}"))
        }
