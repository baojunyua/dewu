"""End-to-end probe: push image -> photo search -> pick thumb -> wait detail."""
from __future__ import annotations

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
    # leave graph search if open
    for _ in range(4):
        act = d.app_current().get("activity", "")
        if "GraphSearch" in act or "Publish" in act:
            d.press("back")
            time.sleep(0.7)
        else:
            break

    src = OUT / "_probe_home.png"
    if not src.exists():
        d.screenshot(str(src))

    remote = "/sdcard/Pictures/dewu_query.jpg"
    print("push", src, "->", remote)
    d.push(str(src), remote)
    try:
        d.shell(
            f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://{remote}"
        )
    except Exception as e:
        print("scan err", e)
    time.sleep(1)

    # also copy to DCIM
    d.shell(f"cp {remote} /sdcard/DCIM/Camera/dewu_query.jpg || true")
    try:
        d.shell(
            "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file:///sdcard/DCIM/Camera/dewu_query.jpg"
        )
    except Exception:
        pass

    if not d(resourceId="com.shizhuang.duapp:id/ivPhotoSearch").exists(timeout=2):
        d.app_start("com.shizhuang.duapp")
        time.sleep(2)

    print("open photo search")
    d(resourceId="com.shizhuang.duapp:id/ivPhotoSearch").click(timeout=2)
    time.sleep(1.5)

    d(resourceId="com.shizhuang.duapp:id/tvAlbum").click_exists(timeout=1)
    time.sleep(0.5)
    # expand album strip if needed
    d(resourceId="com.shizhuang.duapp:id/tvToggleButton").click_exists(timeout=0.5)
    time.sleep(0.8)

    thumbs = d(resourceId="com.shizhuang.duapp:id/imgThumb")
    print("thumbs", thumbs.count)
    if thumbs.count == 0:
        d.screenshot(str(OUT / "_probe_no_thumb.png"))
        print("no thumbs")
        return

    print("click first thumb")
    thumbs[0].click()
    time.sleep(2)
    d.screenshot(str(OUT / "_probe_after_thumb.png"))
    print("activity", d.app_current())

    for i in range(25):
        time.sleep(1)
        act = d.app_current().get("activity", "")
        xml = d.dump_hierarchy()
        hit = any(
            k in xml
            for k in ("出售", "立即购买", "加入购物车", "最近购买", "发售日期", "货号")
        )
        print(i, act.split(".")[-1], "detail_like=", hit)
        d.screenshot(str(OUT / f"_probe_wait_{i}.png"))
        (OUT / f"_probe_wait_{i}.xml").write_text(xml, encoding="utf-8")
        if hit or "ProductDetail" in act or "Detail" in act:
            print("REACHED DETAIL-ish")
            break
        # maybe result list - click first product
        if i in (3, 6, 10):
            for sel in (
                {"textContains": "相似"},
                {"textContains": "¥"},
            ):
                if d(**sel).click_exists(timeout=0.5):
                    print("clicked", sel)
                    break


if __name__ == "__main__":
    main()
