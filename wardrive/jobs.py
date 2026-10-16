"""Execução de tarefas longas (scan de pasta, export, upload) em segundo plano.

Um único worker: o WDGoWars recusa uploads simultâneos da mesma conta
(HTTP 429 "Another upload is already being processed"), então serializar
é requisito, não escolha.
"""
from __future__ import annotations

import queue
import threading
import traceback
import uuid
from datetime import datetime

MAX_LOG_LINES = 400


class Job:
    def __init__(self, kind: str, label: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.label = label
        self.status = "na fila"
        self.created_at = datetime.now().strftime("%H:%M:%S")
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.total = 0
        self.done = 0
        self.unit = "itens"
        self.log: list[dict] = []
        self.result: dict | None = None
        self.error: str | None = None
        self._lock = threading.Lock()

    # -------------------------------------------------- API usada pelos workers
    def write(self, message: str, level: str = "info") -> None:
        with self._lock:
            self.log.append({"ts": datetime.now().strftime("%H:%M:%S"), "level": level, "msg": message})
            if len(self.log) > MAX_LOG_LINES:
                del self.log[: len(self.log) - MAX_LOG_LINES]

    def set_total(self, total: int, unit: str = "itens") -> None:
        with self._lock:
            self.total = int(total)
            self.unit = unit

    def tick(self, message: str | None = None) -> None:
        with self._lock:
            self.done += 1
        if message:
            self.write(message)

    def progress(self) -> dict:
        pct = round(100 * self.done / self.total) if self.total else 0
        return {"done": self.done, "total": self.total, "percent": min(pct, 100), "unit": self.unit}

    def to_dict(self, *, with_log: bool = True) -> dict:
        data = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": self.progress(),
            "result": self.result,
            "error": self.error,
        }
        if with_log:
            with self._lock:
                data["log"] = list(self.log[-120:])
        return data


class JobRunner:
    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, name="wardrive-jobs", daemon=True)
        self._thread.start()

    def submit(self, kind: str, label: str, fn, *args, **kwargs) -> Job:
        job = Job(kind, label)
        with self._lock:
            self._jobs[job.id] = job
            # mantém no máximo 30 jobs em memória
            if len(self._jobs) > 30:
                for key in list(self._jobs)[:-30]:
                    if self._jobs[key].status in ("ok", "erro"):
                        self._jobs.pop(key, None)
        self._queue.put((job, fn, args, kwargs))
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 15) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())[::-1]
        return jobs[:limit]

    def busy(self) -> bool:
        return any(j.status in ("na fila", "rodando") for j in self.list(30))

    def _loop(self) -> None:
        while True:
            job, fn, args, kwargs = self._queue.get()
            job.status = "rodando"
            job.started_at = datetime.now().strftime("%H:%M:%S")
            try:
                job.result = fn(job, *args, **kwargs)
                job.status = "ok"
            except Exception as exc:  # noqa: BLE001 - queremos mostrar o erro na UI
                job.status = "erro"
                job.error = f"{type(exc).__name__}: {exc}"
                job.write(job.error, "erro")
                job.write(traceback.format_exc(limit=6), "detalhe")
            finally:
                job.finished_at = datetime.now().strftime("%H:%M:%S")
                self._queue.task_done()


runner = JobRunner()
