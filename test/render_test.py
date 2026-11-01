import sys
import asyncio
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_clients.arona_client import AronaClient
from renderers.card import BirthdayCard, CardPoolCard, ScheduleCard
from renderers.student_detail import StudentDetailCard
from renderers.html_renderer import BA_ASSETS
from api_clients.schaledb_client import SchaleDBClient, SchaleDBError
from api_clients.gamekee import GameKeeClient
from renderers.card_fonts import prepare_card_fonts

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

TEST_STUDENT_NAME = "hifumi_racing"
SWIMSUIT_STUDENT_NAME = "hifumi_swimsuit"

def clean_output():
    """Create the output directory and replace only files produced by this test."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in (
        "student_card.png",
        "schedule_card.png",
        "pool_card.png",
        "birthday_card.png",
        "student_detail_card.png",
        "student_swimsuit_detail_card.png",
        "guide_hifumi.png",
    ):
        output = OUTPUT_DIR / name
        if output.is_file():
            output.unlink()


def save_png(name: str, data: bytes) -> str:
    out = OUTPUT_DIR / name
    out.write_bytes(data)
    return str(out)


async def render_in_thread(card):
    """在独立线程中渲染卡片（Playwright Sync API 不能在 asyncio 循环中直接使用）。"""
    return await asyncio.to_thread(card.render)


async def download_guide_preview() -> Path:
    arona = AronaClient()
    try:
        image_url = await arona.get_guide_image("日富美")
        if arona.last_error:
            raise RuntimeError(f"Arona guide lookup failed: {arona.last_error}")
        if not image_url:
            raise RuntimeError("Arona did not return a guide image for 日富美.")

        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"Arona guide image download failed: HTTP {response.status}"
                    )
                content_type = response.headers.get("Content-Type", "")
                if not content_type.lower().startswith("image/"):
                    raise RuntimeError(
                        f"Arona returned non-image content: {content_type or 'unknown'}"
                    )
                image_data = await response.read()
                if not image_data:
                    raise RuntimeError("Arona returned an empty guide image.")
    finally:
        await arona.close()

    return Path(save_png("guide_hifumi.png", image_data))


async def main_async():
    clean_output()
    fonts, font_errors = await prepare_card_fonts(OUTPUT_DIR.parent / ".font_cache")
    print(f"[CardFonts] ready={list(fonts)}; errors={font_errors}")

    schaledb = SchaleDBClient(OUTPUT_DIR / ".schaledb_cache")
    try:
        student_data = await schaledb.search_student(TEST_STUDENT_NAME)
        student_data = await schaledb.prepare_student(student_data)
        swimsuit_data = await schaledb.search_student(SWIMSUIT_STUDENT_NAME)
        swimsuit_data = await schaledb.prepare_student(swimsuit_data)
        birthday_records = await schaledb.get_birthdays_with_ids("cn")
    except SchaleDBError as exc:
        raise RuntimeError(f"SchaleDB test lookup failed: {exc}") from exc
    finally:
        await schaledb.close()

    gamekee = GameKeeClient()
    try:
        schedule_events = await gamekee.get_activity_calendar("jp")
        if gamekee.last_error:
            raise RuntimeError(f"GameKee schedule lookup failed: {gamekee.last_error}")
        card_pools = await gamekee.get_card_pools("jp")
        if gamekee.last_error:
            raise RuntimeError(f"GameKee card pool lookup failed: {gamekee.last_error}")
    finally:
        await gamekee.close()
    pool_student_names = list(
        dict.fromkeys(
            name
            for pool in card_pools
            for name in pool.get("students", [])
            if isinstance(name, str) and name
        )
    )
    pool_student_icons = await schaledb.get_student_icon_map(pool_student_names, "cn")
    birthday_students = [
        (name, birthday) for name, birthday, _ in birthday_records
    ]
    birthday_icons_by_id = await schaledb.get_student_icons_by_id(
        [
            student_id
            for _, _, student_id in birthday_records[:24]
            if student_id is not None
        ]
    )
    birthday_student_icons = {
        name: birthday_icons_by_id[str(student_id)]
        for name, _, student_id in birthday_records[:24]
        if student_id is not None and str(student_id) in birthday_icons_by_id
    }
    await schaledb.close()
    source = student_data.get("_data_source_language", "SchaleDB")
    print(
        f"[SchaleDB] {student_data.get('Name')} (id={student_data.get('Id')}); "
        f"data={source}, attack={student_data.get('BulletType')}, "
        f"armor={student_data.get('ArmorType')}; "
        f"assets={len(student_data.get('_render_assets', {}))}; "
        f"asset errors={len(schaledb.asset_errors)}"
    )
    if schaledb.asset_errors:
        print(f"[SchaleDB] Missing assets: {', '.join(schaledb.asset_errors)}")

    # 1. 排期卡片
    card = ScheduleCard(
        title="活动日程",
        events=schedule_events,
        region="jp",
    )
    png = await render_in_thread(card)
    print(
        f"[ScheduleCard] {save_png('schedule_card.png', png)} ({len(png)} bytes); "
        f"events={len(schedule_events)}"
    )

    card = CardPoolCard(
        pools=card_pools,
        region="jp",
        student_icons=pool_student_icons,
    )
    png = await render_in_thread(card)
    print(
        f"[CardPoolCard] {save_png('pool_card.png', png)} ({len(png)} bytes); "
        f"pools={len(card_pools)}"
    )

    # 2. 生日卡片
    card = BirthdayCard(
        students=birthday_students[:24],
        student_icons=birthday_student_icons,
    )
    png = await render_in_thread(card)
    print(f"[BirthdayCard] {save_png('birthday_card.png', png)} ({len(png)} bytes)")

    # 3. 学生详情卡片（使用真实/预制数据）
    card = StudentDetailCard(student_data=student_data)
    png = await render_in_thread(card)
    print(f"[StudentDetailCard] {save_png('student_detail_card.png', png)} ({len(png)} bytes) [{source}]")

    card = StudentDetailCard(student_data=swimsuit_data)
    png = await render_in_thread(card)
    print(
        "[SwimsuitStudentDetailCard] "
        f"{save_png('student_swimsuit_detail_card.png', png)} ({len(png)} bytes)"
    )

    guide_path = await download_guide_preview()
    print(f"[GuidePreview] {guide_path} ({guide_path.stat().st_size} bytes)")

    print(f"\nBA_ASSETS: {BA_ASSETS} (exists={BA_ASSETS.exists()})")
    print("All renders completed.")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
