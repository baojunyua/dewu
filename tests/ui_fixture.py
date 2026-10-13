"""Local-only UI fixture; never connects to or operates the emulator."""

import asyncio
import sys
from dataclasses import asdict
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.dewu_bot import QueryResult

app = FastAPI()
app.mount("/assets", StaticFiles(directory=ROOT / "frontend/assets"), name="assets")
clients = set()
sequence = 0


async def publish(event, data):
    for ws in list(clients):
        try:
            await ws.send_json({"event": event, "data": data})
        except Exception:
            clients.discard(ws)


@app.get("/")
async def index():
    return FileResponse(ROOT / "frontend/index.html")


@app.get("/screenshots/{name}")
async def screenshot(name: str):
    return FileResponse(ROOT / "screenshots" / Path(name).name)


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    await ws.send_json({"event": "hello", "data": {"message": "界面测试连接"}})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


@app.post("/api/query/sku")
async def query(body: dict):
    global sequence
    sequence += 1
    job_id = f"fixture-{sequence}"
    sku = body["sku"]
    await publish("job_started", {"job_id": job_id, "query": sku, "query_type": "sku"})
    sizes = ["39", "40", "41", "42", "42.5", "43", "44", "44.5", "45", "46", "47", "47.5", "48.5"]
    rows = [{"size": size, "price": 263.46 + i * 10, "raw": ""} for i, size in enumerate(sizes)]
    shots = sorted((ROOT / "screenshots").glob("*_sizes_*.png"), key=lambda p: p.stat().st_mtime)
    sales_shots = sorted((ROOT / "screenshots").glob("*_sales_*.png"), key=lambda p: p.stat().st_mtime)
    product_shots = sorted((ROOT / "screenshots").glob("*_product_*.png"), key=lambda p: p.stat().st_mtime)
    result = QueryResult(
        ok=True, query_type="sku", query=sku, title="界面测试商品 · 安踏 KAI 2 欧文二代",
        article_number=sku, sell_price=312, lowest_price=263.46,
        size_prices=rows, screenshot=shots[-1].name if shots else None, elapsed_ms=18000,
        product_image=product_shots[-1].name if product_shots else None,
    )
    payload = asdict(result) | {"job_id": job_id}
    await asyncio.sleep(0.2)
    await publish("price_result", payload)
    await asyncio.sleep(0.5)
    result.sales_by_size = {size: {"sales_7d": i * 3} for i, size in enumerate(sizes)}
    result.sales_source = "merchant_7d"
    result.merchant_screenshot = sales_shots[-1].name if sales_shots else None
    result.elapsed_ms = 63000
    if sku == "PARTIAL":
        result.warnings = ["商家销量未采集"]
        result.sales_by_size = {}
        result.sales_source = None
    if sku == "FAIL":
        result.ok = False
        result.message = "界面测试：未找到货号"
    payload = asdict(result) | {"job_id": job_id}
    await publish("result", payload)
    return payload


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8766)
