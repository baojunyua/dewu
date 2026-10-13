"""Dump Me tab texts for merchant switch debugging."""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import settings  # noqa: E402
import uiautomator2 as u2  # noqa: E402

OUT = settings.screenshot_dir


def main() -> None:
    adb = Path(settings.adb_path)
    if adb.exists():
        os.environ["PATH"] = str(adb.parent) + os.pathsep + os.environ.get("PATH", "")
    d = u2.connect(settings.device_serial)
    d(text="我").click_exists(timeout=2)
    time.sleep(1.2)
    xml = d.dump_hierarchy()
    (OUT / "_me_page.xml").write_text(xml, encoding="utf-8")
    d.screenshot(str(OUT / "_me_page.png"))
    texts = [t for t in re.findall(r'text="([^"]*)"', xml) if t.strip()]
    keys = [
        "切换商家版",
        "切换买家版",
        "货值计算",
        "我要出售",
        "出售",
        "商家",
        "买家",
        "待发货",
    ]
    lines = [f"app={d.app_current()}", f"texts={len(texts)}"]
    for k in keys:
        lines.append(f"has {k}: {k in xml}")
    lines.append("TEXT|" + " | ".join(texts[:120]))
    (OUT / "_me_page.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:20]))
    print("wrote", OUT / "_me_page.txt")


if __name__ == "__main__":
    main()
