"""Atomic records, history and durable SSE cursor in a single SQLite database."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from .models import now
from .settings import PACKAGE


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript((PACKAGE / "schema.sql").read_text(encoding="utf-8"))

    @contextmanager
    def transaction(self):
        with self.lock, self.db:
            yield

    def close(self):
        self.db.close()

    def _get(self, table, identifier):
        row = self.db.execute(f"SELECT document FROM {table} WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise KeyError(identifier)
        return json.loads(row[0])

    def get(self, table, identifier):
        assert table in ("events", "tasks")
        with self.lock:
            return self._get(table, identifier)

    def _emit(self, kind, payload):
        return self.db.execute(
            "INSERT INTO outbox(kind,payload) VALUES (?,?)", (kind, encode(payload))
        ).lastrowid

    def emit(self, kind, payload):
        with self.transaction():
            return self._emit(kind, payload)

    def _save(self, table, document, actor, before=None):
        date_key = "received_at" if table == "events" else "created_at"
        self.db.execute(
            f"INSERT INTO {table}(id,{date_key},document) VALUES (?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET document=excluded.document",
            (document["id"], document[date_key], encode(document)),
        )
        if table == "tasks":
            self.db.execute("DELETE FROM task_sources WHERE task_id=?", (document["id"],))
            self.db.executemany(
                "INSERT INTO task_sources(task_id,event_id) VALUES (?,?)",
                [(document["id"], eid) for eid in dict.fromkeys(document["source_event_ids"])],
            )
        self.db.execute(
            "INSERT INTO history(entity_id,actor,changed_at,before_json,after_json) "
            "VALUES (?,?,?,?,?)",
            (document["id"], actor, now(), encode(before) if before else None, encode(document)),
        )
        self._emit("event" if table == "events" else "task", document)

    def save_event(self, event, tasks=()):
        with self.transaction():
            try:
                before = self._get("events", event["id"])
            except KeyError:
                before = None
            self._save("events", event, "system", before)
            for task in tasks:
                self._save("tasks", task, "system")

    def create_task(self, task, actor):
        with self.transaction():
            self._validate_sources(task["source_event_ids"])
            self._save("tasks", task, actor)
        return task

    def _validate_sources(self, identifiers):
        for identifier in identifiers:
            event = self._get("events", identifier)
            if event["status"] not in ("needs_review", "confirmed"):
                raise ValueError("確定字幕の交信を選択してください")

    def edit(self, table, identifier, changes, actor):
        with self.transaction():
            before = self._get(table, identifier)
            after = {**before, **changes}
            if table == "events":
                if before["status"] not in ("needs_review", "confirmed"):
                    raise ValueError("認識完了後に確認・訂正してください")
                if "corrected_text" in changes:
                    after.update(corrected_by=actor, corrected_at=now())
                if changes.get("status") == "confirmed":
                    after.update(confirmed_by=actor, confirmed_at=now())
                elif changes.get("status") == "needs_review":
                    after.update(confirmed_by=None, confirmed_at=None)
            else:
                self._validate_sources(after["source_event_ids"])
                if changes.get("status") in ("open", "done"):
                    after["confirmed_by"] = actor
                if "status" in changes:
                    after["completed_at"] = now() if after["status"] == "done" else None
                after["updated_at"] = now()
            self._save(table, after, actor, before)
            return after

    def snapshot(self):
        with self.lock:
            return {
                "cursor": self.db.execute("SELECT COALESCE(MAX(seq),0) FROM outbox").fetchone()[0],
                "events": [
                    json.loads(r[0])
                    for r in self.db.execute("SELECT document FROM events ORDER BY received_at,id")
                ],
                "tasks": [
                    json.loads(r[0])
                    for r in self.db.execute("SELECT document FROM tasks ORDER BY created_at,id")
                ],
            }

    def since(self, cursor, limit=200):
        with self.lock:
            return [
                dict(r)
                for r in self.db.execute(
                    "SELECT seq,kind,payload FROM outbox WHERE seq>? ORDER BY seq LIMIT ?",
                    (cursor, limit),
                )
            ]

    def history(self, identifier):
        with self.lock:
            return [
                {
                    **dict(r),
                    "before": json.loads(r["before_json"] or "null"),
                    "after": json.loads(r["after_json"]),
                }
                for r in self.db.execute(
                    "SELECT * FROM history WHERE entity_id=? ORDER BY seq", (identifier,)
                )
            ]
