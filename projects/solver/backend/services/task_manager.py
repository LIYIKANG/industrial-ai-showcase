from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable


class TaskManager:
    def __init__(self, workers: int = 2):
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="solver-task")
        self.tasks: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[], Any]) -> dict[str, Any]:
        task_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": task_id,
            "kind": kind,
            "status": "queued",
            "stage": "等待执行",
            "progress": 0,
            "created_at": now,
            "updated_at": now,
            "result": None,
            "error": None,
            "cancel_requested": False,
        }
        with self.lock:
            self.tasks[task_id] = record
        self.executor.submit(self._run, task_id, fn)
        return self.snapshot(task_id) or record

    def _update(self, task_id: str, **values: Any) -> None:
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task.update(values)
            task["updated_at"] = datetime.now(timezone.utc).isoformat()

    def _run(self, task_id: str, fn: Callable[[], Any]) -> None:
        self._update(task_id, status="running", stage="正在执行", progress=15)
        try:
            with self.lock:
                if self.tasks[task_id]["cancel_requested"]:
                    self.tasks[task_id].update(status="cancelled", stage="已取消", progress=100)
                    return
            result = fn()
            with self.lock:
                if self.tasks[task_id]["cancel_requested"]:
                    self.tasks[task_id].update(
                        status="cancelled",
                        stage="已取消，结果未写入",
                        progress=100,
                        result=None,
                    )
                    return
            self._update(task_id, status="completed", stage="已完成", progress=100, result=result)
        except Exception as exc:
            self._update(task_id, status="failed", stage="执行失败", progress=100, error=str(exc))

    def snapshot(self, task_id: str) -> dict[str, Any] | None:
        with self.lock:
            task = self.tasks.get(task_id)
            return dict(task) if task else None

    def cancel(self, task_id: str) -> dict[str, Any] | None:
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return None
            task["cancel_requested"] = True
            if task["status"] == "queued":
                task.update(status="cancelled", stage="已取消", progress=100)
            elif task["status"] == "running":
                task["stage"] = "正在取消；当前模型请求结束后丢弃结果"
            task["updated_at"] = datetime.now(timezone.utc).isoformat()
            return dict(task)
