from pathlib import Path
import re
import sys
import time

import uiautomator2 as u2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import settings  # noqa: E402

OUT = ROOT / "screenshots"


def main() -> None:
    d = u2.connect(settings.device_serial)
    print("act", d.app_current())
    xml = d.dump_hierarchy()
    (OUT / "_probe_psresult.xml").write_text(xml, encoding="utf-8")
    d.screenshot(str(OUT / "_probe_psresult.png"))

    texts = []
    for m in re.finditer(r'text="([^"]+)"', xml):
        texts.append(m.group(1))
    (OUT / "_probe_psresult_texts.txt").write_text("\n".join(texts), encoding="utf-8")

    rids = []
    for m in re.finditer(
        r'resource-id="(com\.shizhuang\.duapp:id/[^"]+)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml,
    ):
        rid = m.group(1)
        low = rid.lower()
        if any(
            k in low
            for k in (
                "product",
                "item",
                "cover",
                "title",
                "price",
                "img",
                "card",
                "goods",
                "result",
                "rv",
                "recycler",
            )
        ):
            rids.append(f"{rid} {m.group(2)},{m.group(3)}-{m.group(4)},{m.group(5)}")
    (OUT / "_probe_psresult_rids.txt").write_text("\n".join(rids), encoding="utf-8")
    print("wrote dumps", "texts", len(texts), "rids", len(rids))

    # try click first product-like node
    for rid in (
        "com.shizhuang.duapp:id/ivCover",
        "com.shizhuang.duapp:id/imgCover",
        "com.shizhuang.duapp:id/ivProduct",
        "com.shizhuang.duapp:id/productImage",
        "com.shizhuang.duapp:id/iv_cover",
    ):
        if d(resourceId=rid).click_exists(timeout=0.6):
            print("clicked", rid)
            time.sleep(2)
            print("now", d.app_current())
            d.screenshot(str(OUT / "_probe_after_product.png"))
            return

    # fallback: click center of first result area
    w, h = d.window_size()
    d.click(int(w * 0.25), int(h * 0.35))
    time.sleep(2)
    print("fallback click now", d.app_current())
    d.screenshot(str(OUT / "_probe_after_product.png"))
    xml2 = d.dump_hierarchy()
    (OUT / "_probe_after_product.xml").write_text(xml2, encoding="utf-8")


if __name__ == "__main__":
    main()
