"""
Queue Manager - 비동기 작업 큐 관리
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class QueueTask:
    task_type: str
    payload: dict[str, Any]
    retry_count: int = 0


class QueueManager:
    """Thread-safe in-memory task queue with retry and dead-letter support."""

    def __init__(self, max_retries: int = 3) -> None:
        self.max_retries = max_retries
        self._lock = threading.Lock()
        self._queue: list[QueueTask] = []
        self._dead_letter: list[QueueTask] = []
        self._processed = 0
        self._failed = 0

    def enqueue(self, task_type: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._queue.append(QueueTask(task_type=task_type, payload=payload))

    def process_all(self, handlers: dict[str, Callable[[dict[str, Any]], None]]) -> None:
        """Process all queued tasks using provided handlers."""
        while True:
            with self._lock:
                if not self._queue:
                    break
                task = self._queue.pop(0)

            handler = handlers.get(task.task_type)
            if handler is None:
                with self._lock:
                    self._failed += 1
                    if task.retry_count < self.max_retries:
                        task.retry_count += 1
                        self._queue.append(task)
                    else:
                        self._dead_letter.append(task)
                continue

            try:
                handler(task.payload)
                with self._lock:
                    self._processed += 1
            except Exception:
                with self._lock:
                    self._failed += 1
                    if task.retry_count < self.max_retries:
                        task.retry_count += 1
                        self._queue.append(task)
                    else:
                        self._dead_letter.append(task)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "queued": len(self._queue),
                "processed": self._processed,
                "failed": self._failed,
                "dead_letter": len(self._dead_letter),
            }
