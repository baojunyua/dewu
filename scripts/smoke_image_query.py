"""Smoke test automated image query (partial: open+pick+result+detail)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.dewu_bot import bot  # noqa: E402


def prog(stage: str, msg: str) -> None:
    print(f"[{stage}] {msg}", flush=True)


def main() -> None:
    img = ROOT / "screenshots" / "_probe_home.png"
    if not img.exists():
        raise SystemExit("no test image")
    result = bot.query_by_image(img, progress=prog)
    print("OK=", result.ok)
    print("msg=", result.message)
    print("sell=", result.sell_price)
    print("sizes=", len(result.size_prices or []))
    print("elapsed=", result.elapsed_ms)


if __name__ == "__main__":
    main()
