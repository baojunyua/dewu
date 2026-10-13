"""Dump current screen texts around graph search permission dialog."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import uiautomator2 as u2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import settings  # noqa: E402

OUT = ROOT / "screenshots"


def main() -> None:
    d = u2.connect(settings.device_serial)
    print("act", d.app_current())
    xml = d.dump_hierarchy()
    (OUT / "_probe_cam_dialog.xml").write_text(xml, encoding="utf-8")
    d.screenshot(str(OUT / "_probe_cam_dialog.png"))
    texts = []
    for m in re.finditer(
        r'text="([^"]*)"[^>]*resource-id="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml,
    ):
        if m.group(1):
            texts.append(
                f"{m.group(1)} | {m.group(2)} | {m.group(3)},{m.group(4)}-{m.group(5)},{m.group(6)}"
            )
    for m in re.finditer(
        r'content-desc="([^"]+)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml,
    ):
        if "电量" not in m.group(1) and "信号" not in m.group(1):
            texts.append(f"DESC {m.group(1)} | {m.group(2)},{m.group(3)}-{m.group(4)},{m.group(5)}")
    (OUT / "_probe_cam_dialog_texts.txt").write_text("\n".join(texts), encoding="utf-8")
    print("wrote", len(texts), "lines")


if __name__ == "__main__":
    main()
