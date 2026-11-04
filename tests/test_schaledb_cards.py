from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from api_clients.arona_client import AronaAmbiguousError, AronaClient
from api_clients.schaledb_client import (
    SchaleDBClient,
    StudentAmbiguousError,
)
from api_clients.cache_manager import ImageCacheManager
from api_clients.gamekee import GameKeeClient
from renderers.card import (
    BirthdayCard,
    CardPoolCard,
    ScheduleCard,
    parse_birthday_date,
)
from renderers.student_detail import StudentDetailCard, _format_description


class ImageCacheManagerTests(unittest.TestCase):
    def test_put_returns_managed_png_and_enforces_item_limit(self):
        with TemporaryDirectory() as temp_dir:
            cache = ImageCacheManager(temp_dir, max_items=1)

            first = cache.put("first", b"first png")
            second = cache.put("second", b"second png")

            self.assertTrue(second.is_file())
            self.assertEqual(second.suffix, ".png")
            self.assertEqual(second.read_bytes(), b"second png")
            self.assertFalse(first.exists())


class _AronaResponse:
    status = 200

    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, **_kwargs):
        return self.data


class _AronaSession:
    def __init__(self, data):
        self.data = data

    def get(self, *_args, **_kwargs):
        return _AronaResponse(self.data)


class _GameKeeResponse:
    status = 200

    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, **_kwargs):
        return self.data


class _GameKeeSession:
    def __init__(self):
        self.headers = {}
        self.urls = []

    def get(self, url):
        from urllib.parse import parse_qs, urlparse

        self.urls.append(url)
        if "/indexV2" in url:
            regions = {
                server_id: [
                    {
                        "title": f"{server_name}活动",
                        "begin_at": 1761955200,
                        "end_at": 1762041600,
                    }
                ]
                for region, server_id in GameKeeClient.SERVER_IDS.items()
                for server_name in [GameKeeClient.SERVER_NAMES[region]]
            }
            return _GameKeeResponse(
                {
                    "data": [
                        {
                            "module": {"name": "活动周历"},
                            "list": regions,
                        }
                    ]
                }
            )

        query = parse_qs(urlparse(url).query)
        server_id = query["serverId"][0]
        server_name = next(
            GameKeeClient.SERVER_NAMES[region]
            for region, value in GameKeeClient.SERVER_IDS.items()
            if value == server_id
        )
        return _GameKeeResponse(
            {
                "data": [
                    {
                        "name": f"{server_name}卡池",
                        "start_at": 1761955200,
                        "end_at": 4102444800,
                    }
                ]
            }
        )


class GameKeeClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_server_queries_merge_events_and_pools_with_server_labels(self):
        client = GameKeeClient()
        session = _GameKeeSession()
        client._get_session = AsyncMock(return_value=session)

        events = await client.get_activity_calendar("all")
        self.assertIsNone(client.last_error)
        self.assertEqual(
            {event["pub_area"] for event in events},
            {"国服", "国际服", "日服"},
        )
        self.assertEqual(len(session.urls), 3)

        session.urls.clear()
        pools = await client.get_card_pools("all")
        self.assertIsNone(client.last_error)
        self.assertEqual(
            {pool["pub_area"] for pool in pools},
            {"国服", "国际服", "日服"},
        )
        self.assertEqual(len(session.urls), 3)


class AronaClientTests(unittest.IsolatedAsyncioTestCase):
    def test_schaledb_forms_map_to_confirmed_arona_catalog_names(self):
        self.assertEqual(
            AronaClient.guide_name_for_student(
                {"PathName": "aris_maid", "Name": "爱丽丝（女仆）"}
            ),
            "女仆爱丽丝",
        )
        self.assertEqual(
            AronaClient.guide_name_for_student(
                {"PathName": "hifumi_racing", "Name": "ヒフミ（レーシング）"}
            ),
            "赛车日富美",
        )
        self.assertEqual(
            AronaClient.guide_name_for_student(
                {"PathName": "aris", "Name": "爱丽丝"}
            ),
            "爱丽丝",
        )

    def test_schaledb_parenthesized_forms_are_prefixed_for_arona(self):
        self.assertEqual(
            AronaClient.guide_name_for_student(
                {"PathName": "azusa_swimsuit", "Name": "梓（泳装）"}
            ),
            "泳装梓",
        )
        self.assertEqual(
            AronaClient.guide_name_for_student(
                {"PathName": "haruka_newyear", "Name": "春香（新年）"}
            ),
            "正月春香",
        )
        self.assertEqual(
            AronaClient.guide_name_for_student(
                {"PathName": "aru_newyear", "Name": "爱露（新年）"},
                {"新年": "和服"},
            ),
            "和服爱露",
        )

    async def test_fuzzy_guide_matches_return_candidate_names_not_first_image(self):
        client = AronaClient()
        client._get_session = AsyncMock(
            return_value=_AronaSession(
                {
                    "code": 101,
                    "data": [
                        {"name": "日富美", "content": "/student_rank/hifumi.png"},
                        {"name": "泳装日富美", "content": "/student_rank/swimsuit.png"},
                    ],
                }
            )
        )

        with self.assertRaises(AronaAmbiguousError) as raised:
            await client.get_guide_image("日富美（赛车）")

        self.assertIn("日富美、泳装日富美", str(raised.exception))

    async def test_exact_guide_match_returns_url(self):
        client = AronaClient()
        client._get_session = AsyncMock(
            return_value=_AronaSession(
                {
                    "code": 200,
                    "data": [{"name": "日富美", "content": "/student_rank/hifumi.png"}],
                }
            )
        )

        self.assertEqual(
            await client.get_guide_image("日富美"),
            "https://arona.cdn.diyigemt.com/image/s/student_rank/hifumi.png",
        )


class SchaleDBClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_region_labels_follow_is_released_not_locale_dataset(self):
        language_records = []
        for language, names in (
            ("cn", ("日富美", "日富美（泳装）", "日富美（赛车）")),
            ("en", ("Hifumi", "Hifumi (Swimsuit)", "Hifumi (Racing)")),
            ("jp", ("ヒフミ", "ヒフミ（水着）", "ヒフミ（レーシング）")),
        ):
            language_records.append(
                [
                    {
                        "Id": student_id,
                        "Name": name,
                        "PathName": path_name,
                        "FamilyName": "Ajitani",
                        "PersonalName": "Hifumi",
                        "IsReleased": [True, True, True]
                        if student_id != 10153
                        else [True, False, False],
                    }
                    for student_id, path_name, name in zip(
                        (10003, 20005, 10153),
                        ("hifumi", "hifumi_swimsuit", "hifumi_racing"),
                        names,
                    )
                ]
            )

        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(side_effect=language_records)
            candidates = await client.search_student_candidates("日富美", "cn")

        racing = next(item for item in candidates if item["Id"] == 10153)
        self.assertEqual(racing["_data_source_language"], "cn")
        self.assertEqual(racing["_released_regions"], ["日服"])

    async def test_dataset_json_is_loaded_once_into_memory_cache(self):
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            path = client._cache_path("cn", "students")
            path.write_text(
                '[{"Id":1,"Name":"星野"}]',
                encoding="utf-8",
            )
            with patch.object(
                client, "_read_dataset_cache", wraps=client._read_dataset_cache
            ) as read_cache:
                first = await client._fetch_dataset("students", "cn")
                second = await client._fetch_dataset("students", "cn")

        self.assertIs(first, second)
        read_cache.assert_called_once()

    async def test_prepare_student_downloads_base_weapon_and_unique_gear_icons(self):
        student = {
            "Id": 10153,
            "PathName": "hifumi_racing",
            "FamilyName": "Hifumi",
            "PersonalName": "Ajitani",
            "Weapon": {"Name": "My Necessity"},
            "Gear": {"Name": "MomoFriends Heater"},
            "MemoryLobby": [6, 0, 0],
        }
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(
                return_value=[
                    {
                        "Id": 10003,
                        "PathName": "hifumi",
                        "FamilyName": "Hifumi",
                        "PersonalName": "Hifumi",
                    },
                    {
                        "Id": 10153,
                        "PathName": "hifumi_racing",
                        "FamilyName": "Hifumi",
                        "PersonalName": "Hifumi",
                    },
                ]
            )
            client._fetch_dataset = AsyncMock(return_value=[])
            client._download_assets = AsyncMock(return_value={})

            await client.prepare_student(student, "cn")

        specs = client._download_assets.await_args.args[0]
        self.assertEqual(
            specs["weapon"],
            "https://schaledb.com/images/weapon/weapon_icon_10003.webp",
        )
        self.assertEqual(
            specs["gear"], "https://schaledb.com/images/gear/icon/10153.webp"
        )
        self.assertEqual(
            specs["lobby"], "https://schaledb.com/images/student/lobby/10153.webp"
        )

    async def test_prepare_student_uses_weapon_icon_and_all_schools(self):
        student = {
            "Id": 12345,
            "School": "WildHunt",
            "WeaponImg": "weapon_icon_12345",
        }
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client._fetch_dataset = AsyncMock(return_value=[])
            client._download_assets = AsyncMock(return_value={})

            await client.prepare_student(student, "cn")

        specs = client._download_assets.await_args.args[0]
        self.assertEqual(
            specs["weapon"],
            "https://schaledb.com/images/weapon/weapon_icon_12345.webp",
        )
        self.assertEqual(
            specs["school"], "https://schaledb.com/images/schoolicon/WildHunt.png"
        )

    async def test_prepare_student_uses_generic_school_icon_when_unavailable(self):
        student = {"Id": 26011, "School": "Sakugawa"}
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client._fetch_dataset = AsyncMock(return_value=[])
            client._download_assets = AsyncMock(return_value={})

            prepared = await client.prepare_student(student, "cn")

        specs = client._download_assets.await_args.args[0]
        self.assertNotIn("school", specs)
        card = StudentDetailCard({**prepared, "_render_assets": {}})
        school_markup = card._build_html()
        self.assertIn("栅川中学", school_markup)
        self.assertIn("img/schaledb/Terrain_Street.png", school_markup)

    async def test_search_uses_schaledb_name_fields(self):
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(
                return_value=[
                    {"Id": 10000, "Name": "Aru", "PathName": "aru", "Skills": {}},
                    {"Id": 10001, "Name": "Aru (New Year)", "PathName": "aru_newyear"},
                ]
            )
            result = await client.search_student("aru")

        self.assertEqual(result["Id"], 10000)

    async def test_search_uses_japanese_record_and_localized_display_name(self):
        chinese = [
            {
                "Id": 20005,
                "Name": "日富美（泳装）",
                "BulletType": "Pierce",
                "AttackPower100": 1234,
                "Skills": {"Ex": {"Name": "泳装攻击", "Desc": "造成伤害"}},
            }
        ]
        english = [
            {
                "Id": 20005,
                "Name": "Hifumi (Swimsuit)",
                "BulletType": "Pierce",
                "AttackPower100": 1234,
                "Skills": {"Ex": {"Name": "Swimsuit Attack", "Desc": "Deals damage"}},
            }
        ]
        japanese = [
            {
                "Id": 20005,
                "Name": "ヒフミ（水着）",
                "BulletType": "Pierce",
                "AttackPower100": 1234,
                "Skills": {"Ex": {"Name": "Japanese", "Desc": "Japanese desc"}},
            }
        ]
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(side_effect=[chinese, english, japanese])
            result = await client.search_student("日富美（泳装）", "cn")

        self.assertEqual(result["Id"], 20005)
        self.assertEqual(result["Name"], "日富美（泳装）")
        self.assertEqual(result["Skills"]["Ex"]["Name"], "泳装攻击")
        self.assertEqual(result["AttackPower100"], 1234)
        self.assertEqual(result["_data_source_language"], "cn")
        self.assertEqual(result["_display_language"], "cn")

    async def test_search_falls_back_to_localized_only_student(self):
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(
                side_effect=[
                    [{"Id": 2, "Name": "CN only student", "PathName": "cn_only"}],
                    [],
                    [{"Id": 1, "Name": "JP student", "PathName": "jp_student"}],
                ]
            )
            result = await client.search_student("CN only student", "cn")

        self.assertEqual(result["Id"], 2)
        self.assertEqual(result["_data_source_language"], "cn")

    async def test_search_matches_localized_name_for_untranslated_jp_variant(self):
        racing_jp = {
            "Id": 10153,
            "Name": "Hifumi (Racing)",
            "PathName": "hifumi_racing",
            "BulletType": "Explosion",
        }
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(
                side_effect=[
                    [
                        {
                            "Id": 10003,
                            "Name": "日富美",
                            "PathName": "hifumi",
                        },
                        {
                            "Id": 10153,
                            "Name": "Hifumi (Racing)",
                            "PathName": "hifumi_racing",
                            "BulletType": "Explosion",
                        },
                    ],
                    [],
                    [racing_jp],
                ]
            )
            result = await client.search_student("日富美（赛车）", "cn")

        self.assertEqual(result["Id"], 10153)
        self.assertEqual(result["Name"], "日富美（赛车）")
        self.assertEqual(result["BulletType"], "Explosion")

    async def test_plain_name_groups_cross_language_forms_and_keeps_base_form_first(self):
        cn_students = [
            {
                "Id": 10003,
                "Name": "日富美",
                "PathName": "hifumi",
                "FamilyName": "阿慈谷",
                "PersonalName": "日富美",
            },
            {
                "Id": 20005,
                "Name": "日富美（泳装）",
                "PathName": "hifumi_swimsuit",
                "FamilyName": "阿慈谷",
                "PersonalName": "日富美",
            },
            {
                "Id": 10153,
                "Name": "ヒフミ（レーシング）",
                "PathName": "hifumi_racing",
                "FamilyName": "阿慈谷",
                "PersonalName": "ヒフミ",
            },
        ]
        en_students = [
            {
                "Id": 10003,
                "Name": "Hifumi",
                "PathName": "hifumi",
                "FamilyName": "Ajitani",
                "PersonalName": "Hifumi",
            },
            {
                "Id": 20005,
                "Name": "Hifumi (Swimsuit)",
                "PathName": "hifumi_swimsuit",
                "FamilyName": "Ajitani",
                "PersonalName": "Hifumi",
            },
            {
                "Id": 10153,
                "Name": "Hifumi (Racing)",
                "PathName": "hifumi_racing",
                "FamilyName": "Ajitani",
                "PersonalName": "Hifumi",
            },
        ]
        jp_students = [
            {
                "Id": 10003,
                "Name": "ヒフミ",
                "PathName": "hifumi",
                "FamilyName": "阿慈谷",
                "PersonalName": "ヒフミ",
            },
            {
                "Id": 20005,
                "Name": "ヒフミ（水着）",
                "PathName": "hifumi_swimsuit",
                "FamilyName": "阿慈谷",
                "PersonalName": "ヒフミ",
            },
            {
                "Id": 10153,
                "Name": "ヒフミ（レーシング）",
                "PathName": "hifumi_racing",
                "FamilyName": "阿慈谷",
                "PersonalName": "ヒフミ（レーシング）",
            },
        ]
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(
                side_effect=[cn_students, en_students, jp_students]
            )
            candidates = await client.search_student_candidates("日富美", "cn")

        self.assertEqual(
            {candidate["Id"] for candidate in candidates},
            {10003, 20005, 10153},
        )
        self.assertEqual(candidates[0]["Id"], 10003)
        self.assertFalse(candidates.needs_confirmation)

    async def test_search_matches_variant_name_when_variant_prefix_is_reordered(self):
        cn_students = [
            {"Id": 10003, "Name": "星野", "PathName": "hoshino"},
        ]
        en_students = [
            {"Id": 10003, "Name": "Hoshino", "PathName": "hoshino"},
            {
                "Id": 10004,
                "Name": "Hoshino (Swimsuit)",
                "PathName": "hoshino_swimsuit",
            },
        ]
        jp_students = [
            {"Id": 10003, "Name": "ホシノ", "PathName": "hoshino"},
            {
                "Id": 10004,
                "Name": "ホシノ（水着）",
                "PathName": "hoshino_swimsuit",
            },
        ]
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(
                side_effect=[cn_students, en_students, jp_students]
            )

            student = await client.search_student("泳装星野", "cn")

        self.assertEqual(student["Id"], 10004)
        self.assertEqual(student["PathName"], "hoshino_swimsuit")

    async def test_search_supports_partial_name_with_variant_prefix(self):
        cn_students = [
            {"Id": 10003, "Name": "星野", "PathName": "hoshino"},
            {
                "Id": 10004,
                "Name": "星野（泳装）",
                "PathName": "hoshino_swimsuit",
            },
        ]
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(return_value=cn_students)

            student = await client.search_student("泳装星", "cn")

        self.assertEqual(student["Id"], 10004)
        self.assertEqual(student["Name"], "星野（泳装）")

    async def test_search_expands_student_nicknames_and_variant_aliases(self):
        students = [
            {
                "Id": 10000,
                "Name": "爱露",
                "PathName": "aru",
                "FamilyName": "陆八魔",
                "PersonalName": "爱露",
            },
            {
                "Id": 10031,
                "Name": "爱露（新年）",
                "PathName": "aru_newyear",
                "FamilyName": "陆八魔",
                "PersonalName": "爱露",
            },
            {
                "Id": 10089,
                "Name": "爱露（礼服）",
                "PathName": "aru_dress",
                "FamilyName": "陆八魔",
                "PersonalName": "爱露",
            },
            {
                "Id": 10005,
                "Name": "星野",
                "PathName": "hoshino",
                "FamilyName": "小鸟游",
                "PersonalName": "星野",
            },
            {
                "Id": 10045,
                "Name": "星野（泳装）",
                "PathName": "hoshino_swimsuit",
                "FamilyName": "小鸟游",
                "PersonalName": "星野",
            },
        ]
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(return_value=students)

            aru_candidates = await client.search_student_candidates("啊露", "cn")
            newyear = await client.search_student_candidates("和服爱露", "cn")
            dress = await client.search_student_candidates("礼服爱露", "cn")
            hoshino_candidates = await client.search_student_candidates(
                "水星野", "cn"
            )

        self.assertEqual(
            {candidate["Id"] for candidate in aru_candidates},
            {10000, 10031, 10089},
        )
        self.assertEqual(
            {candidate["Id"] for candidate in newyear},
            {10000, 10031, 10089},
        )
        self.assertEqual(
            {candidate["Id"] for candidate in dress},
            {10000, 10031, 10089},
        )
        self.assertEqual(
            {candidate["Id"] for candidate in hoshino_candidates},
            {10005, 10045},
        )

    async def test_search_uses_user_configured_alias_and_exposes_top_identities(self):
        students = [
            {
                "Id": 10000,
                "Name": "爱露",
                "PathName": "aru",
                "FamilyName": "陆八魔",
                "PersonalName": "爱露",
            },
            {
                "Id": 10089,
                "Name": "爱露（礼服）",
                "PathName": "aru_dress",
                "FamilyName": "陆八魔",
                "PersonalName": "爱露",
            },
            {
                "Id": 10005,
                "Name": "星野",
                "PathName": "hoshino",
                "FamilyName": "小鸟游",
                "PersonalName": "星野",
            },
        ]
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.student_aliases = {"社长": "爱露"}
            client.get_students = AsyncMock(return_value=students)

            candidates = await client.search_student_candidates("社长", "cn")

        self.assertEqual({student["Id"] for student in candidates}, {10000, 10089})
        self.assertEqual(candidates.ranked_identities[0]["name"], "爱露")
        self.assertEqual(
            candidates.ranked_identities[0]["variants"],
            ["爱露", "爱露（礼服）"],
        )
        self.assertEqual(candidates.ranked_identities[1]["name"], "星野")

    async def test_search_derives_unlisted_variant_from_cached_student_relations(self):
        students = [
            {"Id": 10, "Name": "Nia", "PathName": "nia"},
            {
                "Id": 11,
                "Name": "Nia",
                "PathName": "nia_festival",
            },
            {"Id": 20, "Name": "Mika", "PathName": "mika"},
        ]
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(return_value=students)

            candidates = await client.search_student_candidates("Festival Nia", "cn")
            student = await client.search_student("Festival Nia", "cn")

        self.assertEqual({candidate["Id"] for candidate in candidates}, {10, 11})
        self.assertEqual(candidates.primary_name, "Nia")
        self.assertEqual(student["Id"], 11)
        self.assertIn("festival", candidates.variant_terms)

    async def test_student_search_index_is_reused_while_cached_data_is_unchanged(self):
        students = [
            {
                "Id": 10000,
                "Name": "爱露",
                "PathName": "aru",
                "FamilyName": "陆八魔",
                "PersonalName": "爱露",
            },
            {
                "Id": 10089,
                "Name": "爱露（礼服）",
                "PathName": "aru_dress",
                "FamilyName": "陆八魔",
                "PersonalName": "爱露",
            },
        ]
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(return_value=students)
            with patch.object(
                client,
                "_build_student_search_index",
                wraps=client._build_student_search_index,
            ) as build_index:
                await client.search_student_candidates("爱露", "cn")
                await client.search_student_candidates("礼服爱露", "cn")

        build_index.assert_called_once()

    async def test_student_search_index_rebuilds_when_source_cache_changes(self):
        students = [
            {
                "Id": 10000,
                "Name": "爱露",
                "PathName": "aru",
                "FamilyName": "陆八魔",
                "PersonalName": "爱露",
            }
        ]
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(return_value=students)
            cache_path = client._cache_path("cn", "students")
            cache_path.write_text("first", encoding="utf-8")
            with patch.object(
                client,
                "_build_student_search_index",
                wraps=client._build_student_search_index,
            ) as build_index:
                await client.search_student_candidates("爱露", "cn")
                cache_path.write_text("changed source cache", encoding="utf-8")
                await client.search_student_candidates("爱露", "cn")

        self.assertEqual(build_index.call_count, 2)

    async def test_search_suggests_nearby_students_for_confirmation(self):
        students = [
            {
                "Id": 1,
                "Name": "星野",
                "PathName": "hoshino",
                "FamilyName": "小鸟游",
                "PersonalName": "星野",
            },
            {
                "Id": 2,
                "Name": "星野（泳装）",
                "PathName": "hoshino_swimsuit",
                "FamilyName": "小鸟游",
                "PersonalName": "星野",
            },
            {
                "Id": 3,
                "Name": "日鞠",
                "PathName": "himari",
                "FamilyName": "明星",
                "PersonalName": "日鞠",
            },
        ]
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(return_value=students)

            candidates = await client.search_student_candidates("星也", "cn")

        self.assertEqual(
            {candidate["Id"] for candidate in candidates},
            {1, 2},
        )
        self.assertTrue(candidates.needs_confirmation)
        self.assertEqual(candidates.recommendations, [])

    async def test_search_recommends_similar_student_identities_not_their_forms(self):
        students = [
            {
                "Id": 1,
                "Name": "星野",
                "PathName": "hoshino",
                "FamilyName": "小鸟游",
                "PersonalName": "星野",
            },
            {
                "Id": 2,
                "Name": "星野（泳装）",
                "PathName": "hoshino_swimsuit",
                "FamilyName": "小鸟游",
                "PersonalName": "星野",
            },
            {
                "Id": 3,
                "Name": "星野子",
                "PathName": "hoshino_child",
                "FamilyName": "小鸟游",
                "PersonalName": "星野子",
            },
            {
                "Id": 4,
                "Name": "星野子（礼服）",
                "PathName": "hoshino_child_dress",
                "FamilyName": "小鸟游",
                "PersonalName": "星野子",
            },
        ]
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(return_value=students)

            candidates = await client.search_student_candidates("星野", "cn")

        self.assertEqual([candidate["Id"] for candidate in candidates], [1, 2])
        self.assertEqual(candidates.recommendations, ["星野子"])

    async def test_search_reports_ambiguous_skin_match(self):
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(
                return_value=[
                    {
                        "Id": 1,
                        "Name": "Hifumi",
                        "PathName": "hifumi",
                        "SearchTags": ["Hifumi"],
                    },
                    {
                        "Id": 2,
                        "Name": "Hifumi (Swimsuit)",
                        "PathName": "hifumi_swimsuit",
                        "SearchTags": ["Hifumi"],
                    },
                ]
            )

            with self.assertRaises(StudentAmbiguousError) as raised:
                await client.search_student("Hifumi (Racing)")

        self.assertEqual(
            {candidate["Id"] for candidate in raised.exception.candidates},
            {1, 2},
        )

    async def test_search_collects_candidates_and_prefers_cn_then_en_then_jp(self):
        cn_students = [
            {
                "Id": 1,
                "Name": "爱丽丝",
                "PathName": "alice",
                "SearchTags": ["爱丽丝"],
            },
            {
                "Id": 2,
                "Name": "爱丽丝（女仆）",
                "PathName": "alice_maid",
                "SearchTags": ["爱丽丝"],
            },
        ]
        en_students = [
            {"Id": 1, "Name": "Alice", "PathName": "alice"},
            {"Id": 2, "Name": "Alice (Maid)", "PathName": "alice_maid"},
            {"Id": 3, "Name": "Alice (Tactical)", "PathName": "alice_tactical"},
        ]
        jp_students = [
            {"Id": 1, "Name": "アリス", "PathName": "alice"},
            {"Id": 2, "Name": "アリス（メイド）", "PathName": "alice_maid"},
            {"Id": 3, "Name": "アリス（臨戦）", "PathName": "alice_tactical"},
        ]
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(
                side_effect=[cn_students, en_students, jp_students]
            )

            candidates = await client.search_student_candidates("爱丽丝", "cn")

        self.assertEqual([item["Id"] for item in candidates], [1, 2, 3])
        self.assertEqual(
            [item["Name"] for item in candidates],
            ["爱丽丝", "爱丽丝（女仆）", "Alice (Tactical)"],
        )
        self.assertEqual(
            [item["_data_source_language"] for item in candidates],
            ["cn", "cn", "en"],
        )
        self.assertEqual(
            [call.args[0] for call in client.get_students.await_args_list],
            ["cn", "en", "jp"],
        )

    async def test_search_falls_back_from_cn_to_en_before_jp(self):
        english = [{"Id": 2, "Name": "Alice (Tactical)", "PathName": "alice_tactical"}]
        japanese = [{"Id": 2, "Name": "アリス（臨戦）", "PathName": "alice_tactical"}]
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(side_effect=[[], english, japanese])

            student = await client.search_student("Alice (Tactical)", "cn")

        self.assertEqual(student["Id"], 2)
        self.assertEqual(student["Name"], "Alice (Tactical)")
        self.assertEqual(student["_data_source_language"], "en")
        self.assertEqual(student["_display_language"], "en")

    async def test_birthday_records_are_sorted_and_accept_both_api_fields(self):
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(
                return_value=[
                    {"Id": 10, "Name": "B", "Birthday": "12/1"},
                    {"Id": 20, "Name": "A", "BirthDay": "1/2"},
                    {"Name": "No birthday"},
                ]
            )
            self.assertEqual(
                await client.get_birthdays(),
                [("A", "1/2"), ("B", "12/1")],
            )
            self.assertEqual(
                await client.get_birthdays_with_ids(),
                [("A", "1/2", 20), ("B", "12/1", 10)],
            )

    async def test_birthdays_deduplicate_student_variants(self):
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(
                return_value=[
                    {
                        "Id": 10003,
                        "Name": "日富美",
                        "PathName": "hifumi",
                        "Birthday": "11/27",
                    },
                    {
                        "Id": 10153,
                        "Name": "Hifumi (Racing)",
                        "PathName": "hifumi_racing",
                        "Birthday": "11/27",
                    },
                    {
                        "Id": 10098,
                        "Name": "星野（武装）",
                        "PathName": "hoshino_battle_tank",
                        "Birthday": "1/2",
                    },
                    {
                        "Id": 10099,
                        "Name": "星野（武装）",
                        "PathName": "hoshino_battle_dealer",
                        "Birthday": "1/2",
                    },
                ]
            )
            birthdays = await client.get_birthdays("cn")

        self.assertEqual(len(birthdays), 2)
        self.assertEqual(len({name for name, _ in birthdays}), 2)

    async def test_student_icon_map_downloads_icons_by_localized_name(self):
        student = {"Id": 10153, "Name": "日富美（赛车）"}
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(return_value=[student])
            client._download_assets = AsyncMock(
                return_value={"student_icon:日富美(赛车)": "cached-icon.webp"}
            )

            icons = await client.get_student_icon_map([" 日富美（赛车） "], "cn")

        specs = client._download_assets.await_args.args[0]
        self.assertEqual(icons, {"日富美（赛车）": "cached-icon.webp"})
        self.assertEqual(
            specs["student_icon:日富美(赛车)"],
            "https://schaledb.com/images/student/icon/10153.webp",
        )

    async def test_student_icon_map_resolves_localized_variant_names(self):
        racing_student = {
            "Id": 10153,
            "PathName": "hifumi_racing",
            "Name": "Hifumi (Racing)",
        }
        base_student = {
            "Id": 10003,
            "PathName": "hifumi",
            "Name": "日富美",
        }
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client.get_students = AsyncMock(
                side_effect=[[racing_student], [base_student]]
            )
            client._download_assets = AsyncMock(
                return_value={"student_icon:日富美(赛车)": "cached-icon.webp"}
            )

            icons = await client.get_student_icon_map(["日富美（赛车）"], "cn")

        self.assertEqual(icons, {"日富美（赛车）": "cached-icon.webp"})

    async def test_student_icons_can_be_downloaded_directly_by_id(self):
        with TemporaryDirectory() as temp_dir:
            client = SchaleDBClient(temp_dir)
            client._download_assets = AsyncMock(
                return_value={"student_icon_id:10003": "cached-icon.webp"}
            )

            icons = await client.get_student_icons_by_id([10003, 10003])

        specs = client._download_assets.await_args.args[0]
        self.assertEqual(icons, {"10003": "cached-icon.webp"})
        self.assertEqual(
            specs["student_icon_id:10003"],
            "https://schaledb.com/images/student/icon/10003.webp",
        )


