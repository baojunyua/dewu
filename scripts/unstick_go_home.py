"""Unstick from 货值计算 and verify go_home."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import settings  # noqa: E402
from backend.dewu_bot import DewuBot  # noqa: E402


def main() -> None:
    adb = Path(settings.adb_path)
    if adb.exists():
        os.environ["PATH"] = str(adb.parent) + os.pathsep + os.environ.get("PATH", "")
    bot = DewuBot()
    bot.connect()
    bot._open_dewu()

    def p(stage: str, msg: str) -> None:
        try:
            print(f"[{stage}] {msg}", flush=True)
        except Exception:
            print(f"[{stage}]", flush=True)

    print("huozhi", bot._is_huozhi_overlay())
    print("seller", bot._is_seller_me_page())
    bot._go_home(p)
    xml = bot._page_text()
    print("after_huozhi", bot._is_huozhi_overlay(xml))
    print("after_seller", bot._is_seller_me_page(xml))
    print("has_feed", any(k in xml for k in ("推荐", "关注", "灵感")))
    bot.d.screenshot(str(settings.screenshot_dir / "_after_go_home.png"))
    print("done")


if __name__ == "__main__":
    main()
