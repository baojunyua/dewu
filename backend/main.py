from __future__ import annotations

import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .cleanup import cleanup_media
from .config import ROOT, settings
from .dewu_bot import QueryResult, bot
from .ws_manager import manager

FRONTEND = ROOT / "frontend"
FRONTEND_ASSETS = FRONTEND / "assets"
executor = ThreadPoolExecutor(max_workers=1)  # 得物操作串行，避免抢控件
logger = logging.getLogger("dewu")

app = FastAPI(title="得物决策助手", version="0.1.0")
_query_lock = asyncio.Lock()


class SkuQuery(BaseModel):
    sku: str = Field(..., min_length=1, max_length=128)


class StatusOut(BaseModel):
    device: str
    adb_path: str
    connected_clients: int


async def push(event: str, data: dict) -> None:
    await manager.broadcast({"event": event, "data": data})


def _publish_prices(result: QueryResult, job_id: str) -> None:
    payload = result.to_dict()
    payload["job_id"] = job_id
    future = asyncio.run_coroutine_threadsafe(push("price_result", payload), loop)
    try:
        future.result(timeout=5)
    except Exception:
        logger.exception("price result broadcast failed")


def _run_sku(sku: str, job_id: str) -> dict:
    def progress(stage: str, msg: str) -> None:
        # 线程里不能直接 await，用线程安全调度
        fut = asyncio.run_coroutine_threadsafe(
            push(
                "progress",
                {"job_id": job_id, "stage": stage, "message": msg, "query": sku},
            ),
            loop,
        )
        try:
            fut.result(timeout=5)
        except Exception:
            pass

    result = bot.query_by_sku(
        sku, progress=progress, on_prices=lambda result: _publish_prices(result, job_id)
    )
    payload = result.to_dict()
    payload["job_id"] = job_id
    return payload


def _run_image(path: Path, job_id: str) -> dict:
    def progress(stage: str, msg: str) -> None:
        fut = asyncio.run_coroutine_threadsafe(
            push(
                "progress",
                {
                    "job_id": job_id,
                    "stage": stage,
                    "message": msg,
                    "query": path.name,
                },
            ),
            loop,
        )
        try:
            fut.result(timeout=5)
        except Exception:
            pass

    try:
        result = bot.query_by_image(
            path, progress=progress, on_prices=lambda result: _publish_prices(result, job_id)
        )
        payload = result.to_dict()
        payload["job_id"] = job_id
        return payload
    finally:
        # 识图用完即删上传文件，避免 uploads 堆积
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


loop: asyncio.AbstractEventLoop
_cleanup_task: asyncio.Task | None = None


async def _cleanup_loop() -> None:
    interval = max(5.0, float(settings.cleanup_interval_minutes)) * 60
    while True:
        try:
            await asyncio.to_thread(cleanup_media, settings)
        except Exception:
            logger.exception("periodic cleanup failed")
        await asyncio.sleep(interval)


@app.on_event("startup")
async def _startup() -> None:
    global loop, _cleanup_task
    loop = asyncio.get_running_loop()
    try:
        cleanup_media(settings)
    except Exception:
        logger.exception("startup cleanup failed")
    _cleanup_task = asyncio.create_task(_cleanup_loop())


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _cleanup_task
    if _cleanup_task is not None:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
        _cleanup_task = None


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "service": "dewu"}


@app.get("/api/status", response_model=StatusOut)
async def status() -> StatusOut:
    return StatusOut(
        device=settings.device_serial,
        adb_path=settings.adb_path,
        connected_clients=len(manager._clients),
    )


@app.post("/api/query/sku")
async def query_sku(body: SkuQuery) -> dict:
    sku = body.sku.strip()
    if not sku:
        raise HTTPException(status_code=422, detail="货号不能为空")
    return await _execute_query(_run_sku, sku, "sku", sku)


async def _execute_query(worker, argument, query_type: str, query: str) -> dict:
    if _query_lock.locked():
        raise HTTPException(status_code=409, detail="已有查询正在执行，请等待完成或终止当前查询")
    job_id = uuid.uuid4().hex[:10]
    async with _query_lock:
        await push(
            "job_started",
            {"job_id": job_id, "query_type": query_type, "query": query},
        )
        future = loop.run_in_executor(executor, worker, argument, job_id)
        try:
            result = await asyncio.shield(future)
        except asyncio.CancelledError:
            # Keep ownership of the device until the worker actually stops.
            bot.request_cancel()
            await future
            raise
        await push("result", result)
        return result


@app.post("/api/query/image")
async def query_image(file: UploadFile = File(...)) -> dict:
    job_id = uuid.uuid4().hex[:10]
    suffix = Path(file.filename or "upload.jpg").suffix or ".jpg"
    dest = settings.upload_dir / f"{job_id}{suffix}"
    content = await file.read()
    dest.write_bytes(content)

    try:
        return await _execute_query(
            _run_image, dest, "image", file.filename or dest.name
        )
    finally:
        dest.unlink(missing_ok=True)


@app.post("/api/cleanup")
async def cleanup_now() -> dict:
    """手动触发清理截图/上传目录。"""
    summary = await asyncio.to_thread(cleanup_media, settings)
    return {"ok": True, **summary}


@app.post("/api/cancel")
async def cancel_query() -> dict:
    """终止当前正在执行的查价任务。"""
    running = bot.request_cancel()
    await push(
        "progress",
        {
            "stage": "cancel",
            "message": "已请求终止查询…" if running else "当前没有进行中的查询",
        },
    )
    return {"ok": True, "was_running": running}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    await ws.send_json(
        {
            "event": "hello",
            "data": {
                "message": "已连接得物决策助手",
                "device": settings.device_serial,
            },
        }
    )
    try:
        while True:
            # 保持连接；前端可发 ping
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_json({"event": "pong", "data": {}})
    except WebSocketDisconnect:
        await manager.disconnect(ws)


@app.get("/screenshots/{name}")
async def get_screenshot(name: str) -> FileResponse:
    # 防止路径穿越
    path = (settings.screenshot_dir / name).resolve()
    if path.parent != settings.screenshot_dir.resolve() or not path.exists():
        raise HTTPException(status_code=404, detail="screenshot not found")
    return FileResponse(path)


if FRONTEND_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS), name="assets")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")
