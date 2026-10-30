"""T6：人工确认闭环、持久化与恢复。

流程
----
::

    商品 → 类目匹配 → PENDING（存任务 + 候选）
                          ↓ 人工选一个
                     confirm(task_id, target_cid)
                          ↓ 原子写 YAML + 更新任务状态
                     下次同 fingerprint 直接命中

三条硬约束（验收标准 8、9）
--------------------------
1. **YAML 写失败时原文件内容不变，任务仍是 pending。**
   做法：先写临时文件，成功后再 ``os.replace`` 原子替换。
   ``writer`` 可注入，便于测试失败路径。
2. **重复确认不产生重复记录。** 同一 fingerprint 只会有一条 confirmed 记录。
3. **过期候选、冲突确认要返回可观察的错误**，不能静默吞掉。

存储分工
--------
- SQLite（``data/app.db``）：任务表，记录 pending/confirmed 状态与候选快照
- YAML（``data/mapping/category_map.yaml``）：最终映射表，人工可读可改
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import yaml

from ..core.config import get_settings
from .category_models import (CategoryCandidate, MappingError, MappingStatus,
                              normalize_text)

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS mapping_tasks (
  task_id TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL,
  item_id TEXT,
  platform TEXT,
  target_platform TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  target_cid TEXT,
  target_path TEXT,
  candidates TEXT,
  detail TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mt_fp ON mapping_tasks(fingerprint);
CREATE INDEX IF NOT EXISTS idx_mt_status ON mapping_tasks(status);
"""

# 任务状态
ST_PENDING = "pending"
ST_CONFIRMED = "confirmed"
ST_REJECTED = "rejected"
ST_EXPIRED = "expired"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class MappingStoreError(RuntimeError):
    """映射表读写失败。"""

    def __init__(self, code: MappingError, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")


# ================================================================ 跨进程锁


@contextmanager
def file_lock(lock_path: Path, timeout: float = 15.0, poll: float = 0.05):
    """跨进程互斥锁（基于 O_CREAT|O_EXCL 自旋）。

    复审 blocker：两个进程并发确认不同指纹时，后写入者会覆盖先写入者的
    「读-改-写」结果，导致已确认映射丢失。线程锁 ``_lock`` 只挡同进程，
    这里再包一层文件锁挡住其他进程。
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout
    fd = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.time() >= deadline:
                raise MappingStoreError(
                    MappingError.MAPPING_STORE_WRITE_FAILED,
                    f"获取映射表文件锁超时（{lock_path.name}），可能有其他进程正在写入")
            time.sleep(poll)
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


# ================================================================ YAML 原子写


def atomic_write_yaml(path: Path, data: dict,
                      writer: Callable[[Path, str], None] | None = None) -> None:
    """原子写 YAML：写临时文件 → os.replace。

    ``writer`` 可注入，签名 ``(tmp_path, text) -> None``；抛异常时原文件保持不变。
    默认 writer 用 ``Path.write_text``。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=True, default_flow_style=False)

    fd, tmp_name = tempfile.mkstemp(prefix=".map_", suffix=".yaml.tmp",
                                    dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        if writer:
            writer(tmp, text)
        else:
            tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)          # 原子替换：要么全成功，要么原文件不变
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ================================================================ 任务模型


class MappingTask:
    def __init__(self, row: dict):
        self.task_id = row["task_id"]
        self.fingerprint = row["fingerprint"]
        self.item_id = row.get("item_id") or ""
        self.platform = row.get("platform") or ""
        self.target_platform = row.get("target_platform") or ""
        self.status = row.get("status") or ST_PENDING
        self.target_cid = row.get("target_cid")
        self.target_path = json.loads(row["target_path"]) if row.get("target_path") else []
        self.candidates = [CategoryCandidate(**c) for c in
                           (json.loads(row["candidates"]) if row.get("candidates") else [])]
        self.detail = row.get("detail") or ""
        self.created_at = row.get("created_at") or ""
        self.updated_at = row.get("updated_at") or ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "fingerprint": self.fingerprint,
            "item_id": self.item_id,
            "platform": self.platform,
            "target_platform": self.target_platform,
            "status": self.status,
            "target_cid": self.target_cid,
            "target_path": self.target_path,
            "candidates": [c.model_dump() for c in self.candidates],
            "detail": self.detail,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ================================================================ 存储


