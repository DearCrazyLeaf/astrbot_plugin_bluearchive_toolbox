from __future__ import annotations

import asyncio
import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from .html_renderer import configure_font_cache_dir


@dataclass(frozen=True)
class FontSource:
    filename: str
    url: str
    sha256: str


FONT_SOURCES = (
    FontSource(
        filename="ResourceHanRoundedCN.ttf",
        url=(
            "https://raw.githubusercontent.com/RedBeanN/BlueArchive/master/"
            "assets/fonts/ResourceHanRoundedCN.ttf"
        ),
        sha256="b2ec1727fbc04b70255db6cb71be0eca3b7a7167ef11655059c5576cc920143f",
    ),
)
MAX_FONT_BYTES = 20 * 1024 * 1024
FONT_LICENSE_FILE = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "fonts"
    / "RESOURCE_HAN_ROUNDED_OFL.txt"
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as font_file:
        for chunk in iter(lambda: font_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _ensure_font(
    session: aiohttp.ClientSession,
    directory: Path,
    source: FontSource,
) -> Path:
    font_path = directory / source.filename
    if font_path.is_file():
        if await asyncio.to_thread(_file_sha256, font_path) == source.sha256:
            return font_path
        font_path.unlink()

    async with session.get(source.url) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        if (
            response.content_length is not None
            and response.content_length > MAX_FONT_BYTES
        ):
            raise RuntimeError(
                f"font exceeds the {MAX_FONT_BYTES // (1024 * 1024)} MiB download limit"
            )
        content = bytearray()
        digest = hashlib.sha256()
        async for chunk in response.content.iter_chunked(256 * 1024):
            content.extend(chunk)
            digest.update(chunk)
            if len(content) > MAX_FONT_BYTES:
                raise RuntimeError(
                    f"font exceeds the {MAX_FONT_BYTES // (1024 * 1024)} MiB download limit"
                )

    if digest.hexdigest() != source.sha256:
        raise RuntimeError("downloaded font checksum does not match the pinned file")

    font_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = font_path.with_name(f".{font_path.name}.download")
    try:
        await asyncio.to_thread(temporary_path.write_bytes, content)
        await asyncio.to_thread(temporary_path.replace, font_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return font_path


async def prepare_card_fonts(
    font_cache_dir: str | Path,
) -> tuple[dict[str, Path], dict[str, str]]:
    directory = Path(font_cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    configure_font_cache_dir(directory)

    try:
        await asyncio.to_thread(
            shutil.copyfile,
            FONT_LICENSE_FILE,
            directory / FONT_LICENSE_FILE.name,
        )
    except OSError as error:
        return {}, {FONT_LICENSE_FILE.name: f"cannot install font license: {error}"}

    timeout = aiohttp.ClientTimeout(total=120, connect=15, sock_read=45)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        results = await asyncio.gather(
            *(_ensure_font(session, directory, source) for source in FONT_SOURCES),
            return_exceptions=True,
        )

    fonts: dict[str, Path] = {}
    errors: dict[str, str] = {}
    for source, result in zip(FONT_SOURCES, results):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, Exception):
            errors[source.filename] = str(result)
        else:
            fonts[source.filename] = result
    return fonts, errors
