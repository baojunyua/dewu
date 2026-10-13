"""Stop after huozhi search and dump page."""
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
        print(s, m.encode("gbk", "ignore").decode("gbk"), flush=True)

    bot._open_dewu()
    bot._ensure_seller_mode(p)
    bot._open_huozhi(p)
    ok = bot._search_huozhi_sku(SKU, p)
    print("search_ok", ok)
    time.sleep(1)
    xml = bot._page_text()
    (OUT / "_after_huozhi_search.xml").write_text(xml, encoding="utf-8")
    bot.d.screenshot(str(OUT / "_after_huozhi_search.png"))
    texts = [t for t in re.findall(r'text="([^"]*)"', xml) if t.strip()]
    keys = ["规格行情", "7日销量", "货值计算", "553558", "搜索", "缺货", "库存"]
    lines = [f"search_ok={ok}"]
    for k in keys:
        lines.append(f"has {k}: {k in xml}")
    lines.append("TEXTS|" + " | ".join(texts[:100]))
    # resource ids with search/iv
    ids = sorted(set(re.findall(r'resource-id="([^"]+)"', xml)))
    lines.append("IDS|" + " | ".join(i for i in ids if i)[:80])
    (OUT / "_after_huozhi_search.txt").write_text("\n".join(lines), encoding="utf-8")
    print("wrote dump")


if __name__ == "__main__":
    main()
