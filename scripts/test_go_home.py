"""Test _go_home from current album / graph search page."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.dewu_bot import bot  # noqa: E402


def prog(stage: str, msg: str) -> None:
    line = f"[{stage}] {msg}\n"
    p = Path("screenshots/_gohome_log.txt")
    p.write_text(p.read_text(encoding="utf-8") + line if p.exists() else line, encoding="utf-8")


def main() -> None:
    Path("screenshots/_gohome_log.txt").write_text("", encoding="utf-8")
    bot.connect()
    before = bot.d.app_current()
    Path("screenshots/_gohome_log.txt").write_text(f"before={before}\n", encoding="utf-8")
    bot._go_home(prog)
    after = bot.d.app_current()
    p = Path("screenshots/_gohome_log.txt")
    p.write_text(p.read_text(encoding="utf-8") + f"after={after}\n", encoding="utf-8")
    xml = bot._page_text()
    homeish = ("推荐" in xml or "关注" in xml) and "拍照搜索" not in xml and "我的相册" not in xml
    p.write_text(p.read_text(encoding="utf-8") + f"homeish={homeish}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
