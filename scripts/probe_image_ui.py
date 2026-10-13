"""Probe Dewu image-search UI on emulator."""
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
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.xml").write_text(xml, encoding="utf-8")
    d.screenshot(str(OUT / f"{name}.png"))
    return xml


def interesting(xml: str) -> None:
    keys = (
        "相机",
        "拍照",
        "识图",
        "搜索",
        "相册",
        "图库",
        "确认",
        "完成",
        "全部",
        "最近",
        "图片",
        "camera",
        "album",
        "gallery",
        "photo",
        "Search",
        "scan",
    )
    print("=== content-desc ===")
    for m in re.finditer(
        r'content-desc="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml
    ):
        desc = m.group(1)
        if desc and any(k.lower() in desc.lower() for k in keys):
            print(f"  [{desc}] {m.group(2)},{m.group(3)}-{m.group(4)},{m.group(5)}")

    print("=== text ===")
    for m in re.finditer(
        r'text="([^"]*)"[^>]*resource-id="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml,
    ):
        text, rid = m.group(1), m.group(2)
        blob = text + rid
        if text and any(k.lower() in blob.lower() for k in keys):
            print(
                f"  text=[{text}] id={rid} {m.group(3)},{m.group(4)}-{m.group(5)},{m.group(6)}"
            )

    print("=== resource-id (search/camera/img) ===")
    for m in re.finditer(
        r'resource-id="(com\.shizhuang\.duapp:id/[^"]+)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml,
    ):
        rid = m.group(1)
        low = rid.lower()
        if any(
            k in low
            for k in (
                "search",
                "camera",
                "scan",
                "photo",
                "album",
                "gallery",
                "img",
                "icon",
                "right",
                "iv_",
            )
        ):
            print(f"  {rid} {m.group(2)},{m.group(3)}-{m.group(4)},{m.group(5)}")


def main() -> None:
    d = u2.connect(settings.device_serial)
    print("app", d.app_current(), "size", d.window_size())
    xml = dump(d, "_probe_home")
    interesting(xml)

    # try open camera entry
    w, h = d.window_size()
    tapped = False
    for sel in (
        {"descriptionContains": "相机"},
        {"descriptionContains": "拍照"},
        {"descriptionContains": "识图"},
        {"descriptionContains": "扫一扫"},
    ):
        if d(**sel).click_exists(timeout=0.8):
            print("clicked", sel)
            tapped = True
            break
    if not tapped:
        # common: camera icon near search bar right
        for rid in (
            "com.shizhuang.duapp:id/ivCamera",
            "com.shizhuang.duapp:id/iv_camera",
            "com.shizhuang.duapp:id/ivSearchCamera",
            "com.shizhuang.duapp:id/ivPhotoSearch",
            "com.shizhuang.duapp:id/ivScan",
        ):
            if d(resourceId=rid).click_exists(timeout=0.5):
                print("clicked rid", rid)
                tapped = True
                break
    if not tapped:
        print("fallback click top-right search camera area")
        d.click(int(w * 0.93), int(h * 0.07))

    time.sleep(2)
    xml2 = dump(d, "_probe_camera")
    interesting(xml2)
    print("activity", d.app_current())


if __name__ == "__main__":
    main()
