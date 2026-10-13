"""Open photo search and dump UI steps."""
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


def dump(d, name: str) -> str:
    xml = d.dump_hierarchy()
    (OUT / f"{name}.xml").write_text(xml, encoding="utf-8")
    d.screenshot(str(OUT / f"{name}.png"))
    print("saved", name, "activity", d.app_current().get("activity"))
    return xml


def texts(xml: str) -> None:
    for m in re.finditer(r'text="([^"]+)"', xml):
        t = m.group(1).strip()
        if t and len(t) < 40:
            print(" text:", t)
    for m in re.finditer(r'content-desc="([^"]+)"', xml):
        t = m.group(1).strip()
        if t and len(t) < 40 and "电量" not in t:
            print(" desc:", t)
    for m in re.finditer(
        r'resource-id="(com\.shizhuang\.duapp:id/[^"]+)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml,
    ):
        rid = m.group(1)
        low = rid.lower()
        if any(
            k in low
            for k in (
                "photo",
                "album",
                "gallery",
                "camera",
                "search",
                "confirm",
                "thumb",
                "ok",
                "btn",
                "tv",
                "img",
            )
        ):
            print(f" rid: {rid} {m.group(2)},{m.group(3)}-{m.group(4)},{m.group(5)}")


def main() -> None:
    d = u2.connect(settings.device_serial)
    # leave publish page if there
    for _ in range(3):
        act = d.app_current().get("activity", "")
        if "Publish" in act or "gallery" in act.lower():
            d.press("back")
            time.sleep(0.8)
        else:
            break

    # ensure home-ish
    if not d(resourceId="com.shizhuang.duapp:id/ivPhotoSearch").exists(timeout=1):
        d.app_start("com.shizhuang.duapp")
        time.sleep(2)

    print("click ivPhotoSearch")
    assert d(resourceId="com.shizhuang.duapp:id/ivPhotoSearch").click_exists(timeout=2)
    time.sleep(2)
    xml = dump(d, "_probe_photosearch")
    texts(xml)

    # try common album buttons
    for sel in (
        {"text": "相册"},
        {"textContains": "相册"},
        {"text": "从相册选择"},
        {"textContains": "相册选择"},
        {"descriptionContains": "相册"},
        {"text": "图片"},
        {"textContains": "图库"},
    ):
        if d(**sel).click_exists(timeout=0.8):
            print("clicked", sel)
            time.sleep(1.5)
            xml = dump(d, "_probe_album")
            texts(xml)
            break
    else:
        print("no album text clicked; listing clickable")


if __name__ == "__main__":
    main()
