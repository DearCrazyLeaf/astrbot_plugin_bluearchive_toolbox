from __future__ import annotations

import hashlib
import importlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PARENT = PLUGIN_ROOT.parent
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

card_fonts = importlib.import_module(
    "astrbot_plugin_bluearchive_toolbox.renderers.card_fonts"
)
main_module = importlib.import_module("astrbot_plugin_bluearchive_toolbox.main")

class _FontResponse:
    status = 200

    def __init__(self, content: bytes):
        self.content_length = len(content)
        self.content = self
        self._content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def iter_chunked(self, _chunk_size: int):
        yield self._content


class _FontSession:
    def __init__(self, content: bytes):
        self.content = content
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True

    def get(self, _url: str):
        return _FontResponse(self.content)


class CardFontTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_font_download_is_disabled_by_default(self):
        plugin = main_module.BAToolbox.__new__(main_module.BAToolbox)
        plugin.config = {}
        plugin._poll_task = Mock(done=Mock(return_value=False))
        with (
            patch.object(main_module, "prepare_card_fonts") as prepare,
            patch.object(main_module, "configure_font_cache_dir") as configure,
            patch.object(main_module.logger, "info"),
        ):
            await plugin.initialize()

        prepare.assert_not_awaited()
        configure.assert_called_once_with(None)

    async def test_full_font_download_runs_only_when_enabled(self):
        plugin = main_module.BAToolbox.__new__(main_module.BAToolbox)
        plugin.config = {"basic_settings": {"download_full_font": True}}
        plugin._poll_task = Mock(done=Mock(return_value=False))
        plugin._data_dir = Path("plugin-data")
        with (
            patch.object(
                main_module,
                "prepare_card_fonts",
                return_value=({}, {}),
            ) as prepare,
            patch.object(main_module.logger, "info"),
        ):
            await plugin.initialize()

        prepare.assert_awaited_once_with(Path("plugin-data") / "fonts")

    async def test_font_download_is_checksum_verified_and_cached(self):
        content = b"font test bytes"
        source = card_fonts.FontSource(
            filename="ResourceHanRoundedCN.ttf",
            url="https://fonts.example/font.ttf",
            sha256=hashlib.sha256(content).hexdigest(),
        )
        session = _FontSession(content)
        with TemporaryDirectory() as temp_dir:
            with (
                patch.object(card_fonts, "FONT_SOURCES", (source,)),
                patch.object(card_fonts.aiohttp, "ClientSession", return_value=session),
            ):
                fonts, errors = await card_fonts.prepare_card_fonts(temp_dir)

            self.assertEqual(errors, {})
            self.assertEqual(fonts[source.filename].read_bytes(), content)
            self.assertTrue(
                (Path(temp_dir) / card_fonts.FONT_LICENSE_FILE.name).is_file()
            )
            self.assertTrue(session.closed)

            with patch.object(card_fonts, "FONT_SOURCES", (source,)):
                cached_fonts, cache_errors = await card_fonts.prepare_card_fonts(
                    temp_dir
                )

        self.assertEqual(cache_errors, {})
        self.assertEqual(cached_fonts[source.filename], Path(temp_dir) / source.filename)

    async def test_checksum_mismatch_is_reported_without_installing_file(self):
        source = card_fonts.FontSource(
            filename="ResourceHanRoundedCN.ttf",
            url="https://fonts.example/font.ttf",
            sha256="0" * 64,
        )
        session = _FontSession(b"unexpected content")
        with TemporaryDirectory() as temp_dir:
            with (
                patch.object(card_fonts, "FONT_SOURCES", (source,)),
                patch.object(card_fonts.aiohttp, "ClientSession", return_value=session),
            ):
                fonts, errors = await card_fonts.prepare_card_fonts(temp_dir)

            self.assertEqual(fonts, {})
            self.assertIn("checksum", errors[source.filename])
            self.assertFalse((Path(temp_dir) / source.filename).exists())
            self.assertTrue(session.closed)
