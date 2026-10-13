"""Background jobs (download, extract, scan, convert, collection) with
persistent status and logs. Identical requests are deduplicated: while a job
with the same ``dedupe_key`` is queued or running, submitting again returns
that job instead of starting a second one."""

from __future__ import annotations

import os
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from .db import StateDB, dumps, loads, now

MAX_LOG = 64 * 1024


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class JobContext:
    def __init__(self, mgr: JobManager, job_id: str):
        self.mgr = mgr
        self.id = job_id

    def log(self, *parts) -> None:
        self.mgr._append_log(self.id, " ".join(str(p) for p in parts))

    def progress(self, frac: float, message: str | None = None) -> None:
        self.mgr.db.x("UPDATE jobs SET progress=?, message=COALESCE(?, message) WHERE id=?",
                      (round(max(0.0, min(1.0, frac)), 3), message, self.id))


class JobManager:
    def __init__(self, db: StateDB, workers: int = 2):
        self.db = db
        self.pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="synty-job")
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self.recover()

    def recover(self) -> int:
        """Jobs left 'running'/'queued' by a process that no longer exists are marked failed."""
        n = 0
        for j in self.db.q("SELECT id, pid FROM jobs WHERE status IN ('queued','running')"):
            if not _pid_alive(j["pid"]):
                self.db.x("UPDATE jobs SET status='failed', error='interrupted (server restarted)', finished_at=? "
                          "WHERE id=?", (now(), j["id"]))
                n += 1
        return n

    def _append_log(self, job_id: str, line: str) -> None:
        with self.db.tx() as con:
            row = con.execute("SELECT log FROM jobs WHERE id=?", (job_id,)).fetchone()
            log = ((row[0] if row else "") + line.rstrip() + "\n")[-MAX_LOG:]
            con.execute("UPDATE jobs SET log=? WHERE id=?", (log, job_id))

    def submit(self, kind: str, params: dict, fn, *, dedupe_key: str | None = None) -> dict:
        with self._lock:
            if dedupe_key:
                live = self.db.one("SELECT id, pid FROM jobs WHERE dedupe_key=? AND status IN ('queued','running') "
                                   "ORDER BY created_at DESC", (dedupe_key,))
                if live and _pid_alive(live["pid"]):
                    return {**self.get(live["id"]), "deduplicated": True}
            job_id = uuid.uuid4().hex[:12]
            self.db.x("INSERT INTO jobs(id, kind, dedupe_key, params, status, progress, created_at, pid) "
                      "VALUES(?,?,?,?,?,?,?,?)", (job_id, kind, dedupe_key, dumps(params), "queued", 0.0, now(),
                                                 os.getpid()))
            ev = threading.Event()
            self._events[job_id] = ev
        self.pool.submit(self._run, job_id, fn, ev)
        return self.get(job_id)

    def _run(self, job_id: str, fn, ev: threading.Event) -> None:
        ctx = JobContext(self, job_id)
        self.db.x("UPDATE jobs SET status='running', started_at=? WHERE id=?", (now(), job_id))
        try:
            result = fn(ctx)
            self.db.x("UPDATE jobs SET status='done', progress=1.0, result=?, finished_at=? WHERE id=?",
                      (dumps(result), now(), job_id))
        except Exception as exc:  # noqa: BLE001 - recorded on the job
            ctx.log(traceback.format_exc()[-3000:])
            self.db.x("UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
                      (f"{type(exc).__name__}: {exc}", now(), job_id))
        finally:
            ev.set()

    def wait(self, job_id: str, timeout: float) -> dict:
        ev = self._events.get(job_id)
        if ev is not None:
            ev.wait(timeout)
        return self.get(job_id)

    def get(self, job_id: str, *, with_log: bool = False) -> dict | None:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not j:
            return None
        out = {
            "job_id": j["id"], "kind": j["kind"], "status": j["status"], "progress": j["progress"],
            "message": j["message"], "error": j["error"], "params": loads(j["params"], {}),
            "result": loads(j["result"]), "created_at": j["created_at"], "finished_at": j["finished_at"],
        }
        if with_log:
            out["log"] = j["log"][-8000:]
        return out

    def list(self, status: str | None = None, limit: int = 50) -> list[dict]:
        if status:
            rows = self.db.q("SELECT id FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit))
        else:
            rows = self.db.q("SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            j = self.get(r["id"])
            j.pop("result", None)
            out.append(j)
        return out
