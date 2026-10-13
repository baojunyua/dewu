"""Dump product detail hierarchy to find 出售 entry."""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import uiautomator2 as u2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import settings  # noqa: E402

OUT = ROOT / "screenshots"


def main() -> None:
    d = u2.connect(settings.device_serial)
    act = d.app_current()
    OUT.joinpath("_sell_probe_act.txt").write_text(str(act), encoding="utf-8")
    xml = d.dump_hierarchy()
    OUT.joinpath("_sell_probe.xml").write_text(xml, encoding="utf-8")
    d.screenshot(str(OUT / "_sell_probe.png"))

    lines = []
    for m in re.finditer(
        r'text="([^"]*)"[^>]*resource-id="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml,
    ):
        text, rid = m.group(1), m.group(2)
        if any(
            k in (text + rid)
            for k in ("出售", "卖", "出价", "收入", "sell", "Sell", "want", "想要")
        ):
            lines.append(f"T {text!r} {rid} {m.group(3)},{m.group(4)}-{m.group(5)},{m.group(6)}")
    for m in re.finditer(
        r'content-desc="([^"]*)"[^>]*resource-id="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml,
    ):
        desc, rid = m.group(1), m.group(2)
        if any(k in (desc + rid) for k in ("出售", "卖", "出价", "sell", "想要", "icon")):
            lines.append(
                f"D {desc!r} {rid} {m.group(3)},{m.group(4)}-{m.group(5)},{m.group(6)}"
            )
    # top-left image area clickable nodes
    for m in re.finditer(
        r'resource-id="(com\.shizhuang\.duapp:id/[^"]+)"[^>]*clickable="true"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml,
    ):
        x1, y1, x2, y2 = map(int, m.groups()[1:])
        if y2 < 200 and x1 < 1000:
            lines.append(f"CLK {m.group(1)} {x1},{y1}-{x2},{y2}")

    OUT.joinpath("_sell_probe_hits.txt").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
