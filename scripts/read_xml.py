from pathlib import Path
import re

out = Path("screenshots/_probe_texts.txt")
xml = Path("screenshots/_probe_photosearch.xml").read_text(encoding="utf-8")
lines = []
for m in re.finditer(r'text="([^"]+)"', xml):
    lines.append("TEXT " + m.group(1))
for m in re.finditer(r'content-desc="([^"]+)"', xml):
    if m.group(1):
        lines.append("DESC " + m.group(1))
out.write_text("\n".join(lines), encoding="utf-8")
print("wrote", out, "n=", len(lines))
