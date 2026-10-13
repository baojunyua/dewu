"""清理 screenshots / uploads，避免查价截图与识图上传无限堆积。"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger("dewu.cleanup")

# 调试探针文件前缀，可更激进删除
_PROBE_PREFIXES = ("_probe_", "_dismiss_", "_wins", "_cam_")


def _iter_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return [p for p in directory.iterdir() if p.is_file()]


def cleanup_dir(
    directory: Path,
    *,
    max_age_hours: float,
    keep_newest: int = 40,
    probe_max_age_hours: float = 1.0,
) -> dict:
    """
    按修改时间删除过期文件；若仍过多则只保留最新 keep_newest 个。
    返回 {deleted, kept, freed_bytes}。
    """
    now = time.time()
    max_age_sec = max(0.0, max_age_hours) * 3600
    probe_age_sec = max(0.0, probe_max_age_hours) * 3600
    deleted = 0
    freed = 0
    survivors: list[tuple[float, Path]] = []

    for path in _iter_files(directory):
        try:
            mtime = path.stat().st_mtime
            size = path.stat().st_size
        except OSError:
            continue

        name = path.name
        is_probe = name.startswith("_") or any(
            name.startswith(p) for p in _PROBE_PREFIXES
        )
        age_limit = probe_age_sec if is_probe else max_age_sec
        if now - mtime > age_limit:
            try:
                path.unlink(missing_ok=True)
                deleted += 1
                freed += size
            except OSError as e:
                logger.warning("delete failed %s: %s", path, e)
            continue
        survivors.append((mtime, path))

    # 仍超量：按时间倒序只留最新 N 个
    if keep_newest >= 0 and len(survivors) > keep_newest:
        survivors.sort(key=lambda x: x[0], reverse=True)
        for _, path in survivors[keep_newest:]:
            try:
                size = path.stat().st_size
                path.unlink(missing_ok=True)
                deleted += 1
                freed += size
            except OSError as e:
                logger.warning("delete failed %s: %s", path, e)
        survivors = survivors[:keep_newest]

    return {"deleted": deleted, "kept": len(survivors), "freed_bytes": freed}


def cleanup_media(settings) -> dict:
    shot = cleanup_dir(
        settings.screenshot_dir,
        max_age_hours=settings.screenshot_max_age_hours,
        keep_newest=settings.screenshot_keep_newest,
        probe_max_age_hours=settings.probe_max_age_hours,
    )
    up = cleanup_dir(
        settings.upload_dir,
        max_age_hours=settings.upload_max_age_hours,
        keep_newest=settings.upload_keep_newest,
        probe_max_age_hours=settings.probe_max_age_hours,
    )
    summary = {
        "screenshots": shot,
        "uploads": up,
    }
    total_del = shot["deleted"] + up["deleted"]
    if total_del:
        logger.info(
            "cleanup: screenshots -%s uploads -%s (freed %.1f KB)",
            shot["deleted"],
            up["deleted"],
            (shot["freed_bytes"] + up["freed_bytes"]) / 1024,
        )
    return summary
