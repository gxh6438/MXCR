"""
元数据存储 (v1.1.0)

底层迁移到 StructBridge 后，方块/实体数据由引擎序列化进 .mcstructure /
.bundle 文件，本插件只维护一份轻量索引：

- structures 表：建筑元数据（键名/归属/边界/公开状态）
- undo_entries 表：撤销/重做栈（快照键名 + 区域边界），重启后仍可用

文件本身位于 StructBridge 的结构库（plugins/structbridge/library/mystructure/），
删除走 structbridge.delete_structure()，由插件层调用（本模块不管文件）。
"""
import sqlite3
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .structure_data import StructureMetadata, UndoEntry


class MetadataStore:
    """建筑元数据 + 撤销栈存储（单文件 SQLite）。"""

    def __init__(self, data_dir: Path, max_undo_history: int = 10):
        self.db_path = Path(data_dir) / "structures.db"
        self.max_undo_history = max_undo_history
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS structures (
                    structure_id TEXT PRIMARY KEY,
                    sb_key       TEXT NOT NULL UNIQUE,
                    name         TEXT NOT NULL,
                    min_x INTEGER, min_y INTEGER, min_z INTEGER,
                    max_x INTEGER, max_y INTEGER, max_z INTEGER,
                    timestamp    INTEGER NOT NULL,
                    player_uuid  TEXT NOT NULL,
                    player_name  TEXT NOT NULL,
                    dimension    TEXT NOT NULL,
                    is_public    INTEGER NOT NULL DEFAULT 0,
                    is_bundle    INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_structures_owner ON structures(player_uuid)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_structures_public ON structures(is_public)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS undo_entries (
                    entry_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                    stack        TEXT NOT NULL,
                    player_name  TEXT NOT NULL,
                    restore_key  TEXT NOT NULL,
                    min_x INTEGER, min_y INTEGER, min_z INTEGER,
                    max_x INTEGER, max_y INTEGER, max_z INTEGER,
                    dimension    TEXT NOT NULL,
                    description  TEXT NOT NULL,
                    timestamp    INTEGER NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_undo_lookup "
                "ON undo_entries(stack, player_name, entry_id)")

    # ================================================================
    #  建筑
    # ================================================================

    @staticmethod
    def _row_to_meta(row) -> StructureMetadata:
        return StructureMetadata(
            structure_id=row["structure_id"],
            sb_key=row["sb_key"],
            name=row["name"],
            min_x=row["min_x"], min_y=row["min_y"], min_z=row["min_z"],
            max_x=row["max_x"], max_y=row["max_y"], max_z=row["max_z"],
            timestamp=row["timestamp"],
            player_uuid=row["player_uuid"],
            player_name=row["player_name"],
            dimension=row["dimension"],
            is_public=bool(row["is_public"]),
            is_bundle=bool(row["is_bundle"]),
        )

    def add_structure(self, meta: StructureMetadata) -> None:
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO structures (
                    structure_id, sb_key, name,
                    min_x, min_y, min_z, max_x, max_y, max_z,
                    timestamp, player_uuid, player_name, dimension,
                    is_public, is_bundle
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                meta.structure_id, meta.sb_key, meta.name,
                meta.min_x, meta.min_y, meta.min_z,
                meta.max_x, meta.max_y, meta.max_z,
                int(meta.timestamp), meta.player_uuid, meta.player_name,
                meta.dimension, 1 if meta.is_public else 0, 1 if meta.is_bundle else 0,
            ))

    def get_structure(self, structure_id: str) -> Optional[StructureMetadata]:
        """按 ID 查询（不限归属；权限由调用方校验）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM structures WHERE structure_id = ?",
                (structure_id,)
            ).fetchone()
        return self._row_to_meta(row) if row else None

    def get_owned_structure(self, player_uuid: str, player_name: str,
                            structure_id: str) -> Optional[StructureMetadata]:
        """按 ID + 归属查询（"我的建筑"路径）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM structures WHERE structure_id = ? AND player_uuid = ?",
                (structure_id, player_uuid)
            ).fetchone()
        return self._row_to_meta(row) if row else None

    def list_owned_structures(self, player_uuid: str) -> List[StructureMetadata]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM structures WHERE player_uuid = ? ORDER BY timestamp DESC",
                (player_uuid,)
            ).fetchall()
        return [self._row_to_meta(r) for r in rows]

    def list_public_structures(self) -> List[StructureMetadata]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM structures WHERE is_public = 1 ORDER BY timestamp DESC"
            ).fetchall()
        return [self._row_to_meta(r) for r in rows]

    def count_owned_structures(self, player_uuid: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM structures WHERE player_uuid = ?",
                (player_uuid,)
            ).fetchone()
        return int(row[0]) if row else 0

    def set_public(self, structure_id: str, is_public: bool) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE structures SET is_public = ? WHERE structure_id = ?",
                (1 if is_public else 0, structure_id)
            )
            return cur.rowcount > 0

    def transfer_ownership(self, structure_id: str, new_uuid: str,
                          new_name: str) -> bool:
        """管理员"转入我的建筑"：转移归属（文件不动，键名不变）。"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE structures SET player_uuid = ?, player_name = ? WHERE structure_id = ?",
                (new_uuid, new_name, structure_id)
            )
            return cur.rowcount > 0

    def delete_structure(self, structure_id: str) -> Optional[str]:
        """
        删除元数据记录。
        返回被删建筑的 sb_key（供调用方删除文件），不存在则返回 None。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT sb_key FROM structures WHERE structure_id = ?",
                (structure_id,)
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "DELETE FROM structures WHERE structure_id = ?", (structure_id,))
            # 引用该建筑的撤销记录一并作废（如搬迁模式的恢复键）
            conn.execute(
                "DELETE FROM undo_entries WHERE restore_key = ?", (row["sb_key"],))
            return str(row["sb_key"])

    def get_by_sb_key(self, sb_key: str) -> Optional[StructureMetadata]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM structures WHERE sb_key = ?", (sb_key,)
            ).fetchone()
        return self._row_to_meta(row) if row else None

    # ---------- 管理员浏览 ----------

    def all_owners(self) -> List[Tuple[str, str]]:
        """所有拥有建筑的玩家 [(uuid, 显示名), ...]。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT player_uuid, player_name, MAX(timestamp) AS t "
                "FROM structures GROUP BY player_uuid, player_name "
                "ORDER BY t DESC"
            ).fetchall()
        return [(str(r["player_uuid"]), str(r["player_name"])) for r in rows]

    def list_structures_of_owner(self, player_uuid: str) -> List[StructureMetadata]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM structures WHERE player_uuid = ? ORDER BY timestamp DESC",
                (player_uuid,)
            ).fetchall()
        return [self._row_to_meta(r) for r in rows]

    # ================================================================
    #  撤销 / 重做栈（持久化）
    # ================================================================

    @staticmethod
    def _row_to_entry(row) -> UndoEntry:
        return UndoEntry(
            entry_id=int(row["entry_id"]),
            restore_key=str(row["restore_key"]),
            bounds=(
                int(row["min_x"]), int(row["min_y"]), int(row["min_z"]),
                int(row["max_x"]), int(row["max_y"]), int(row["max_z"]),
            ),
            dimension=str(row["dimension"]),
            description=str(row["description"]),
            timestamp=int(row["timestamp"]),
        )

    def push_undo_entry(self, stack: str, player_name: str, entry_like) -> int:
        """压入撤销/重做栈（entry_like: 含 restore_key/bounds/dimension/description）。"""
        b = entry_like.bounds
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO undo_entries (
                    stack, player_name, restore_key,
                    min_x, min_y, min_z, max_x, max_y, max_z,
                    dimension, description, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                stack, player_name, entry_like.restore_key,
                b[0], b[1], b[2], b[3], b[4], b[5],
                entry_like.dimension, entry_like.description, int(time.time()),
            ))
            new_id = int(cur.lastrowid)
            # 超出容量：删最旧记录（按 entry_id 升序）
            rows = conn.execute(
                "SELECT entry_id, restore_key FROM undo_entries "
                "WHERE stack = ? AND player_name = ? ORDER BY entry_id",
                (stack, player_name)
            ).fetchall()
            excess = len(rows) - self.max_undo_history
            evicted_keys: List[str] = []
            for r in rows[:max(0, excess)]:
                conn.execute("DELETE FROM undo_entries WHERE entry_id = ?", (r["entry_id"],))
                evicted_keys.append(str(r["restore_key"]))
        # 淘汰的快照键名返回给调用方做文件 GC（在事务外执行避免重入）
        for key in evicted_keys:
            self.on_entry_evicted(key)
        return new_id

    def pop_undo_entry(self, stack: str, player_name: str) -> Optional[UndoEntry]:
        """弹出该栈最新一条记录（删除行并返回）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM undo_entries WHERE stack = ? AND player_name = ? "
                "ORDER BY entry_id DESC LIMIT 1",
                (stack, player_name)
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM undo_entries WHERE entry_id = ?", (row["entry_id"],))
        return self._row_to_entry(row)

    def has_undo_entry(self, stack: str, player_name: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM undo_entries WHERE stack = ? AND player_name = ? LIMIT 1",
                (stack, player_name)
            ).fetchone()
        return row is not None

    def snapshot_key_referenced(self, sb_key: str) -> bool:
        """该键名是否仍被任一撤销记录引用（GC 判断用）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM undo_entries WHERE restore_key = ? LIMIT 1",
                (sb_key,)
            ).fetchone()
        return row is not None

    # ---------- GC 钩子 ----------

    _gc_callback = None  # 由插件注入: fn(sb_key) 做文件删除

    def set_gc_callback(self, fn) -> None:
        self._gc_callback = fn

    def on_entry_evicted(self, restore_key: str) -> None:
        """
        记录被淘汰/删除后调用：若为快照键且无其他引用，则清理其文件。
        建筑键（mxcr_ 前缀）永不在这里删除。
        """
        from .structbridge_backend import is_snapshot_key
        if not is_snapshot_key(restore_key):
            return
        if self.snapshot_key_referenced(restore_key):
            return
        if self._gc_callback is not None:
            try:
                self._gc_callback(restore_key)
            except Exception:
                pass
