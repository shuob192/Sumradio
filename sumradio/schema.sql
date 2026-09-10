PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, received_at TEXT NOT NULL, document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_sources (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    event_id TEXT NOT NULL REFERENCES events(id),
    PRIMARY KEY(task_id, event_id)
);
CREATE TABLE IF NOT EXISTS history (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL, actor TEXT NOT NULL, changed_at TEXT NOT NULL,
    before_json TEXT, after_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL, payload TEXT NOT NULL
);
