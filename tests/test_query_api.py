import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from backend import main


class QueryApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.loop = asyncio.get_running_loop()
        main._query_lock = asyncio.Lock()
        self.push_patch = patch.object(main, "push", new_callable=AsyncMock)
        self.push = self.push_patch.start()
        self.addCleanup(self.push_patch.stop)

    async def test_blank_sku_rejected(self):
        with self.assertRaises(HTTPException) as error:
            await main.query_sku(main.SkuQuery(sku="   "))
        self.assertEqual(error.exception.status_code, 422)

    async def test_second_query_is_rejected_not_queued(self):
        started, release = threading.Event(), threading.Event()
        def worker(sku, job_id):
            started.set()
            release.wait(timeout=5)
            return {"ok": True, "job_id": job_id}
        first = asyncio.create_task(main._execute_query(worker, "A", "sku", "A"))
        try:
            await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=2)
            with self.assertRaises(HTTPException) as error:
                await main._execute_query(worker, "B", "sku", "B")
            self.assertEqual(error.exception.status_code, 409)
        finally:
            release.set()
            await first
        self.assertFalse(main._query_lock.locked())
        self.assertEqual([call.args[0] for call in self.push.await_args_list],
                         ["job_started", "result"])

    async def test_lock_released_after_worker_failure(self):
        def worker(*args):
            raise RuntimeError("device disconnected")
        with self.assertRaises(RuntimeError):
            await main._execute_query(worker, "A", "sku", "A")
        self.assertFalse(main._query_lock.locked())

    async def test_client_cancel_keeps_lock_until_worker_exits(self):
        started, release = threading.Event(), threading.Event()
        def worker(sku, job_id):
            started.set()
            release.wait(timeout=5)
            return {"ok": False, "job_id": job_id}
        with patch.object(main.bot, "request_cancel") as cancel:
            task = asyncio.create_task(main._execute_query(worker, "A", "sku", "A"))
            try:
                await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=2)
                task.cancel()
                await asyncio.sleep(0.02)
                self.assertTrue(main._query_lock.locked())
                cancel.assert_called_once()
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertFalse(main._query_lock.locked())


if __name__ == "__main__":
    unittest.main()
