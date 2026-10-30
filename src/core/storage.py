"""存储层：raw/items JSON 落盘 + SQLite 索引 + 任务队列。"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import get_settings
from .models import Item, PublishResult, RawItem

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  item_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'collect',
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
  item_id TEXT PRIMARY KEY,
  platform TEXT,
  source_url TEXT,
  title TEXT,
  price_min REAL,
  price_max REAL,
  category_path TEXT,
  json_path TEXT,
  collected_at TEXT
);
CREATE TABLE IF NOT EXISTS publish_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT, target TEXT, shop TEXT, mode TEXT,
  ok INTEGER, target_item_id TEXT, error_code TEXT, error_msg TEXT,
  request TEXT, response TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str, max_len: int = 120) -> str:
    """把任意字符串变成合法文件名。

    淘宝客推广链接、带 query 的 URL 直接当文件名会在 Windows 上炸
    （``:`` ``?`` ``/`` 都是非法字符），所以统一清洗。
    """
    s = _UNSAFE.sub("_", str(name)).strip(" .")
    if len(s) > max_len:
        s = s[:max_len]
    return s or "unnamed"


def connect() -> sqlite3.Connection:
    s = get_settings()
    conn = sqlite3.connect(s.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------- raw / item

def save_raw(raw: RawItem) -> Path:
    s = get_settings()
    d = s.raw_dir / raw.platform
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{safe_name(raw.item_id)}.json"
    p.write_text(raw.model_dump_json(indent=2), encoding="utf-8")
    return p


def save_item(item: Item) -> Path:
    s = get_settings()
    s.items_dir.mkdir(parents=True, exist_ok=True)
    p = s.items_dir / f"{safe_name(item.item_id)}.json"
    p.write_text(item.model_dump_json(indent=2), encoding="utf-8")

    with _lock, connect() as conn:
        conn.execute(
            """INSERT INTO items(item_id, platform, source_url, title, price_min, price_max,
                                 category_path, json_path, collected_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(item_id) DO UPDATE SET
                 title=excluded.title, price_min=excluded.price_min,
                 price_max=excluded.price_max, category_path=excluded.category_path,
                 json_path=excluded.json_path, collected_at=excluded.collected_at""",
            (item.item_id, item.platform, item.source_url, item.title,
             item.price.min, item.price.max, "/".join(item.category.path),
             str(p), item.collected_at),
        )
    return p


def load_item(item_id: str) -> Item | None:
    s = get_settings()
    p = s.items_dir / f"{safe_name(item_id)}.json"
    if not p.exists():
        return None
    return Item.model_validate_json(p.read_text(encoding="utf-8"))


def list_items(limit: int = 100) -> list[dict[str, Any]]:
    with _lock, connect() as conn:
        rows = conn.execute(
            "SELECT * FROM items ORDER BY collected_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- tasks

def enqueue(platform: str, item_id: str, kind: str = "collect") -> str:
    tid = uuid.uuid4().hex[:12]
    ts = _now()
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO tasks(id, platform, item_id, kind, status, created_at, updated_at)"
            " VALUES(?,?,?,?, 'pending', ?, ?)",
            (tid, platform, item_id, kind, ts, ts),
        )
    return tid


def claim_next() -> dict[str, Any] | None:
    with _lock, connect() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE status='pending' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE tasks SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
            (_now(), row["id"]),
        )
        return dict(row)


def finish_task(tid: str, ok: bool, error: str | None = None, max_attempts: int = 3) -> None:
    with _lock, connect() as conn:
        row = conn.execute("SELECT attempts FROM tasks WHERE id=?", (tid,)).fetchone()
        attempts = row["attempts"] if row else 0
        if ok:
            status = "done"
        elif attempts >= max_attempts:
            status = "failed"
        else:
            status = "pending"
        conn.execute(
            "UPDATE tasks SET status=?, error=?, updated_at=? WHERE id=?",
            (status, error, _now(), tid),
        )


# ---------------------------------------------------------------- publish log

def log_publish(item_id: str, target: str, shop: str, mode: str,
                result: PublishResult, request: dict | None = None) -> None:
    with _lock, connect() as conn:
        conn.execute(
            """INSERT INTO publish_logs(item_id, target, shop, mode, ok, target_item_id,
                                        error_code, error_msg, request, response, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (item_id, target, shop, mode, int(result.ok), result.target_item_id,
             result.error_code, result.error_msg,
             json.dumps(request, ensure_ascii=False) if request else None,
             json.dumps(result.raw_response, ensure_ascii=False), _now()),
        )
