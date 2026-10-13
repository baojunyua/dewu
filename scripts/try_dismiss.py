"""Try dismiss camera dialog then pick first album thumb."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.dewu_bot import bot  # noqa: E402


def prog(stage: str, msg: str) -> None:
    Path("screenshots/_dismiss_log.txt").write_text(
        Path("screenshots/_dismiss_log.txt").read_text(encoding="utf-8")
        + f"[{stage}] {msg}\n"
        if Path("screenshots/_dismiss_log.txt").exists()
        else f"[{stage}] {msg}\n",
        encoding="utf-8",
    )


def main() -> None:
    Path("screenshots/_dismiss_log.txt").write_text("", encoding="utf-8")
    bot.connect()
    bot._grant_camera_permission()
    bot._dismiss_camera_request_dialog(prog)
    time.sleep(0.5)
    d = bot.d
    act = d.app_current()
    Path("screenshots/_dismiss_log.txt").write_text(
        Path("screenshots/_dismiss_log.txt").read_text(encoding="utf-8")
        + f"act={act}\n",
        encoding="utf-8",
    )
    if "GraphSearch" in (act.get("activity") or ""):
        ok = bot._pick_album_image_for_search(prog)
        Path("screenshots/_dismiss_log.txt").write_text(
            Path("screenshots/_dismiss_log.txt").read_text(encoding="utf-8")
            + f"pick_ok={ok} after={d.app_current()}\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
