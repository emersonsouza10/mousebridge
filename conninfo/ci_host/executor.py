"""Thread pool para rodar DBAPI bloqueante fora do event loop.

Drivers de banco são síncronos; sem isto, uma consulta longa congelaria todas as
sessões e o controle (CANCEL). Cada operação de banco roda num thread; o loop
nunca bloqueia.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable


class DbExecutor:
    def __init__(self, pool_size: int) -> None:
        self._pool = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="conninfo-db")

    async def run(self, fn: Callable[..., Any], *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, fn, *args)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
