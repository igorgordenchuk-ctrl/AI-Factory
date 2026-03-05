"""
ParallelManager — manages concurrent worker execution.
Uses ThreadPoolExecutor (workers are I/O-bound: waiting for Claude API).
"""

import logging
import concurrent.futures
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)


class ParallelManager:
    """
    Manages a pool of worker threads.

    Two modes:
    1. Persistent workers: long-running agents that watch folders
    2. Batch workers: one-shot tasks executed in parallel
    """

    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="factory-worker",
        )
        self._futures: dict[str, concurrent.futures.Future] = {}  # agent_id → Future
        self._lock = Lock()
        self._shutdown = False

    def submit_worker(self, agent_id: str, fn, *args, **kwargs) -> str:
        """
        Submit a worker function to the thread pool.
        Returns agent_id for tracking.
        """
        if self._shutdown:
            raise RuntimeError("ParallelManager is shut down")

        future = self._executor.submit(fn, *args, **kwargs)

        with self._lock:
            self._futures[agent_id] = future

        # Callback для очистки при завершении
        def on_done(f):
            with self._lock:
                self._futures.pop(agent_id, None)
            if f.exception():
                logger.error(f"[{agent_id}] Завершился с ошибкой: {f.exception()}")
            else:
                logger.info(f"[{agent_id}] Завершил работу")

        future.add_done_callback(on_done)
        logger.info(f"[{agent_id}] Запущен в пуле потоков")
        return agent_id

    def submit_batch(
        self,
        tasks: list,
        worker_fn,
        max_parallel: Optional[int] = None,
    ) -> list:
        """
        Execute a batch of tasks in parallel.
        worker_fn(task) is called for each task.
        Returns list of results.
        """
        n = max_parallel or self.max_workers
        results = []

        # Используем отдельный executor для батча
        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as batch_executor:
            future_to_task = {
                batch_executor.submit(worker_fn, task): task
                for task in tasks
            }

            for future in concurrent.futures.as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.error(f"Ошибка в батч-задаче: {e}")
                    results.append(None)

        return results

    def get_status(self) -> dict:
        """Return status of all tracked workers."""
        with self._lock:
            active = sum(1 for f in self._futures.values() if f.running())
            pending = sum(1 for f in self._futures.values() if not f.done())
            return {
                "active": active,
                "pending": pending,
                "total_tracked": len(self._futures),
                "max_workers": self.max_workers,
                "agents": list(self._futures.keys()),
            }

    def is_idle(self) -> bool:
        """Check if no workers are currently running."""
        with self._lock:
            return all(f.done() for f in self._futures.values())

    def shutdown(self, wait: bool = True):
        """Shutdown the thread pool."""
        self._shutdown = True
        self._executor.shutdown(wait=wait)
        logger.info("ParallelManager остановлен")
