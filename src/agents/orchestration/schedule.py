"""Scheduled agent runs. Streamlit has no daemon, so due jobs run when the app is opened/refreshed
(`run_due`); a cron/launchd caller can invoke the same function headlessly. State is vault-encrypted."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional, Tuple

from src.agents.store import _load, _save
from .filelock import locked

KEY = "agent_schedule"
MIN_INTERVAL_MIN = 15


def _now() -> datetime:
    return datetime.now(timezone.utc)


def add_job(vault: Any, name: str, query: str, every_minutes: int) -> str:
    with locked("schedule"):
        return _add(vault, name, query, every_minutes)


def _add(vault, name, query, every_minutes) -> str:
    data = _load(vault, KEY, {"jobs": []})
    job = {"id": uuid.uuid4().hex, "name": name[:60], "query": query[:500],
           "every_minutes": max(MIN_INTERVAL_MIN, int(every_minutes)), "last_run": None}
    data["jobs"].append(job)
    _save(vault, KEY, data)
    return job["id"]


def list_jobs(vault: Any) -> List[dict]:
    return list(_load(vault, KEY, {"jobs": []})["jobs"])


def remove_job(vault: Any, job_id: str) -> None:
    with locked("schedule"):
        data = _load(vault, KEY, {"jobs": []})
        _save(vault, KEY, {"jobs": [j for j in data["jobs"] if j["id"] != job_id]})


def record_result(vault: Any, job_id: str, text: str) -> None:
    """Short templated outcome shown in the Scheduled tab (never raw agent/untrusted text)."""
    with locked("schedule"):
        data = _load(vault, KEY, {"jobs": []})
        for j in data["jobs"]:
            if j["id"] == job_id:
                j["last_result"] = text[:200]
        _save(vault, KEY, data)


def _claim(vault: Any, job_id: str, now: datetime) -> bool:
    """Atomically mark a still-due job as run, so two processes never run the same job."""
    with locked("schedule"):
        data = _load(vault, KEY, {"jobs": []})
        for j in data["jobs"]:
            if j["id"] == job_id and j in due_jobs_from(data["jobs"], now):
                j["last_run"] = now.isoformat()
                _save(vault, KEY, data)
                return True
    return False


def due_jobs_from(jobs: List[dict], now: datetime) -> List[dict]:
    return [j for j in jobs
            if j["last_run"] is None
            or now - datetime.fromisoformat(j["last_run"]) >= timedelta(minutes=j["every_minutes"])]


def due_jobs(vault: Any, now: Optional[datetime] = None) -> List[dict]:
    return due_jobs_from(list_jobs(vault), now or _now())


def run_due(vault: Any, run_query: Callable[[str], Any], now: Optional[datetime] = None) -> List[Tuple[dict, Any]]:
    """Runs each due job once. A failing job is marked run (no hot retry loop) and reported as an exception."""
    now = now or _now()
    results = []
    for job in due_jobs(vault, now):
        if not _claim(vault, job["id"], now):
            continue  # another process got there first
        try:
            outcome = run_query(job["query"])
        except Exception as exc:
            outcome = exc
        results.append((job, outcome))
    return results
