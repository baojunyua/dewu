"""Probe merchant 货值计算 → 7日销量 path."""
from __future__ import annotations

import os
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
    lines: list[str] = []

    def progress(stage: str, msg: str) -> None:
        lines.append(f"[{stage}] {msg}")
        try:
            print(f"[{stage}] {msg}", flush=True)
        except UnicodeEncodeError:
            print(f"[{stage}] ok", flush=True)

    bot._open_dewu()
    time.sleep(1.0)
    bot._go_home(progress)

    shot, sales, trades = bot._capture_merchant_sales(SKU, progress, stem="probe_m")
    lines.append(f"shot={shot}")
    lines.append(f"sizes={len(sales)}")
    for size, row in sorted(sales.items(), key=lambda x: x[0]):
        lines.append(f"  {size}: 7d={row.get('sales_7d')}")
    lines.append(f"trades={len(trades)}")
    (OUT / "_probe_merchant.txt").write_text("\n".join(lines), encoding="utf-8")
    print("done", len(sales), "sizes")


if __name__ == "__main__":
    main()
