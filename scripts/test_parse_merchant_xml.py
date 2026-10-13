from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.dewu_bot import DewuBot

xml = Path("screenshots/probe_m_sales.xml").read_text(encoding="utf-8")
bot = DewuBot()
parsed = bot._extract_merchant_size_sales(xml)
print("count", len(parsed))
for k in sorted(parsed, key=lambda x: float(x)):
    print(k, parsed[k]["sales_7d"])