class MappingTaskStore:
    """任务表（SQLite）+ 映射表（YAML）。

    同时实现 :class:`src.normalize.category_match.ConfirmedMappingStore` 协议，
    可以直接传给 ``CategoryResolver(store=...)``。
    """

    def __init__(self, db_path: str | Path | None = None,
                 map_file: str | Path | None = None,
                 writer: Callable[[Path, str], None] | None = None):
        s = get_settings()
        self.db_path = Path(db_path) if db_path else s.db_path
        self.map_file = Path(map_file) if map_file else s.mapping_dir / "category_map.yaml"
        self.writer = writer
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------ 挂起
    def submit_pending(self, fingerprint: str, item_id: str,
                       platform: str, target_platform: str,
                       candidates: list[CategoryCandidate],
                       detail: str = "") -> MappingTask:
        """记录一次待确认。同一 fingerprint 已有 pending 任务时复用，不重复插入。"""
        with _lock, self._conn() as c:
            row = c.execute(
                "SELECT * FROM mapping_tasks WHERE fingerprint=? AND status=? "
                "ORDER BY created_at DESC LIMIT 1",
                (fingerprint, ST_PENDING)).fetchone()
            if row:
                return MappingTask(dict(row))

            task_id = uuid.uuid4().hex[:12]
            ts = _now()
            c.execute(
                """INSERT INTO mapping_tasks(task_id, fingerprint, item_id, platform,
                     target_platform, status, candidates, detail, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (task_id, fingerprint, item_id, platform, target_platform,
                 ST_PENDING,
                 json.dumps([x.model_dump() for x in candidates], ensure_ascii=False),
                 detail, ts, ts))
            row = c.execute("SELECT * FROM mapping_tasks WHERE task_id=?",
                            (task_id,)).fetchone()
        return MappingTask(dict(row))

    # ------------------------------------------------------------ 查询
    def get_task(self, task_id: str) -> MappingTask | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM mapping_tasks WHERE task_id=?",
                            (task_id,)).fetchone()
        return MappingTask(dict(row)) if row else None

    def list_pending(self, limit: int = 100) -> list[MappingTask]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM mapping_tasks WHERE status=? ORDER BY created_at LIMIT ?",
                (ST_PENDING, limit)).fetchall()
        return [MappingTask(dict(r)) for r in rows]

    def list_by_status(self, status: str, limit: int = 100) -> list[MappingTask]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM mapping_tasks WHERE status=? ORDER BY created_at LIMIT ?",
                (status, limit)).fetchall()
        return [MappingTask(dict(r)) for r in rows]

    # ------------------------------------------------------------ 确认
    def confirm(self, task_id: str, target_cid: str,
                target_path: list[str] | None = None) -> tuple[bool, str]:
        """确认一个 pending 任务，写入 YAML 映射表。

        返回 ``(成功, 说明)``。失败原因：任务不存在 / 状态不对 / 目标为空 /
        YAML 写入失败 / 冲突。
        """
        task = self.get_task(task_id)
        if task is None:
            return False, "任务不存在"
        if task.status != ST_PENDING:
            return False, f"任务状态是 {task.status}，只有 pending 能确认"
        cid = str(target_cid or "").strip()
        if not cid:
            return False, "target_cid 不能为空"

        # 候选校验：如果给了候选列表，确认的目标必须在内（防止确认过期候选）
        if task.candidates and not any(c.cid == cid for c in task.candidates):
            return False, (f"目标 {cid} 不在该任务的候选列表里"
                           f"（候选: {[c.cid for c in task.candidates]}）")

        # 复审 blocker：同一指纹已确认成别的目标时必须拒绝，不能直接覆盖。
        # 覆盖会让后续同指纹商品悄悄换到另一个类目。
        conflict = self.detect_conflict(task.fingerprint, cid)
        if conflict:
            return False, conflict

        path = list(target_path or [])
        if not path:
            for c in task.candidates:
                if c.cid == cid:
                    path = list(c.path)
                    break

        # 写 YAML（原子；失败则原文件不变）
        try:
            self._write_mapping(task, cid, path)
        except Exception as e:
            return False, f"{MappingError.MAPPING_STORE_WRITE_FAILED.value}: {e}"

        with _lock, self._conn() as c:
            c.execute(
                "UPDATE mapping_tasks SET status=?, target_cid=?, target_path=?, "
                "updated_at=? WHERE task_id=?",
                (ST_CONFIRMED, cid, json.dumps(path, ensure_ascii=False),
                 _now(), task_id))
        return True, f"已确认 {cid}"

    def _write_mapping(self, task: MappingTask, target_cid: str,
                       target_path: list[str]) -> None:
        # 跨进程锁包住「读最新文件 → 冲突检查 → 原子替换」全过程
        with file_lock(self.map_file.with_suffix(".lock")):
            self._write_mapping_locked(task, target_cid, target_path)

    def _write_mapping_locked(self, task: MappingTask, target_cid: str,
                              target_path: list[str]) -> None:
        """把确认结果写进 YAML。同一 fingerprint 只保留一条，重复确认是覆盖。

        **复审 blocker：现有文件无法解析时必须中止，绝不用空字典覆盖。**
        否则一次确认就能把历史映射全清空。
        """
        data: dict[str, Any] = {}
        if self.map_file.exists():
            raw_text = self.map_file.read_text(encoding="utf-8")
            if raw_text.strip():
                try:
                    loaded = yaml.safe_load(raw_text)
                except Exception as e:
                    raise MappingStoreError(
                        MappingError.MAPPING_STORE_WRITE_FAILED,
                        f"现有映射表无法解析，拒绝覆盖（{self.map_file.name}）: {e}") from e
                if not isinstance(loaded, dict):
                    raise MappingStoreError(
                        MappingError.MAPPING_STORE_WRITE_FAILED,
                        f"现有映射表顶层不是字典，拒绝覆盖（{self.map_file.name}）")
                data = loaded

        key = f"{task.platform}->{task.target_platform}"
        node = data.setdefault(key, {})
        by_fp = node.setdefault("_by_fingerprint", {})

        existing = by_fp.get(task.fingerprint)
        record = {
            "target_cid": target_cid,
            "target_path": target_path,
            "item_id": task.item_id,
            "confirmed_at": _now(),
        }
        if existing and existing.get("target_cid") != target_cid:
            # 覆盖是允许的（人工改主意），但要留痕
            record["previous_cid"] = existing.get("target_cid")
        by_fp[task.fingerprint] = record

        atomic_write_yaml(self.map_file, data, self.writer)

    # ------------------------------------------------------------ 拒绝
    def reject(self, task_id: str, reason: str = "") -> bool:
        with _lock, self._conn() as c:
            cur = c.execute(
                "UPDATE mapping_tasks SET status=?, detail=?, updated_at=? "
                "WHERE task_id=? AND status=?",
                (ST_REJECTED, reason, _now(), task_id, ST_PENDING))
            return cur.rowcount > 0

    def mark_expired(self, task_id: str, reason: str = "") -> bool:
        with _lock, self._conn() as c:
            cur = c.execute(
                "UPDATE mapping_tasks SET status=?, detail=?, updated_at=? WHERE task_id=?",
                (ST_EXPIRED, reason, _now(), task_id))
            return cur.rowcount > 0

    # ------------------------------------------------------------ 已确认映射
    def get(self, fingerprint: str) -> dict | None:
        """实现 ``ConfirmedMappingStore`` 协议：按指纹取已确认映射。

        注意：查任务请用 :meth:`get_task`，别和这个混了。
        """
        return self.get_confirmed(fingerprint)

    def get_confirmed(self, fingerprint: str) -> dict | None:
        if not self.map_file.exists():
            return None
        try:
            data = yaml.safe_load(self.map_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        for key, node in data.items():
            if not isinstance(node, dict):
                continue
            rec = (node.get("_by_fingerprint") or {}).get(fingerprint)
            if rec and rec.get("target_cid"):
                return {**rec, "mapping_key": key}
        return None

    def put(self, fingerprint: str, record: dict) -> None:
        with file_lock(self.map_file.with_suffix(".lock")):
            self._put_locked(fingerprint, record)

    def _put_locked(self, fingerprint: str, record: dict) -> None:
        """实现 ConfirmedMappingStore 协议：直接写一条已确认映射（绕过任务表）。

        与 :meth:`_write_mapping` 一样，现有文件解析失败时中止，不用空字典覆盖。
        """
        data: dict[str, Any] = {}
        if self.map_file.exists():
            raw_text = self.map_file.read_text(encoding="utf-8")
            if raw_text.strip():
                try:
                    loaded = yaml.safe_load(raw_text)
                except Exception as e:
                    raise MappingStoreError(
                        MappingError.MAPPING_STORE_WRITE_FAILED,
                        f"现有映射表无法解析，拒绝覆盖: {e}") from e
                if not isinstance(loaded, dict):
                    raise MappingStoreError(
                        MappingError.MAPPING_STORE_WRITE_FAILED,
                        "现有映射表顶层不是字典，拒绝覆盖")
                data = loaded
        key = record.get("mapping_key") or "taobao->taobao"
        node = data.setdefault(key, {})
        by_fp = node.setdefault("_by_fingerprint", {})
        by_fp[fingerprint] = {
            "target_cid": record.get("target_cid"),
            "target_path": record.get("target_path") or [],
            "item_id": record.get("item_id", ""),
            "confirmed_at": record.get("confirmed_at") or _now(),
        }
        atomic_write_yaml(self.map_file, data, self.writer)

    # ------------------------------------------------------------ 冲突检测
    def detect_conflict(self, fingerprint: str, target_cid: str) -> str | None:
        """同一指纹被确认成不同目标时返回说明，否则 None。"""
        rec = self.get_confirmed(fingerprint)
        if not rec:
            return None
        old = str(rec.get("target_cid") or "")
        if old and old != str(target_cid):
            return (f"{MappingError.MAPPING_CONFLICT.value}: "
                    f"指纹已确认为 {old}，现在要改成 {target_cid}")
        return None
