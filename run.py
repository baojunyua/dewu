"""得物决策助手服务入口。"""

from __future__ import annotations

import uvicorn

from backend.config import settings


def main() -> None:
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
