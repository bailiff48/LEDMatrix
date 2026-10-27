"""
Shows Wi-Fi setup messages on the LED panel  (Oct 2026)

src/wifi_setup_service.py writes config/wifi_status.json:
  {"pages": [[title, line, line, line], ...], "timestamp": t, "ttl": s, "color": "green"?}
While that file is fresh, the display controller calls show() each loop
instead of its normal rotation. Pages rotate every PAGE_SECONDS.
"""
import json
import logging
import time
from pathlib import Path

from PIL import ImageFont

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent
STATUS_FILE = PROJECT_DIR / "config" / "wifi_status.json"
PAGE_SECONDS = 5
TITLE_COLORS = {"green": (0, 220, 90), None: (255, 200, 0)}
BODY_COLOR = (255, 255, 255)

_font_cache = {}


def _font(size=8):
    if size not in _font_cache:
        try:
            _font_cache[size] = ImageFont.truetype(str(PROJECT_DIR / "assets/fonts/5by7.regular.ttf"), size)
        except Exception:
            _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


def read_status(path=STATUS_FILE, now=None):
    """The status dict if the file exists and is fresh, else None."""
    now = time.time() if now is None else now
    try:
        data = json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return None
    except Exception as e:
        logger.debug("wifi status unreadable: %s", e)
        return None
    pages = data.get("pages")
    if not pages or now - float(data.get("timestamp", 0)) > float(data.get("ttl", 30)):
        return None
    return data


def draw_status(draw, width, height, status, now=None):
    """Draw the current page onto a PIL ImageDraw (black background)."""
    now = time.time() if now is None else now
    pages = status["pages"]
    page = pages[int(now // PAGE_SECONDS) % len(pages)]
    draw.rectangle([0, 0, width - 1, height - 1], fill=(0, 0, 0))
    old_mode = draw.fontmode
    draw.fontmode = "1"                      # crisp pixels, no anti-aliasing
    font = _font(10)                         # 5by7 renders pixel-exact at 10
    line_h = max(8, height // 4)
    title_color = TITLE_COLORS.get(status.get("color"), TITLE_COLORS[None])
    for i, text in enumerate(page[: height // line_h]):
        text = str(text)
        while text and draw.textlength(text, font=font) > width:
            text = text[:-1]
        w = draw.textlength(text, font=font)
        x = max(0, int((width - w) // 2)) if i == 0 else 1
        draw.text((x, i * line_h), text, font=font, fill=title_color if i == 0 else BODY_COLOR)
    draw.fontmode = old_mode
    if len(pages) > 1:                      # page dots, bottom-right
        for j in range(len(pages)):
            on = j == int(now // PAGE_SECONDS) % len(pages)
            draw.point((width - 2 - 3 * (len(pages) - 1 - j), height - 1),
                       fill=(255, 200, 0) if on else (70, 70, 70))


def show(display_manager, path=STATUS_FILE):
    """Draw the setup message if one is active. Returns True if it did."""
    status = read_status(path)
    if not status:
        return False
    try:
        dm = display_manager
        draw_status(dm.draw, dm.image.width, dm.image.height, status)
        dm.update_display()
        return True
    except Exception as e:
        logger.warning("Could not draw Wi-Fi status: %s", e)
        return False
