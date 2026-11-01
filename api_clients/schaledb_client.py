from __future__ import annotations

import asyncio
from difflib import SequenceMatcher
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


class StudentSuggestionError(StudentAmbiguousError):
    """Raised when only approximate student matches can be suggested."""

    def __init__(self, query: str, candidates: list[dict[str, Any]]):
        self.query = query
        self.candidates = candidates
        names = [
            str(student.get("Name") or student.get("PathName") or student.get("Id"))
            for student in candidates
        ]
        suggestions = "、".join(dict.fromkeys(names))
        super().__init__(query, candidates)
        self.args = (
            f"「{query}」没有直接匹配，可能是：{suggestions}。请确认后选择。",
        )


class StudentCandidateMatches(list[dict[str, Any]]):
    """All forms of the best-matching student plus other likely identities."""

    def __init__(
        self,
        candidates: list[dict[str, Any]],
        *,
        primary_name: str,
        recommendations: list[str],
        needs_confirmation: bool,
        ranked_identities: list[dict[str, Any]] | None = None,
        variant_terms: set[str] | None = None,
        variant_terms_by_id: dict[int, set[str]] | None = None,
        base_student_ids: set[int] | None = None,
    ):
        super().__init__(candidates)
        self.primary_name = primary_name
        self.recommendations = recommendations
        self.needs_confirmation = needs_confirmation
        self.ranked_identities = ranked_identities or []
        self.variant_terms = variant_terms or set()
        self.variant_terms_by_id = variant_terms_by_id or {}
        self.base_student_ids = base_student_ids or set()


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
        "dress": "礼服",
        "hot_spring": "温泉",
        "uniform": "制服",
        "battle_tank": "武装",
        "battle_dealer": "武装",
    }
    VARIANT_SEARCH_LABELS = {
        "泳装": ("swimsuit", "水着"),
        "新年": ("newyear", "new year", "和服"),
        "赛车": ("racing",),
        "兔女郎": ("bunny",),
        "女仆": ("maid",),
        "圣诞": ("christmas",),
        "应援团": ("cheerleader",),
        "运动服": ("track",),
        "礼服": ("dress",),
        "温泉": ("hot spring", "hotspring"),
        "制服": ("uniform",),
        "武装": ("battle", "tactical", "临战"),
    }
    STUDENT_SEARCH_ALIASES = {
        "啊露": "爱露",
        "阿露": "爱露",
        "水星野": "星野",
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
        self.student_aliases: dict[str, str] = {}
        self._dataset_memory_cache: dict[
            tuple[str, str], tuple[tuple[int, int, int] | None, Any]
        ] = {}
        self._student_search_index_cache: tuple[
            tuple[Any, ...], dict[str, Any]
        ] | None = None

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

    @staticmethod
    def _file_signature(path: Path) -> tuple[int, int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size, getattr(stat, "st_ino", 0)

    def _read_dataset_cache(self, path: Path) -> Any | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    async def _fetch_dataset(self, resource: str, language: str) -> Any:
        path = self._cache_path(language, resource)
        cache_key = (language, resource)
        file_signature = self._file_signature(path)
        memory_entry = self._dataset_memory_cache.get(cache_key)
        if (
            memory_entry is not None
            and memory_entry[0] == file_signature
            and file_signature is not None
            and file_signature[0] / 1_000_000_000 + self.data_ttl_seconds
            > time.time()
        ):
            return memory_entry[1]

        cached = self._read_dataset_cache(path)
        if cached is not None:
            try:
                file_signature = self._file_signature(path)
                if (
                    file_signature is not None
                    and file_signature[0] / 1_000_000_000 + self.data_ttl_seconds
                    > time.time()
                ):
                    self._dataset_memory_cache[cache_key] = (
                        file_signature,
                        cached,
                    )
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
            self._dataset_memory_cache[cache_key] = (
                self._file_signature(path),
                payload,
            )
            return payload
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            SchaleDBError,
            OSError,
            ValueError,
        ) as exc:
            fallback = cached
            if fallback is None and memory_entry is not None:
                fallback = memory_entry[1]
            if fallback is not None:
                self.last_error = f"{exc}; using cached {resource} data"
                self._dataset_memory_cache[cache_key] = (
                    self._file_signature(path),
                    fallback,
                )
                return fallback
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

    @classmethod
    def _canonical_search_name(cls, value: str) -> str:
        normalized = cls._normalize_name(value)
        normalized = re.sub(r"[()（）\[\]【】_\-·・]", "", normalized)
        labels = sorted(
            (
                (alias, canonical)
                for canonical, aliases in cls.VARIANT_SEARCH_LABELS.items()
                for alias in (canonical, *aliases)
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        for label, canonical in labels:
            if normalized.startswith(label) and len(normalized) > len(label):
                return f"{normalized[len(label):]}{canonical}"
        return normalized

    @classmethod
    def _name_without_separators(cls, value: str) -> str:
        normalized = cls._normalize_name(value)
        return re.sub(r"[()（）\[\]【】_\-·・]", "", normalized)

    def _expand_student_aliases(self, value: str) -> str:
        normalized = self._normalize_name(value)
        aliases = {**self.STUDENT_SEARCH_ALIASES, **self.student_aliases}
        for alias, canonical in sorted(
            aliases.items(), key=lambda item: len(item[0]), reverse=True
        ):
            normalized = normalized.replace(
                self._normalize_name(alias), self._normalize_name(canonical)
            )
        return normalized

    @classmethod
    def _variant_search_parts(
        cls, value: str, data_driven_variants: set[str] | None = None
    ) -> tuple[str, str] | None:
        normalized = cls._name_without_separators(value)
        for label, aliases in cls.VARIANT_SEARCH_LABELS.items():
            for variant in (label, *aliases):
                if normalized.startswith(variant) and len(normalized) > len(variant):
                    return normalized[len(variant):], label
                if normalized.endswith(variant) and len(normalized) > len(variant):
                    return normalized[:-len(variant)], label
        for variant in sorted(data_driven_variants or (), key=len, reverse=True):
            if normalized.startswith(variant) and len(normalized) > len(variant):
                return normalized[len(variant):], variant
            if normalized.endswith(variant) and len(normalized) > len(variant):
                return normalized[:-len(variant)], variant
        return None

    @classmethod
    def _canonical_variant_term(cls, value: str) -> str:
        normalized = cls._name_without_separators(value)
        for label, aliases in cls.VARIANT_SEARCH_LABELS.items():
            if normalized in {
                cls._name_without_separators(alias)
                for alias in (label, *aliases)
            }:
                return label
        return normalized

    @classmethod
    def _derive_variant_term(cls, form_name: str, base_name: str) -> str | None:
        form = cls._name_without_separators(form_name)
        base = cls._name_without_separators(base_name)
        if not form or not base or form == base:
            return None
        if form.startswith(base):
            term = form[len(base):]
        elif form.endswith(base):
            term = form[:-len(base)]
        else:
            return None
        if len(term) < 2 or (term.isascii() and term.isalpha() and len(term) < 3):
            return None
        return cls._canonical_variant_term(term)

    @classmethod
    def _student_search_group_key(
        cls, student: dict[str, Any], path_names: set[str] | None = None
    ) -> str:
        family = student.get("FamilyName")
        personal = student.get("PersonalName")
        if isinstance(family, str) and family and isinstance(personal, str) and personal:
            return f"person:{cls._normalize_name(family + personal)}"

        path_name = str(student.get("PathName") or "").lower()
        path_parts = path_name.split("_")
        known_paths = path_names or set()
        matching_roots = [
            "_".join(path_parts[:index])
            for index in range(1, len(path_parts))
            if "_".join(path_parts[:index]) in known_paths
        ]
        if matching_roots:
            return f"path:{max(matching_roots, key=len)}"

        suffixes = sorted(
            (*cls.VARIANT_LABELS, "tactical", "new_year", "hotspring"),
            key=len,
            reverse=True,
        )
        for suffix in suffixes:
            marker = f"_{suffix}"
            if path_name.endswith(marker):
                path_name = path_name[: -len(marker)]
                break
        if path_name:
            return f"path:{path_name}"
        name = str(student.get("Name") or "")
        variant_parts = cls._variant_search_parts(name)
        if variant_parts:
            return f"name:{variant_parts[0]}"
        return f"name:{cls._canonical_search_name(name)}"

    @classmethod
    def _fuzzy_search_names(
        cls, student: dict[str, Any], data_driven_variants: set[str]
    ) -> set[tuple[str, float]]:
        family = student.get("FamilyName")
        personal = student.get("PersonalName")
        weighted_names: list[tuple[str, float]] = []
        for field in ("Name", "PersonalName"):
            value = student.get(field)
            if isinstance(value, str) and value:
                weighted_names.append((value, 1.0))
        if isinstance(family, str) and family and isinstance(personal, str) and personal:
            weighted_names.append((family + personal, 0.9))
        if isinstance(family, str) and family:
            weighted_names.append((family, 0.7))
        search_tags = student.get("SearchTags")
        if isinstance(search_tags, list):
            weighted_names.extend(
                (tag, 0.9)
                for tag in search_tags
                if isinstance(tag, str) and tag
            )
        for field in ("DevName", "PathName"):
            value = student.get(field)
            if isinstance(value, str) and value:
                weighted_names.append((value, 0.5))

        result = set()
        for value, weight in weighted_names:
            candidate = cls._canonical_search_name(value)
            variant_parts = cls._variant_search_parts(
                candidate, data_driven_variants
            )
            result.add(
                (
                    cls._canonical_search_name(
                        variant_parts[0] if variant_parts else candidate
                    ),
                    weight,
                )
            )
        return result

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

    def _build_student_search_index(
        self,
        students_by_language: dict[str, list[dict[str, Any]]],
        language: str,
        source_languages: list[str],
    ) -> dict[str, Any]:
        students_by_id: dict[str, dict[int, dict[str, Any]]] = {
            source_language: {
                int(student["Id"]): student
                for student in students
                if student.get("Id") is not None
            }
            for source_language, students in students_by_language.items()
        }
        preferred_records: dict[int, dict[str, Any]] = {}
        display_languages = list(dict.fromkeys((language, "cn", "en", "jp")))
        for source_language in ("cn", "en", "jp", *source_languages):
            for student in students_by_language.get(source_language, []):
                if student.get("Id") is not None:
                    preferred_records.setdefault(int(student["Id"]), student)

        display_records = {
            student_id: next(
                (
                    students_by_id[candidate_language][student_id]
                    for candidate_language in display_languages
                    if student_id in students_by_id.get(candidate_language, {})
                ),
                student,
            )
            for student_id, student in preferred_records.items()
        }
        path_names = {
            str(student.get("PathName") or "").lower()
            for student in preferred_records.values()
            if student.get("PathName")
        }
        group_student_ids: dict[str, set[int]] = {}
        identity_records: dict[str, list[dict[str, Any]]] = {}
        records_by_id: dict[int, list[dict[str, Any]]] = {}
        names_by_id: dict[int, set[str]] = {}
        group_by_id: dict[int, str] = {}

        for student_id, preferred in preferred_records.items():
            records = [
                localized[student_id]
                for localized in students_by_id.values()
                if student_id in localized
            ]
            group_key = self._student_search_group_key(preferred, path_names)
            group_by_id[student_id] = group_key
            group_student_ids.setdefault(group_key, set()).add(student_id)
            identity_records.setdefault(group_key, []).extend(records)
            records_by_id[student_id] = records
            names_by_id[student_id] = {
                self._canonical_search_name(candidate)
                for record in records
                for candidate in self._search_names(record)
                if candidate
            }

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
                        self._canonical_search_name(variant_name)
                    )

        base_ids_by_group: dict[str, set[int]] = {}
        variant_terms_by_group: dict[str, set[str]] = {}
        variant_terms_by_id: dict[int, set[str]] = {}
        fuzzy_names_by_id: dict[int, set[tuple[str, float]]] = {}
        for group_key, student_ids in group_student_ids.items():
            paths = {
                student_id: str(
                    preferred_records[student_id].get("PathName") or ""
                ).lower()
                for student_id in student_ids
            }
            shallowest_depth = min(
                (path.count("_") for path in paths.values() if path),
                default=0,
            )
            root_ids = {
                student_id
                for student_id, path in paths.items()
                if path and path.count("_") == shallowest_depth
            }
            known_base_ids = {
                student_id
                for student_id in student_ids
                if any(
                    not self._variant_search_parts(str(record.get("Name") or ""))
                    for record in records_by_id[student_id]
                )
            }
            base_ids = root_ids or known_base_ids or set(student_ids)
            base_ids_by_group[group_key] = base_ids
            base_names_by_language: dict[str, set[str]] = {}
            for student_id in base_ids:
                for record in records_by_id[student_id]:
                    record_language = next(
                        (
                            candidate_language
                            for candidate_language, records in students_by_id.items()
                            if records.get(student_id) is record
                        ),
                        "",
                    )
                    base_name = str(record.get("Name") or "")
                    if base_name:
                        base_names_by_language.setdefault(
                            record_language, set()
                        ).add(base_name)

            for student_id in student_ids - base_ids:
                terms = set()
                for record in records_by_id[student_id]:
                    record_language = next(
                        (
                            candidate_language
                            for candidate_language, records in students_by_id.items()
                            if records.get(student_id) is record
                        ),
                        "",
                    )
                    for base_name in base_names_by_language.get(record_language, ()):
                        term = self._derive_variant_term(
                            str(record.get("Name") or ""), base_name
                        )
                        if term:
                            terms.add(term)
                path = paths.get(student_id, "")
                for base_id in base_ids:
                    base_path = paths.get(base_id, "")
                    if path.startswith(f"{base_path}_"):
                        path_term = self._canonical_variant_term(
                            path[len(base_path) + 1:]
                        )
                        if len(path_term) >= 2:
                            terms.add(path_term)
                if terms:
                    variant_terms_by_id[student_id] = terms
                    variant_terms_by_group.setdefault(group_key, set()).update(terms)

            group_terms = variant_terms_by_group.get(group_key, set())
            for student_id in student_ids:
                for candidate in tuple(names_by_id[student_id]):
                    parts = self._variant_search_parts(candidate, group_terms)
                    if parts:
                        names_by_id[student_id].add(
                            self._canonical_search_name(parts[0])
                        )
                fuzzy_names_by_id[student_id] = {
                    fuzzy_name
                    for record in records_by_id[student_id]
                    for fuzzy_name in self._fuzzy_search_names(record, group_terms)
                }

        data_driven_variants = {
            term for terms in variant_terms_by_group.values() for term in terms
        }
        return {
            "students_by_id": students_by_id,
            "preferred_records": preferred_records,
            "display_records": display_records,
            "group_student_ids": group_student_ids,
            "identity_records": identity_records,
            "records_by_id": records_by_id,
            "names_by_id": names_by_id,
            "group_by_id": group_by_id,
            "base_ids_by_group": base_ids_by_group,
            "variant_terms_by_group": variant_terms_by_group,
            "variant_terms_by_id": variant_terms_by_id,
            "fuzzy_names_by_id": fuzzy_names_by_id,
            "data_driven_variants": data_driven_variants,
        }

    def _student_search_index_signature(
        self,
        students_by_language: dict[str, list[dict[str, Any]]],
        source_languages: list[str],
        language: str,
    ) -> tuple[Any, ...]:
        signatures = []
        for source_language in source_languages:
            file_signature = self._file_signature(
                self._cache_path(source_language, "students")
            )
            fallback_identity = (
                id(students_by_language.get(source_language))
                if file_signature is None
                else None
            )
            signatures.append((source_language, file_signature, fallback_identity))
        return (language, tuple(signatures))

    async def search_student_candidates(
        self, name: str, language: str = "cn"
    ) -> list[dict[str, Any]]:
        query = self._expand_student_aliases(name)
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

        index_signature = self._student_search_index_signature(
            students_by_language, source_languages, language
        )
        if (
            self._student_search_index_cache is not None
            and self._student_search_index_cache[0] == index_signature
        ):
            search_index = self._student_search_index_cache[1]
        else:
            search_index = self._build_student_search_index(
                students_by_language, language, source_languages
            )
            self._student_search_index_cache = (index_signature, search_index)
        students_by_id = search_index["students_by_id"]
        display_records = search_index["display_records"]
        group_student_ids = search_index["group_student_ids"]
        identity_records = search_index["identity_records"]
        names_by_id = search_index["names_by_id"]
        group_by_id = search_index["group_by_id"]
        base_ids_by_group = search_index["base_ids_by_group"]
        variant_terms_by_group = search_index["variant_terms_by_group"]
        variant_terms_by_id = search_index["variant_terms_by_id"]
        data_driven_variants = search_index["data_driven_variants"]
        normalized_query = self._expand_student_aliases(name)
        variant_query = self._variant_search_parts(
            normalized_query, data_driven_variants
        )
        query_base = self._canonical_search_name(
            variant_query[0] if variant_query else normalized_query
        )
        if not query_base:
            raise SchaleDBError("请输入学生名称。")

        group_scores: dict[str, float] = {}
        for student_id, names in names_by_id.items():
            group_key = group_by_id[student_id]
            group_variants = variant_terms_by_group.get(group_key, set())

            direct_score = 0.0
            for candidate in names:
                candidate_parts = self._variant_search_parts(
                    candidate, group_variants
                )
                candidate_base = self._canonical_search_name(
                    candidate_parts[0] if candidate_parts else candidate
                )
                if not candidate_base:
                    continue
                if candidate_base == query_base:
                    direct_score = max(direct_score, 1.0)
                elif candidate_base.startswith(query_base):
                    direct_score = max(direct_score, 0.82)
                elif query_base in candidate_base or candidate_base in query_base:
                    direct_score = max(direct_score, 0.72)

            fuzzy_score = max(
                (
                    SequenceMatcher(None, query_base, candidate).ratio() * weight
                    for candidate, weight in search_index["fuzzy_names_by_id"][
                        student_id
                    ]
                ),
                default=0.0,
            )
            group_scores[group_key] = max(
                group_scores.get(group_key, 0.0), direct_score, fuzzy_score
            )

        ranked_groups = sorted(
            group_scores.items(), key=lambda item: (-item[1], item[0])
        )
        if not ranked_groups or ranked_groups[0][1] < 0.5:
            raise SchaleDBError(f"SchaleDB 中没有找到学生「{name}」。")

        primary_group, primary_score = ranked_groups[0]
        if primary_score < 0.72 and len(query_base) < 2:
            raise SchaleDBError(f"SchaleDB 中没有找到学生「{name}」。")

        primary_ids = group_student_ids[primary_group]
        needs_confirmation = primary_score < 1.0
        source_priority = {value: index for index, value in enumerate(source_languages)}
        display_priority = list(dict.fromkeys((language, "cn", "en", "jp")))

        def display_student_record(student_id: int) -> dict[str, Any] | None:
            source_language = next(
                (
                    candidate_language
                    for candidate_language in ("cn", "en", "jp", *source_languages)
                    if student_id in students_by_id.get(candidate_language, {})
                ),
                None,
            )
            if source_language is None:
                return None
            source = students_by_id[source_language][student_id]
            display_language = next(
                (
                    candidate_language
                    for candidate_language in display_priority
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
            return record

        ranked_identities = []
        for index, (group_key, score) in enumerate(ranked_groups[:5], start=1):
            ids = group_student_ids[group_key]
            forms = [
                record
                for student_id in ids
                if (record := display_student_record(student_id)) is not None
            ]
            forms.sort(
                key=lambda student: (
                    bool(
                        self._variant_search_parts(
                            str(student.get("Name") or ""),
                            variant_terms_by_group.get(group_key, set()),
                        )
                    ),
                    source_priority.get(
                        student["_data_source_language"], len(source_priority)
                    ),
                    str(student.get("Name") or student.get("PathName") or "").casefold(),
                    int(student.get("Id") or 0),
                )
            )
            ranked_identities.append(
                {
                    "candidate_id": str(index),
                    "name": self._identity_display_name(
                        identity_records[group_key],
                        display_records,
                        ids,
                        base_ids_by_group.get(group_key),
                    ),
                    "local_score": round(score * 100),
                    "variants": list(
                        dict.fromkeys(
                            str(record.get("Name") or record.get("PathName") or "")
                            for record in forms
                        )
                    ),
                    "forms": forms,
                }
            )

        recommendations = [
            identity["name"]
            for identity, (_, score) in zip(ranked_identities[1:], ranked_groups[1:])
            if score >= max(0.5, primary_score - 0.2)
        ][:3]
        recommendations = list(dict.fromkeys(recommendations))
        candidates = ranked_identities[0]["forms"]
        primary_name = self._identity_display_name(
            identity_records[primary_group],
            display_records,
            primary_ids,
            base_ids_by_group.get(primary_group),
        )
        return StudentCandidateMatches(
            candidates,
            primary_name=primary_name,
            recommendations=recommendations,
            needs_confirmation=needs_confirmation,
            ranked_identities=ranked_identities,
            variant_terms=data_driven_variants,
            variant_terms_by_id=variant_terms_by_id,
            base_student_ids=base_ids_by_group.get(primary_group, set()),
        )

    @classmethod
    def _identity_display_name(
        cls,
        records: list[dict[str, Any]],
        display_records: dict[int, dict[str, Any]],
        student_ids: set[int],
        base_student_ids: set[int] | None = None,
    ) -> str:
        if base_student_ids:
            base_records = [
                display_records[student_id]
                for student_id in base_student_ids
                if student_id in display_records
            ]
            if base_records:
                student = min(
                    base_records, key=lambda record: int(record.get("Id") or 0)
                )
                return str(student.get("Name") or student.get("PersonalName") or "")
        preferred = [
            display_records[student_id]
            for student_id in student_ids
            if student_id in display_records
        ]
        base_records = [
            record
            for record in preferred
            if not cls._variant_search_parts(str(record.get("Name") or ""))
        ]
        candidates = base_records or preferred or records
        if candidates:
            student = min(
                candidates,
                key=lambda record: int(record.get("Id") or 0),
            )
            return str(student.get("Name") or student.get("PersonalName") or "")
        return ""

    async def search_student(self, name: str, language: str = "cn") -> dict[str, Any]:
        candidates = await self.search_student_candidates(name, language)
        variant_query = self._variant_search_parts(
            self._expand_student_aliases(name),
            getattr(candidates, "variant_terms", set()),
        )
        if variant_query:
            requested_variant = variant_query[1]

            def matches_requested_variant(candidate: dict[str, Any]) -> bool:
                student_id = int(candidate.get("Id") or 0)
                if requested_variant in getattr(
                    candidates, "variant_terms_by_id", {}
                ).get(student_id, set()):
                    return True
                candidate_variant = self._variant_search_parts(
                    str(candidate.get("Name") or ""),
                    getattr(candidates, "variant_terms", set()),
                )
                return bool(
                    candidate_variant
                    and candidate_variant[1] == requested_variant
                )

            matching_variants = [
                candidate for candidate in candidates
                if matches_requested_variant(candidate)
            ]
            if len(matching_variants) == 1:
                return matching_variants[0]
            if matching_variants:
                candidates = matching_variants
        else:
            base_candidates = [
                candidate
                for candidate in candidates
                if int(candidate.get("Id") or 0)
                in getattr(candidates, "base_student_ids", set())
            ]
            if len(base_candidates) == 1:
                return base_candidates[0]
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
