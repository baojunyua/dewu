"""Confirm SKU dialog then reach sell income sheet."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import uiautomator2 as u2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import settings  # noqa: E402

OUT = ROOT / "screenshots"
lines: list[str] = []


def main() -> None:
    d = u2.connect(settings.device_serial)
    xml = d.dump_hierarchy()
    if "是这款" in xml or "请确认实物" in xml:
        lines.append("confirm dialog present")
        if d(text="是这款").click_exists(timeout=2):
            lines.append("clicked 是这款")
            time.sleep(2)
        else:
            lines.append("failed click 是这款")
    else:
        # click sell icon then confirm
        d.click(760, 80)
        time.sleep(1.5)
        lines.append("clicked sell icon")
        if d(text="是这款").click_exists(timeout=2):
            lines.append("clicked 是这款")
            time.sleep(2)

    xml = d.dump_hierarchy()
    (OUT / "_after_confirm.xml").write_text(xml, encoding="utf-8")
    d.screenshot(str(OUT / "_after_confirm.png"))
    for k in ("预计收入", "最低出售价", "去出价", "是这款", "请确认实物", "出售"):
        if k in xml:
            lines.append(f"has {k}")

    if d(text="预计收入").click_exists(timeout=1.5):
        lines.append("clicked 预计收入")
        time.sleep(1.5)
        xml2 = d.dump_hierarchy()
        (OUT / "_after_income.xml").write_text(xml2, encoding="utf-8")
        d.screenshot(str(OUT / "_after_income.png"))
        lines.append(f"income page size hints: {'尺码' in xml2}")

    (OUT / "_confirm_log.txt").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
