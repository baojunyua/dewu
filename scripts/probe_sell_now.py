"""Dump current page / try open sell sheet for debugging empty size_prices."""
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
OUT.mkdir(parents=True, exist_ok=True)


def summarize(xml: str, label: str) -> list[str]:
    lines = [f"=== {label} ==="]
    keys = (
        "预计收入",
        "最低出售价",
        "去出价",
        "是这款",
        "请确认实物",
        "易发错货",
        "出售",
        "立即购买",
        "skusView",
        "主货号",
        "553558",
        "尺码",
    )
    for k in keys:
        lines.append(f"has {k}: {k in xml}")
    texts = [t.strip() for t in re.findall(r'text="([^"]*)"', xml) if t.strip()]
    lines.append(f"text_count={len(texts)}")
    lines.append("texts: " + " | ".join(texts[:80]))
    # price-like
    prices = [t for t in texts if "¥" in t or "￥" in t or t in ("--", "—")]
    lines.append("price_texts: " + " | ".join(prices[:40]))
    sizes = [t for t in texts if re.fullmatch(r"\d{2,3}(?:\.\d)?", t)]
    lines.append("size_like: " + " | ".join(sizes[:40]))
    return lines


def main() -> None:
    adb = Path(settings.adb_path)
    if adb.exists():
        os.environ["PATH"] = str(adb.parent) + os.pathsep + os.environ.get("PATH", "")

    bot = DewuBot()
    bot.connect()
    d = bot.d
    lines: list[str] = []
    lines.append(f"window={d.window_size()}")
    lines.append(f"app={d.app_current()}")

    xml = d.dump_hierarchy()
    (OUT / "_probe_before.xml").write_text(xml, encoding="utf-8")
    d.screenshot(str(OUT / "_probe_before.png"))
    lines.extend(summarize(xml, "before"))

    # If already on detail, try sell flow
    opened = bot._click_sell_entry()
    lines.append(f"click_sell_entry={opened}")
    time.sleep(0.5)
    xml2 = d.dump_hierarchy()
    (OUT / "_probe_after_sell.xml").write_text(xml2, encoding="utf-8")
    d.screenshot(str(OUT / "_probe_after_sell.png"))
    lines.extend(summarize(xml2, "after_sell"))

    if "是这款" in xml2 or "请确认实物" in xml2:
        ok = bot._confirm_sku_match_dialog()
        lines.append(f"confirm_again={ok}")
        time.sleep(1.2)
        xml2 = d.dump_hierarchy()
        (OUT / "_probe_after_confirm.xml").write_text(xml2, encoding="utf-8")
        d.screenshot(str(OUT / "_probe_after_confirm.png"))
        lines.extend(summarize(xml2, "after_confirm"))

    income = bot._click_estimated_income()
    lines.append(f"click_income={income}")
    time.sleep(1.0)
    xml3 = d.dump_hierarchy()
    (OUT / "_probe_income.xml").write_text(xml3, encoding="utf-8")
    d.screenshot(str(OUT / "_probe_income.png"))
    lines.extend(summarize(xml3, "income"))

    rows = bot._extract_size_prices(xml3)
    lines.append(f"extract_count={len(rows)}")
    lines.append(f"extract_sample={rows[:15]}")
    rows2, _shot = bot._collect_size_prices_with_scroll(stem="_probe_sell")
    lines.append(f"scroll_collect={len(rows2)}")
    lines.append(f"scroll_sample={rows2[:15]}")

    (OUT / "_probe_sell_now.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
