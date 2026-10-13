"""Open merchant detail and dump size-sales texts for parser fix."""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import settings  # noqa: E402
from backend.dewu_bot import DewuBot  # noqa: E402

OUT = settings.screenshot_dir
SKU = (sys.argv[1] if len(sys.argv) > 1 else "553558-040").strip()


def main() -> None:
    adb = Path(settings.adb_path)
    if adb.exists():
        os.environ["PATH"] = str(adb.parent) + os.pathsep + os.environ.get("PATH", "")
    bot = DewuBot()
    bot.connect()

    def p(s, m):
        try:
            print(s, m)
        except Exception:
            print(s)

    bot._open_dewu()
    bot._ensure_seller_mode(p)
    bot._open_huozhi(p)
    assert bot._search_huozhi_sku(SKU, p)
    assert bot._open_first_huozhi_result(p)
    # ensure tab
    bot.d(text="规格行情").click_exists(timeout=0.8)
    time.sleep(0.4)
    bot.d(text="7日").click_exists(timeout=0.5)
    time.sleep(0.3)
    # scroll a bit to size section
    w, h = bot.d.window_size()
    bot.d.swipe(int(w * 0.72), int(h * 0.75), int(w * 0.72), int(h * 0.40), 0.3)
    time.sleep(0.5)
    xml = bot._page_text()
    (OUT / "_merchant_detail.xml").write_text(xml, encoding="utf-8")
    bot.d.screenshot(str(OUT / "_merchant_detail.png"))
    texts = [t for t in re.findall(r'text="([^"]*)"', xml) if t.strip()]
    lines = ["TEXTS|" + " | ".join(texts)]
    parsed = bot._extract_merchant_size_sales(xml)
    lines.append(f"parsed={parsed}")
    (OUT / "_merchant_detail.txt").write_text("\n".join(lines), encoding="utf-8")
    print("texts", len(texts), "parsed", len(parsed))


if __name__ == "__main__":
    main()
