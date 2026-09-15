"""One atomic snapshot is the commit point; Markdown/JSON exports are recoverable projections."""

import fcntl
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from threading import RLock

from .models import Run


class Conflict(ValueError):
    pass


def atomic_json(path: Path, value: dict | list) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.process_lock = (root / ".lock").open("a+")
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.process_lock.close()
            raise RuntimeError("同じ保存先のSumradioが既に起動しています") from None
        self.runs: dict[str, Run] = {}
        self.errors: list[str] = []
        for path in sorted((root / "runs").glob("*/state.json")):
            try:
                run = Run.model_validate_json(path.read_text())
                self.runs[run.id] = run
                self.project(run)
            except Exception as exc:
                self.errors.append(f"{path.parent.name}: {exc}")

    def close(self):
        self.process_lock.close()

    def run_dir(self, run_id: str) -> Path:
        if not run_id.startswith("run_") or not run_id[4:].isalnum():
            raise KeyError(run_id)
        return self.root / "runs" / run_id

    def add(self, run: Run) -> Run:
        with self.lock:
            self.save(run)
            return run.model_copy(deep=True)

    def get(self, run_id: str) -> Run:
        with self.lock:
            return self.runs[run_id].model_copy(deep=True)

    def all(self) -> list[Run]:
        with self.lock:
            return [r.model_copy(deep=True) for r in self.runs.values()]

    def change(self, run_id: str, change: Callable[[Run], None]) -> Run:
        with self.lock:
            run = self.get(run_id)
            change(run)
            run.version += 1
            self.save(run)
            return run.model_copy(deep=True)

    def save(self, run: Run):
        atomic_json(self.run_dir(run.id) / "state.json", run.model_dump(mode="json"))
        self.runs[run.id] = run
        try:
            self.project(run)
        except OSError as exc:
            self.errors.append(f"派生ファイルは再起動で再生成します: {exc}")

    def project(self, run: Run):
        directory = self.run_dir(run.id)
        for comm in run.communications.values():
            header = f"# 交信 {comm.id}\n\n実施回: {run.title} ({run.id})\n\n受信: {comm.received_at}\n\n"
            if run.region:
                header += f"対象地域（開始時の設定・交信原文ではない）: {run.region.label}\n\n"
            audio = f"[元音声](audio/{comm.original_audio})\n\n" if comm.original_audio else ""
            corrections = "".join(
                f"\n## 訂正 r{c.revision} · {c.at} · {c.actor}\n\n{c.text}\n"
                for c in comm.corrections
            )
            atomic_text(
                directory / f"{comm.id}.md",
                header + audio + "## 認識原文\n\n" + comm.original_text + "\n" + corrections,
            )
            atomic_json(directory / f"{comm.id}.json", comm.model_dump(mode="json"))
        atomic_json(
            directory / "tasks.json", [t.model_dump(mode="json") for t in run.tasks.values()]
        )


def check_version(entity, expected: int):
    if entity.version != expected:
        raise Conflict("別の更新が反映されています。最新の内容を確認してください。")
