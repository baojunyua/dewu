"""Dump current page or re-open detail and test sales parser."""
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


def dump(bot: DewuBot, tag: str) -> dict:
    xml = bot._page_text()
    (OUT / f"_{tag}.xml").write_text(xml, encoding="utf-8")
    bot.d.screenshot(str(OUT / f"_{tag}.png"))
    texts = [t for t in re.findall(r'text="([^"]*)"', xml) if t.strip()]
    descs = [t for t in re.findall(r'content-desc="([^"]*)"', xml) if t.strip()]
    parsed = bot._extract_merchant_size_sales(xml)
    keys = ["商品详情", "规格行情", "7日销量", "有原装", "现货价", "规格销量", "立即出价"]
    lines = [f"{k}: {k in xml}" for k in keys]
    lines.append(f"text_n={len(texts)} desc_n={len(descs)}")
    lines.append("TEXTS|" + " | ".join(texts[:200]))
    lines.append("DESCS|" + " | ".join(descs[:80]))
    lines.append(f"parsed_n={len(parsed)}")
    lines.append(str(parsed))
    (OUT / f"_{tag}.txt").write_text("\n".join(lines), encoding="utf-8")
    return parsed


def main() -> None:
    adb = Path(settings.adb_path)
    if adb.exists():
        os.environ["PATH"] = str(adb.parent) + os.pathsep + os.environ.get("PATH", "")
    bot = DewuBot()
    bot.connect()
    bot._open_dewu()
    xml = bot._page_text()
    if "商品详情" in xml and "规格行情" in xml:
        parsed = dump(bot, "cur_detail")
        print("already on detail", len(parsed))
        return

    def p(s, m):
        print(s)

    bot._ensure_seller_mode(p)
    bot._open_huozhi(p)
    if not bot._search_huozhi_sku(SKU, p):
        print("search fail")
        dump(bot, "search_fail2")
        return
    if not bot._open_first_huozhi_result(p):
        print("open fail")
        dump(bot, "open_fail2")
        return
    time.sleep(0.5)
    parsed = dump(bot, "cur_detail")
    print("parsed", len(parsed))
    # scroll once more and dump
    w, h = bot.d.window_size()
    bot.d.swipe(int(w * 0.72), int(h * 0.75), int(w * 0.72), int(h * 0.42), 0.28)
    time.sleep(0.5)
    parsed2 = dump(bot, "cur_detail2")
    print("parsed2", len(parsed2))


if __name__ == "__main__":
    main()
