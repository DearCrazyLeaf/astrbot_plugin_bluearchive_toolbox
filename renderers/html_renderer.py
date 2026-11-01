"""
BlueArchive HTML 卡片渲染器 — 使用 Playwright + Chromium 渲染 HTML 模板。

设计原则：
- 直接嵌入官方 PNG 贴图作为按钮、图标背景
- CSS 实现斜切按钮、标签页、进度条等官方 UI 效果
- flex/grid 布局，复刻度更高
- 无需 PIL 手动绘制形状
- 使用 ba-asset:// 协议加载本地资源，避免 base64 编码大文件（如 15MB 字体）
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

# BA 资源根目录（本插件官方 UI 贴图）
BA_ASSETS = Path(__file__).resolve().parent.parent / "assets"

# 字体文件
FONT_BLUAKA = BA_ASSETS / "fonts" / "Blueaka.ttf"
FONT_BLUAKA_URI = "https://ba-assets.local/fonts/Blueaka.ttf"
FONT_RESOURCE_HAN_URI = "https://ba-assets.local/fonts/ResourceHanRoundedCN.ttf"

_FONT_CACHE_DIR: Path | None = None


def configure_font_cache_dir(directory: str | Path | None) -> None:
    global _FONT_CACHE_DIR
    _FONT_CACHE_DIR = Path(directory) if directory is not None else None


def is_chromium_installed() -> bool:
    with sync_playwright() as playwright:
        return Path(playwright.chromium.executable_path).is_file()


# 官方 UI 贴图映射（相对路径，用于 ba-asset:// 引用）
BTN_ASSET_MAP = {
    "blue": "img/ui/buttons/Common_Btn_Normal_B_C_Pt.png",
    "blue_sel": "img/ui/buttons/Common_Btn_Normal_B_S_Pt.png",
    "pink": "img/ui/buttons/Common_Btn_Normal_P_S_Pt.png",
    "red": "img/ui/buttons/Common_Btn_Normal_R_S_Pt.png",
    "yellow": "img/ui/buttons/Common_Btn_Normal_Y_S_Pt.png",
}

RESOURCE_ICON_MAP = {
    "gem": "img/icons/Common_Icon_Diamond.png",
    "gold": "img/icons/Common_Icon_Gold_Base.png",
    "stamina": "img/icons/Common_Icon_Stamina.png",
}

ROLE_ICON_MAP = {
    "STRIKER": "img/ui/striker.webp",
    "SPECIAL": "img/ui/special.webp",
    "TANKER": "img/ui/cards/Common_Icon_Tanker.png",
    "HEALER": "img/ui/cards/Common_Icon_Healer.png",
    "SUPPORTER": "img/ui/cards/Common_Icon_Supporter.png",
}

STAR_ON = "img/ui/Common_Icon_Formation_Star.png"
STAR_OFF = "img/ui/Common_Icon_Formation_Star_Disable.png"
STAR_WEAPON = "img/ui/Common_Icon_Formation_Star_2.png"
CHECK_ON = "img/ui/Common_Icon_Check_Y_On.png"
CHECK_OFF = "img/ui/Common_Icon_Check_Y_Off.png"
POPUP_BG = "img/ui/Common_Popup_Bg.png"
PROGRESS_BG = "img/ui/Common_Sound_SlideBg.png"
CARD_BG_IMG = "img/ui/cards/Card_Bg.png"
CARD_LINE_CHAR = "img/ui/cards/Card_Line_Char.png"
CARD_SELECT_LINE = "img/ui/cards/Card_Select_Line.png"

# 颜色方案（官方风格）
TEXT_MAIN = "#1e1e2d"
TEXT_SUB = "#666682"
TEXT_WHITE = "#ffffff"
ACCENT = "#00a8fc"
WHITE_CARD = "#ffffff"
WHITE_MASK_ALPHA = 200

CARD_W = 640
PADDING = 24
BLOCK_GAP = 16
INNER_PADDING = 20


def _asset_uri(rel_path: str) -> str:
    """将资源相对路径转换为 ba-asset:// URI。

    浏览器通过 page.route 拦截 ba-asset:// 请求，从本地文件系统读取。
    避免大文件（如 15MB 字体）base64 编码导致 HTML 过大。
    """
    return f"ba-asset://{rel_path}"


def _font_face_css() -> str:
    """生成字体 CSS，使用插件资源路由加载本地字体。"""
    return f"""
    @font-face {{
        font-family: 'ResourceHanRounded';
        src: url('{FONT_RESOURCE_HAN_URI}') format('truetype');
        font-weight: 400;
        font-style: normal;
    }}
    @font-face {{
        font-family: 'Blueaka';
        src: url('{FONT_BLUAKA_URI}') format('truetype');
        font-weight: normal;
        font-style: normal;
    }}
    """


FONT_CSS = _font_face_css()


class HTMLCardRenderer:
    """基于 Playwright 的 HTML 卡片渲染器。"""

    def _render_html(self, html_content: str, width: int, height: int | None) -> bytes:
        """使用 Playwright 渲染 HTML 并返回 PNG 字节。"""
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_viewport_size({"width": width, "height": height or 800})

                # 拦截 ba-asset:// 请求，从本地文件系统读取
                def handle_route(route):
                    url = route.request.url
                    prefixes = ("ba-asset://", "https://ba-assets.local/")
                    prefix = next(
                        (
                            candidate
                            for candidate in prefixes
                            if url.startswith(candidate)
                        ),
                        None,
                    )
                    if prefix:
                        rel_path = url[len(prefix) :].split("?")[0]

                        file_path = BA_ASSETS / rel_path
                        if rel_path.startswith("fonts/") and _FONT_CACHE_DIR:
                            cached_font = _FONT_CACHE_DIR / rel_path.removeprefix(
                                "fonts/"
                            )
                            if cached_font.is_file():
                                file_path = cached_font
                        if not file_path.exists():
                            route.abort()
                            return

                        data = file_path.read_bytes()
                        ext = file_path.suffix.lower()
                        mime_map = {
                            ".png": "image/png",
                            ".jpg": "image/jpeg",
                            ".jpeg": "image/jpeg",
                            ".webp": "image/webp",
                            ".gif": "image/gif",
                            ".ttf": "font/ttf",
                            ".otf": "font/otf",
                            ".woff": "font/woff",
                            ".woff2": "font/woff2",
                        }
                        route.fulfill(
                            body=data,
                            content_type=mime_map.get(ext, "application/octet-stream"),
                            headers={"Access-Control-Allow-Origin": "*"},
                        )
                        return
                    route.continue_()

                page.route("**/*", handle_route)
                page.set_content(html_content, wait_until="domcontentloaded")
                page.evaluate("() => document.fonts.ready.then(() => true)")
                return page.screenshot(full_page=height is None, type="png")
            finally:
                browser.close()

    def _wrap_bg(self, inner_html: str, width: int, height: int) -> str:
        """包裹卡片内容并使用固定浅色背景。"""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                {_font_face_css()}
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                html, body {{
                    width: {width}px;
                    height: {height}px;
                    background: #fafafa;
                }}
                .card-wrap {{
                    width: {width}px;
                    height: {height}px;
                    position: relative;
                    background: rgba(255, 255, 255, 0.8);
                }}
                .inner-card {{
                    position: absolute;
                    top: {PADDING}px;
                    left: {PADDING}px;
                    width: {width - PADDING * 2}px;
                    height: {height - PADDING * 2}px;
                    background: #fff;
                    border-radius: 14px;
                    border: 3px solid #ddd;
                    overflow: hidden;
                }}
            </style>
        </head>
        <body>
            <div class="card-wrap">
                <div class="inner-card">
                    {inner_html}
                </div>
            </div>
        </body>
        </html>
        """

    def render(self, inner_html: str, width: int, height: int) -> bytes:
        """渲染完整卡片并返回 PNG 字节。"""
        html = self._wrap_bg(inner_html, width, height)
        return self._render_html(html, width, height)
