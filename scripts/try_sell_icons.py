"""Click left-panel toolbar icons and see which opens sell sheet."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import uiautomator2 as u2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import settings  # noqa: E402

OUT = ROOT / "screenshots"
log: list[str] = []


def dump(d, name: str) -> str:
    xml = d.dump_hierarchy()
    (OUT / f"{name}.xml").write_text(xml, encoding="utf-8")
    d.screenshot(str(OUT / f"{name}.png"))
    return xml


def main() -> None:
    d = u2.connect(settings.device_serial)
    icons = [
        ((731 + 790) // 2, (51 + 110) // 2, "icon0"),
        ((807 + 866) // 2, (51 + 110) // 2, "icon1"),
        ((883 + 942) // 2, (51 + 110) // 2, "icon2"),
    ]
    for x, y, name in icons:
        log.append(f"click {name} {x},{y}")
        d.click(x, y)
        time.sleep(1.8)
        xml = dump(d, f"_after_{name}")
        keys = [k for k in ("预计收入", "最低出售价", "去出价", "出售", "我想出") if k in xml]
        act = d.app_current().get("activity", "")
        log.append(f"  act={act.split('.')[-1]} keys={keys}")
        # close sheet if opened
        if any(k in xml for k in ("预计收入", "最低出售价", "去出价")):
            log.append("  SUCCESS sell sheet")
            break
        d.press("back")
        time.sleep(0.8)

    (OUT / "_sell_click_log.txt").write_text("\n".join(log), encoding="utf-8")


if __name__ == "__main__":
    main()
