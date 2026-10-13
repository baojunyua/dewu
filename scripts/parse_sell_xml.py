from pathlib import Path
import re

xml = Path("screenshots/_sell_probe.xml").read_text(encoding="utf-8")
rows = []
for m in re.finditer(r"<node ([^>]*)/?>", xml):
    attrs = dict(re.findall(r'(\S+)="([^"]*)"', m.group(1)))
    b = attrs.get("bounds", "")
    mm = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
    if not mm:
        continue
    x1, y1, x2, y2 = map(int, mm.groups())
    rid = attrs.get("resource-id", "")
    text = attrs.get("text", "")
    desc = attrs.get("content-desc", "")
    click = attrs.get("clickable") == "true"
    # left panel icons / top bar
    if y1 < 250 and x1 < 1100 and (click or "icon" in rid.lower() or "sell" in rid.lower() or "出售" in text + desc):
        rows.append((y1, x1, click, rid, text, desc, b))
    if "出售" in text + desc or "sell" in rid.lower() or "Sell" in rid:
        rows.append((y1, x1, click, rid, text, desc, b))

rows = sorted(set(rows))
Path("screenshots/_sell_top_clickables.txt").write_text(
    "\n".join(str(r) for r in rows), encoding="utf-8"
)
print("n=", len(rows))