class StudentDetailCardTests(unittest.TestCase):
    def test_role_icons_and_special_squad_badge_render(self):
        role_icons = {
            "DamageDealer": "Common_Icon_DamageDealer.png",
            "Tanker": "Common_Icon_Tanker.png",
            "Healer": "Common_Icon_Healer.png",
            "Supporter": "Common_Icon_Supporter.png",
        }
        for role, icon in role_icons.items():
            with self.subTest(role=role):
                rendered_html = StudentDetailCard(
                    {
                        "TacticRole": role,
                        "SquadType": "Support",
                    }
                )._build_html()
                self.assertIn(icon, rendered_html)
                self.assertIn("img/ui/special.webp", rendered_html)
                self.assertIn(">支援</span>", rendered_html)

    def test_profile_and_memory_lobby_render_api_fields(self):
        student = {
            "Name": "日富美（赛车）",
            "CharacterVoice": "本渡枫",
            "Birthday": "11/27",
            "CharacterAge": "16岁",
            "CharHeightMetric": "158cm",
            "SchoolYear": "2年生",
            "Designer": "YutokaMizu",
            "Illustrator": "GINGGU",
            "Hobby": "收集可爱的东西",
            "ProfileIntroduction": "简介第一段。\n\n简介第二段。",
            "CharacterSSRNew": "目标是完赛，然后安全行驶！",
            "MemoryLobby": [6, 0, 0],
            "MemoryLobbyBGM": 265,
            "StreetBattleAdaptation": 0,
            "OutdoorBattleAdaptation": 2,
            "IndoorBattleAdaptation": 4,
            "_render_assets": {},
        }

        card = StudentDetailCard(student)
        profile = card._profile()
        memory_lobby = card._memory_lobby()
        rendered_html = card._build_html()

        self.assertIn("学生简介", profile)
        self.assertIn("本渡枫", profile)
        self.assertIn('class="notebook-holes"', profile)
        self.assertIn('class="profile-heading"', profile)
        self.assertNotIn("notebook-heart", profile)
        self.assertIn("<span>趣味</span>", profile)
        self.assertIn("<span>设计</span>", profile)
        self.assertIn("<span>插画</span>", profile)
        self.assertIn("简介第一段。<br><br>简介第二段。", profile)
        self.assertIn("目标是完赛，然后安全行驶！", profile)
        self.assertNotIn("border-top: 1px solid #cbd4da", rendered_html)
        self.assertIn("纪念大厅", memory_lobby)
        self.assertIn("羁绊等级达到6后解锁", memory_lobby)
        self.assertIn("Theme 265", memory_lobby)
        self.assertIn('class="hero-section"', rendered_html)
        self.assertIn('class="hero-bottom"', rendered_html)
        self.assertIn(
            "clip-path: polygon(7px 0, 100% 0, calc(100% - 7px) 100%, 0 100%)",
            rendered_html,
        )
        self.assertNotIn("--tag-button", rendered_html)
        self.assertLess(
            rendered_html.index('class="adapt-panel"'),
            rendered_html.index('class="memory-panel"'),
        )
        self.assertIn("background: #fff", rendered_html)

    def test_school_icon_is_shown_by_school_name_and_adaptation_has_terrain_tiles(self):
        with TemporaryDirectory() as temp_dir:
            school_icon = Path(temp_dir) / "school.png"
            school_icon.write_bytes(b"school")
            student = {
                "Id": 1,
                "Name": "Test Student",
                "School": "Trinity",
                "TacticRole": "DamageDealer",
                "BulletType": "Pierce",
                "ArmorType": "LightArmor",
                "Position": "Front",
                "SquadType": "Main",
                "CharacterAge": "16岁",
                "StreetBattleAdaptation": 0,
                "OutdoorBattleAdaptation": 2,
                "IndoorBattleAdaptation": 4,
                "_render_assets": {
                    "school": str(school_icon),
                },
            }

            card = StudentDetailCard(student)
            rendered_html = card._build_html()
            adaptation = card._adaptation()

        self.assertIn('class="school-inline"', rendered_html)
        self.assertIn('class="school-badge"', rendered_html)
        self.assertNotIn('class="tag school-tag"', rendered_html)
        self.assertIn('src="data:image/png;base64,', rendered_html)
        self.assertIn("Common_Icon_DamageDealer.png", rendered_html)
        self.assertIn("img/ui/front.webp", rendered_html)
        self.assertIn("img/ui/striker.webp", rendered_html)
        self.assertIn(">突击</span>", rendered_html)
        self.assertIn(
            "background: #f3f7fa; color: #344356; font-size: 13px",
            rendered_html,
        )
        self.assertIn(
            "filter: drop-shadow(0 2px 3px rgba(48, 77, 97, .18))",
            rendered_html,
        )
        self.assertIn("--icon-color:#4a9bc4", rendered_html)
        self.assertIn("--icon-color:#e4bd32", rendered_html)
        self.assertIn("--icon-color:#e6535b", rendered_html)
        self.assertIn("background: var(--icon-color, #4a9bc4)", rendered_html)
        self.assertNotIn("--tag-accent", rendered_html)
        self.assertNotIn("Common_Btn_Normal_", rendered_html)
        self.assertIn('class="terrain-tile" title="街道"', adaptation)
        self.assertIn('class="terrain-tile" title="室外"', adaptation)
        self.assertIn('class="terrain-tile" title="室内"', adaptation)

    def test_localized_student_merge_includes_profile_metadata(self):
        merged = SchaleDBClient._merge_localized_student(
            {
                "CharacterAge": "16歳",
                "SchoolYear": "2年生",
                "ProfileIntroduction": "Japanese profile",
            },
            {
                "CharacterAge": "16岁",
                "SchoolYear": "2年级",
                "ProfileIntroduction": "Chinese profile",
            },
        )

        self.assertEqual(merged["CharacterAge"], "16岁")
        self.assertEqual(merged["SchoolYear"], "2年级")
        self.assertEqual(merged["ProfileIntroduction"], "Chinese profile")

    def test_equipment_weapon_heading_includes_weapon_type(self):
        rendered_html = StudentDetailCard(
            {
                "WeaponType": "AR",
                "Weapon": {"Name": "My Necessity"},
            }
        )._equipment()

        self.assertIn("专武 AR", rendered_html)
        self.assertNotIn(">专武</span>", rendered_html)

    def test_equipment_labels_weapon_and_gear_and_renders_their_images(self):
        with TemporaryDirectory() as temp_dir:
            weapon_image = Path(temp_dir) / "weapon.webp"
            gear_image = Path(temp_dir) / "gear.webp"
            weapon_image.write_bytes(b"weapon")
            gear_image.write_bytes(b"gear")
            student = {
                "WeaponType": "AR",
                "Weapon": {"Name": "我的必需品"},
                "Gear": {
                    "Name": "MomoFriends特制车载加热器",
                },
                "_render_assets": {
                    "weapon": str(weapon_image),
                    "gear": str(gear_image),
                },
            }

            rendered_html = StudentDetailCard(student)._equipment()

        self.assertIn('alt="专武"', rendered_html)
        self.assertIn("专武 AR", rendered_html)
        self.assertIn("base64,d2VhcG9u", rendered_html)
        self.assertIn("<small>爱用品</small>", rendered_html)
        self.assertIn("MomoFriends特制车载加热器", rendered_html)
        self.assertIn('src="data:image/webp;base64,Z2Vhcg=="', rendered_html)
        self.assertNotIn(">AR<", rendered_html)
        self.assertNotIn("我的必需品", rendered_html)

    def test_student_detail_card_declares_blueaka_font(self):
        rendered_html = StudentDetailCard({"Name": "学生"})._build_html()

        self.assertIn("font-family: 'Blueaka'", rendered_html)
        self.assertIn("fonts/Blueaka.ttf", rendered_html)
        self.assertIn("font-family: 'ResourceHanRounded'", rendered_html)

    def test_student_detail_subtitle_is_italic_single_line_with_english_emphasis(self):
        rendered_html = StudentDetailCard(
            {
                "Name": "学生",
                "School": "Abydos",
                "Club": "Food Club",
                "SquadType": "Main",
                "Birthday": "01/01",
            }
        )._build_html()

        self.assertIn("font-style: italic; white-space: nowrap", rendered_html)
        self.assertIn("<strong>Food Club</strong>", rendered_html)

    def test_description_parameters_are_expanded_and_html_is_escaped(self):
        description = _format_description(
            "Damage <b:CriticalDamage> <?1> <img src=x>",
            [["10%", "20%"]],
        )

        self.assertIn("10% / 20%", description)
        self.assertIn("暴击伤害", description)
        self.assertIn("&lt;img", description)
        self.assertNotIn("<?1>", description)

    def test_description_resolves_skill_references_and_stat_aliases(self):
        description = _format_description(
            "<b:ATK> increases; consumes <s:CH0379_ExtraPassive>.",
            [],
            {"CH0379_ExtraPassive": "Skilled Racer"},
        )

        self.assertIn("攻击力", description)
        self.assertIn("Skilled Racer", description)
        self.assertNotIn("CH0379_ExtraPassive", description)

    def test_card_reads_api_skill_dictionary_without_mutating_it(self):
        skills = {
            "Ex": {
                "Name": "Test EX",
                "SkillType": "ex",
                "Desc": "Damage <?1>",
                "Parameters": [["100%", "200%"]],
                "Icon": "TEST",
                "Cost": [3],
            },
            "Public": {
                "Name": "Test Normal",
                "Desc": "Normal description",
                "Parameters": [],
                "Icon": "TEST",
            },
        }
        student = {
            "Id": 1,
            "Name": "Test Student",
            "StarGrade": 3,
            "Skills": skills,
        }

        rendered_html = StudentDetailCard(student)._build_html()

        self.assertIn("Test EX", rendered_html)
        self.assertIn("Test Normal", rendered_html)
        self.assertIn("100% / 200%", rendered_html)
        self.assertNotIn("<?1>", rendered_html)
        self.assertEqual(student["Skills"], skills)

    def test_card_resolves_internal_skill_reference_to_localized_skill_name(self):
        student = {
            "Id": 10153,
            "DevName": "CH0379",
            "Name": "Hifumi (Racing)",
            "StarGrade": 3,
            "Skills": {
                "Ex": {
                    "Name": "EX",
                    "Desc": "<s:CH0379_ExtraPassive> stack consumed.",
                },
                "ExtraPassive": {"Name": "Skilled Racer", "Desc": "Gain a stack."},
            },
        }

        rendered_html = StudentDetailCard(student)._build_html()

        self.assertIn("Skilled Racer</span> stack consumed.", rendered_html)
        self.assertNotIn("CH0379_ExtraPassive", rendered_html)

    def test_attack_defense_colors_and_skill_tint_match_game_types(self):
        student = {
            "Id": 1,
            "Name": "Test Student",
            "StarGrade": 3,
            "BulletType": "Pierce",
            "ArmorType": "HeavyArmor",
            "Skills": {"Ex": {"Name": "Test EX", "Icon": "TEST", "Desc": ""}},
        }
        rendered_html = StudentDetailCard(student)._build_html()

        self.assertIn("贯穿", rendered_html)
        self.assertIn("重装甲", rendered_html)
        self.assertNotIn("--tag-accent", rendered_html)
        self.assertIn("--skill-color:#e4bd32", rendered_html)
        self.assertIn("img/schaledb/Type_Attack.png", rendered_html)
        self.assertIn("img/schaledb/Type_Defense.png", rendered_html)

    def test_base_stats_render_site_icons_for_supported_attributes(self):
        student = {
            "Id": 1,
            "Name": "Test Student",
            "StarGrade": 3,
            "MaxHP1": 100,
            "MaxHP100": 1000,
            "AttackPower1": 10,
            "AttackPower100": 100,
            "DefensePower1": 5,
            "DefensePower100": 50,
            "HealPower1": 20,
            "HealPower100": 200,
            "AccuracyPoint": 100,
            "DodgePoint": 200,
            "CriticalPoint": 300,
            "CriticalDamageRate": 2000,
            "StabilityPoint": 400,
            "Range": 650,
            "AmmoCount": 5,
            "RegenCost": 700,
        }

        rendered_html = StudentDetailCard(student)._build_html()

        self.assertEqual(rendered_html.count('class="stat-icon"'), 11)
        self.assertIn("生命值", rendered_html)
        self.assertIn("攻击力", rendered_html)
        self.assertIn("Lv.1 / Lv.90", rendered_html)
        self.assertIn(">100 / 909</b>", rendered_html)
        self.assertIn(">10 / 91</b>", rendered_html)
        self.assertIn(">5 / 45</b>", rendered_html)
        self.assertIn(">20 / 182</b>", rendered_html)
        self.assertIn("Cost回复", rendered_html)
        self.assertIn("data:image/svg+xml;base64,", rendered_html)

    def test_level_90_stats_use_schaledb_level_growth_and_rounding(self):
        card = StudentDetailCard(
            {
                "MaxHP1": 2026,
                "MaxHP100": 20270,
                "AttackPower1": 409,
                "AttackPower100": 4099,
            }
        )

        stats_html = card._stats()

        self.assertIn(">2026 / 18427</b>", stats_html)
        self.assertIn(">409 / 3726</b>", stats_html)

    def test_data_cards_escape_text_and_expand_for_content(self):
        events = [
            {
                "title": f"Event {index} <unsafe>",
                "start_at": 1761955200,
                "end_at": 1762041600,
                "pub_area": "日服",
            }
            for index in range(8)
        ]
        schedule = ScheduleCard(events=events, region="jp")
        pool = CardPoolCard(pools=events[:2], region="jp")
        birthday = BirthdayCard(students=[("Student <unsafe>", "1/2")], month=1)

        self.assertGreater(schedule.CARD_H, 220)
        self.assertIn("&lt;unsafe&gt;", schedule._build_html())
        self.assertIn("卡池千里眼", pool._build_html())
        self.assertIn("&lt;unsafe&gt;", birthday._build_html())

    def test_schedule_cards_show_the_selected_server_or_all_label(self):
        for region, expected_label in (
            ("cn", "国服"),
            ("global", "国际服"),
            ("jp", "日服"),
            ("all", "全服"),
        ):
            with self.subTest(region=region):
                for card in (
                    ScheduleCard(
                        events=[{"title": "活动", "pub_area": "日服"}],
                        region=region,
                    ),
                    CardPoolCard(
                        pools=[{"title": "卡池", "pub_area": "日服"}],
                        region=region,
                    ),
                ):
                    rendered_html = card._build_html()
                    self.assertIn(
                        f'<span class="region">{expected_label}</span>',
                        rendered_html,
                    )
                    if region == "all":
                        self.assertIn(
                            '<h2 class="server-heading">日服</h2>',
                            rendered_html,
                        )

        all_events = [{"title": f"活动{i}", "pub_area": "日服"} for i in range(12)]
        all_card = ScheduleCard(events=all_events, region="all")
        self.assertEqual(
            all_card._build_html().count('<article class="schedule-row">'), 12
        )

    def test_schedule_card_groups_servers_and_sorts_events_as_calendar(self):
        card = ScheduleCard(
            events=[
                {
                    "title": "较晚活动",
                    "pub_area": "国际服",
                    "start_at": 1762041600,
                    "end_at": 1762128000,
                },
                {
                    "title": "日服活动",
                    "pub_area": "日服",
                    "start_at": 1761955200,
                    "end_at": 1762041600,
                },
                {
                    "title": "较早活动",
                    "pub_area": "国际服",
                    "start_at": 1761955200,
                    "end_at": 1762041600,
                },
            ],
            region="all",
        )
        rendered_html = card._build_html()

        self.assertIn('<h2 class="server-heading">国际服</h2>', rendered_html)
        self.assertIn('<h2 class="server-heading">日服</h2>', rendered_html)
        self.assertEqual(rendered_html.count('class="event-check"'), 3)
        self.assertEqual(
            rendered_html.count(
                'src="ba-asset://img/ui/Common_Icon_Check_Y_On.png"'
            ),
            3,
        )
        self.assertLess(rendered_html.index("较早活动"), rendered_html.index("较晚活动"))
        self.assertLess(
            rendered_html.index('<h2 class="server-heading">国际服</h2>'),
            rendered_html.index('<h2 class="server-heading">日服</h2>'),
        )
        self.assertIn("11月01日", rendered_html)
        self.assertIn("11月03日", rendered_html)

    def test_card_header_introductions_share_italic_single_line_style(self):
        card_html = (
            ScheduleCard()._build_html(),
            CardPoolCard(pools=[])._build_html(),
            BirthdayCard()._build_html(),
        )

        for rendered_html in card_html:
            with self.subTest(card=rendered_html[:80]):
                self.assertIn("font-style: italic; white-space: nowrap", rendered_html)
                self.assertIn("font-weight: 700", rendered_html)
                self.assertIn('class="header-caption"', rendered_html)

    def test_schedule_pool_and_birthday_cards_reference_bundled_font_url(self):
        for html in (
            ScheduleCard()._build_html(),
            CardPoolCard(pools=[])._build_html(),
            BirthdayCard()._build_html(),
        ):
            with self.subTest(card=html[:100]):
                self.assertIn("https://ba-assets.local/fonts/Blueaka.ttf", html)
                self.assertIn(
                    "https://ba-assets.local/fonts/ResourceHanRoundedCN.ttf", html
                )
                self.assertIn("'ResourceHanRounded'", html)
                self.assertIn("'BA Compact'", html)

    def test_cards_use_their_icons_and_shared_canvas(self):
        plugin_root = Path(__file__).resolve().parents[1]
        cards = (
            (ScheduleCard(), "collectible_icon_guidemission_s15_1.webp"),
            (CardPoolCard(pools=[]), "collectible_icon_guidemission01.webp"),
            (BirthdayCard(), "item_icon_favor_26.webp"),
        )

        for card, asset_name in cards:
            with self.subTest(card=type(card).__name__):
                rendered_html = card._build_html()
                icon_path = (
                    plugin_root
                    / "views"
                    / "ba-toolbox"
                    / "assets"
                    / "icons"
                    / asset_name
                )
                icon_data = base64.b64encode(icon_path.read_bytes()).decode("ascii")
                self.assertIn(f"data:image/webp;base64,{icon_data}", rendered_html)
                if type(card) is BirthdayCard:
                    self.assertIn('class="birthday-logo"', rendered_html)
                    self.assertIn("img/ui/birthday/logo.png", rendered_html)
                    self.assertIn('class="header-mark"', rendered_html)
                self.assertIn("border-radius: 20px", rendered_html)
                self.assertIn("Noto Sans CJK SC", rendered_html)
                self.assertIn("center/cover no-repeat; transform: scale(1.35)", rendered_html)

                with patch.object(
                    card._renderer, "render", return_value=b"png"
                ) as render:
                    card.render()
                render.assert_called_once_with(
                    rendered_html, card.CARD_W, card.CARD_H, framed=False
                )

    def test_pool_and_birthday_cards_render_student_avatars_when_available(self):
        with TemporaryDirectory() as temp_dir:
            avatar = Path(temp_dir) / "avatar.webp"
            avatar.write_bytes(b"avatar")
            icons = {"日富美": str(avatar), "梓": str(avatar)}
            pool = CardPoolCard(
                pools=[
                    {
                        "title": "卡池：日富美、梓",
                        "students": ["日富美", "梓"],
                    }
                ],
                student_icons=icons,
            )
            birthday = BirthdayCard(
                students=[("日富美", "11/27")],
                student_icons=icons,
                month=11,
            )

            pool_html = pool._build_html()
            birthday_html = birthday._build_html()
        self.assertEqual(pool_html.count('class="pool-avatar"'), 2)
        self.assertIn('class="birthday-avatar"', birthday_html)
        self.assertIn('src="data:image/webp;base64,', birthday_html)

    def test_birthday_card_height_fits_all_24_students_and_row_gaps(self):
        birthday = BirthdayCard(
            students=[(f"Student {index}", f"1/{index}") for index in range(1, 25)],
            month=1,
        )

        self.assertGreater(birthday.CARD_H, 400)
        self.assertIn(f"min-height: {birthday.CARD_H}px", birthday._build_html())
        self.assertIn('class="calendar-weekdays"', birthday._build_html())
        self.assertEqual(len(birthday.students), 24)

    def test_birthday_card_groups_current_month_students_by_calendar_day(self):
        card = BirthdayCard(
            students=[
                ("学生一", "1/2"),
                ("学生二", "1月2日"),
                ("学生三", "2/2"),
            ],
            month=1,
            year=2026,
        )
        rendered_html = card._build_html()

        self.assertEqual(card.students, [("学生一", "1/2"), ("学生二", "1月2日")])
        self.assertEqual(rendered_html.count('class="birthday-entry"'), 2)
        self.assertIn("可发送 “生日 月份” 查询对应月份日历", rendered_html)
        self.assertIn("width: 205px; height: auto", rendered_html)
        self.assertIn(".header-title-row { display: flex; align-items: flex-start;", rendered_html)
        self.assertIn("height: auto; margin-top: -3px", rendered_html)
        self.assertIn("margin-top: -6px; margin-left: 56px", rendered_html)
        self.assertIn("gap: 7px; max-width: calc(100% - 56px)", rendered_html)
        self.assertIn("font-size: 11px; font-style: italic; white-space: nowrap", rendered_html)
        self.assertIn('class="birthday-logo"', rendered_html)
        self.assertIn("img/ui/birthday/logo.png", rendered_html)
        self.assertNotIn("calendar-wordmark", rendered_html)
        self.assertIn('<div class="month-stamp"><strong>01</strong></div>', rendered_html)
        self.assertNotIn("位生日学生", rendered_html)
        self.assertIn('class="calendar-weekdays"', rendered_html)
        self.assertIn('<small>Sun</small>', rendered_html)
        self.assertIn('<small>Sat</small>', rendered_html)
        self.assertIn(".day-cell:not(.empty-day)::after", rendered_html)
        self.assertIn("column-gap: 7px; margin-bottom: 8px", rendered_html)
        self.assertIn('class="doodle-character"', rendered_html)
        self.assertIn('class="doodle-mascot"', rendered_html)
        self.assertIn('class="calendar-doodle-background"', rendered_html)
        self.assertIn(
            ".calendar-doodle-background { position: absolute; z-index: 0; top: 2px; "
            "right: 14px; width: 138px; height: 58px; object-fit: contain; }",
            rendered_html,
        )
        self.assertIn('class="doodle-party-left"', rendered_html)
        self.assertIn('class="doodle-party-right"', rendered_html)
        self.assertEqual(parse_birthday_date("11月27日"), (11, 27))
        self.assertIsNone(parse_birthday_date("13/27"))

    def test_birthday_calendar_marks_today_with_hand_drawn_red_circle(self):
        card = BirthdayCard(
            students=[("星野", "1/2")],
            month=1,
            year=2026,
            today=datetime(2026, 1, 2),
        )
        rendered_html = card._build_html()

        self.assertIn('class="day-cell has-birthday today"', rendered_html)
        self.assertIn('class="day-number today-ring">2</span>', rendered_html)
        self.assertIn(".today-ring::before", rendered_html)
        self.assertIn(".today-ring::after", rendered_html)
        self.assertIn("border: 2px solid #d6473f", rendered_html)
        self.assertIn("border-width: 1px; opacity: .7; transform: rotate(8deg)", rendered_html)
        self.assertIn("'ResourceHanRounded','BA Compact','Blueaka'", rendered_html)
        self.assertIn(".birthday-entry * { font-family: inherit !important; }", rendered_html)

    def test_pool_card_shows_chronological_calendar_timeline_without_numbering(self):
        names = [
            "星野（泳装）",
            "白子（泳装）",
            "野宫（泳装）",
            "日和（泳装）",
            "美咲（泳装）",
            "梓（泳装）",
            "伊吕波",
            "爱丽丝（礼服）",
            "黑馆晴奈",
            "千世",
            "空崎日奈（礼服）",
        ]
        card = CardPoolCard(
            pools=[
                {
                    "title": f"卡池：{'、'.join(names)}",
                    "students": names,
                    "start_at": 1762041600,
                    "end_at": 1762128000,
                    "pub_area": "国际服",
                },
                {
                    "title": "卡池：美游、时雨、冬",
                    "students": ["美游", "时雨", "冬"],
                    "start_at": 1761955200,
                    "end_at": 1762041600,
                    "pub_area": "国际服",
                },
                {
                    "title": "卡池：日奈",
                    "students": ["日奈"],
                    "start_at": 1761955200,
                    "end_at": 1762041600,
                    "pub_area": "国服",
                },
            ],
            region="all",
        )
        rendered_html = card._build_html()

        self.assertIn("grid-template-columns: repeat(3,minmax(0,1fr))", rendered_html)
        self.assertIn('<h2 class="server-heading">国服</h2>', rendered_html)
        self.assertIn('<h2 class="server-heading">国际服</h2>', rendered_html)
        self.assertEqual(rendered_html.count('class="pool-row"'), 3)
        self.assertNotIn("本期招募", rendered_html)
        self.assertNotIn("pool-index", rendered_html)
        self.assertLess(rendered_html.index("美游"), rendered_html.index("星野"))
        for name in names:
            self.assertIn(name, rendered_html)
        self.assertEqual(rendered_html.count("<time>"), 3)
        self.assertIn("11月01日", rendered_html)
        self.assertIn("11月03日", rendered_html)
        self.assertIn("招募档期", rendered_html)

    def test_calendar_date_captions_appear_above_date_ranges(self):
        schedule_html = ScheduleCard(
            events=[{"title": "活动", "pub_area": "国服"}]
        )._build_html()
        pool_html = CardPoolCard(
            pools=[{"title": "卡池", "pub_area": "国服"}]
        )._build_html()

        schedule_dates = schedule_html[
            schedule_html.index('<div class="schedule-dates">'):
            schedule_html.index("</div>", schedule_html.index('<div class="schedule-dates">'))
        ]
        pool_dates = pool_html[
            pool_html.index('<div class="pool-dates">'):
            pool_html.index("</div>", pool_html.index('<div class="pool-dates">'))
        ]

        self.assertLess(
            schedule_dates.index("活动档期"),
            schedule_dates.index("<time>"),
        )
        self.assertLess(pool_dates.index("招募档期"), pool_dates.index("<time>"))
        self.assertNotIn("本期招募", pool_html)

    def test_calendar_timeline_rows_share_one_server_card(self):
        schedule_html = ScheduleCard(
            events=[
                {"title": "活动一", "pub_area": "国服"},
                {"title": "活动二", "pub_area": "国服"},
            ]
        )._build_html()
        pool_html = CardPoolCard(
            pools=[
                {"title": "卡池一", "pub_area": "国服"},
                {"title": "卡池二", "pub_area": "国服"},
            ]
        )._build_html()

        self.assertIn(
            ".schedule-list { display: grid; overflow: hidden; border:",
            schedule_html,
        )
        self.assertIn(
            ".pool-list { display: grid; overflow: hidden; border:",
            pool_html,
        )
        self.assertEqual(schedule_html.count('<div class="schedule-list">'), 1)
        self.assertEqual(pool_html.count('<div class="pool-list">'), 1)
        self.assertIn(".schedule-timeline::before", schedule_html)
        self.assertIn(
            ".schedule-timeline::before { position: absolute; top: -10px; bottom: -10px; left: 50%; width: 2px; background: #b7dce8;",
            schedule_html,
        )
        self.assertIn(".schedule-row:first-child .schedule-timeline::before { top: 50%; }", schedule_html)
        self.assertIn(".schedule-row:last-child .schedule-timeline::before { bottom: 50%; }", schedule_html)
        self.assertIn(".pool-timeline::before", pool_html)
        self.assertIn(
            ".pool-timeline::before { position: absolute; top: -11px; bottom: -11px; left: 50%; width: 2px; background: #b7dce8;",
            pool_html,
        )
        self.assertIn(".pool-row:first-child .pool-timeline::before { top: 50%; }", pool_html)
        self.assertIn(".pool-row:last-child .pool-timeline::before { bottom: 50%; }", pool_html)
        self.assertNotIn(".schedule-row + .schedule-row", schedule_html)
        self.assertNotIn(".pool-row + .pool-row", pool_html)

    def test_calendar_timeline_single_item_has_no_line_beyond_its_node(self):
        schedule_html = ScheduleCard(
            events=[{"title": "仅一个活动", "pub_area": "国服"}]
        )._build_html()
        pool_html = CardPoolCard(
            pools=[{"title": "仅一个卡池", "pub_area": "国服"}]
        )._build_html()

        self.assertIn(
            ".schedule-row:first-child .schedule-timeline::before { top: 50%; }",
            schedule_html,
        )
        self.assertIn(
            ".schedule-row:last-child .schedule-timeline::before { bottom: 50%; }",
            schedule_html,
        )
        self.assertIn(
            ".pool-row:first-child .pool-timeline::before { top: 50%; }",
            pool_html,
        )
        self.assertIn(
            ".pool-row:last-child .pool-timeline::before { bottom: 50%; }",
            pool_html,
        )

    def test_schedule_card_height_accounts_for_wrapped_event_titles(self):
        title = "维护结束后将开放新的活动任务与特别奖励，记得领取" * 2
        card = ScheduleCard(
            events=[
                {"title": title, "pub_area": "国服"},
                {"title": title, "pub_area": "国际服"},
                {"title": title, "pub_area": "日服"},
            ]
        )

        self.assertGreaterEqual(card.CARD_H, 450)
        self.assertEqual(card._build_html().count(title), 3)

    def test_birthday_card_counts_long_names_when_estimating_wrapped_rows(self):
        students = [
            (f"日富美活动特别推荐版本学生名称测试{index}", "11/27")
            for index in range(12)
        ]
        birthday = BirthdayCard(students=students, month=11)

        self.assertGreater(birthday.CARD_H, 220)
        self.assertIn("日富美活动特别推荐版本学生名称测试11", birthday._build_html())


if __name__ == "__main__":
    unittest.main()
