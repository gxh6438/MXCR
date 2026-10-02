"""
撤销/重做管理器 (v1.0.0 磁盘化重构)

v1.0.0 核心变更:
- 撤销/重做数据不再全量驻留内存, 而是溢出到磁盘 sqlite 文件 (undo_cache/ 目录)
- 内存中只保留操作元数据 (玩家/类型/时间/维度/描述/方块数/包围盒/实体快照)
- 提供流式写入接口 (open_action_writer) 供边放置边记录, 避免中间列表
- 提供按区块流式读取接口 (iter_chunks / iter_blocks), 恢复时一次只物化一个区块
- 丢弃记录时自动删除对应磁盘文件, 插件启动/关闭时清理残留缓存
"""
import json
import sqlite3
import threading
import time
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


@dataclass(slots=True)
class BlockRecord:
    """方块记录"""
    x: int
    y: int
    z: int
    block_type: str
    block_data: int = 0
    block_states: Dict[str, Any] = field(default_factory=dict)  # 方块状态（门朝向、楼梯方向等）
    nbt_data: Optional[str] = None  # 容器物品数据（JSON 字符串），None 表示非容器或无物品


class UndoAction:
    """
    撤销操作记录 (磁盘化)

    方块数据存储在磁盘 sqlite 文件中, 本对象只保留元数据。
    通过 iter_blocks() / iter_chunks() 流式读取, 或 load_all_blocks() 兼容旧代码。
    """

    __slots__ = (
        "player_name", "action_type", "timestamp", "dimension",
        "description", "entities", "block_count",
        "min_x", "min_y", "min_z", "max_x", "max_y", "max_z",
        "db_path", "_manager",
    )

    def __init__(self, player_name: str, action_type: str, timestamp: float,
                 dimension: str, description: str = "",
                 entities: Optional[list] = None,
                 block_count: int = 0,
                 bounds: Optional[tuple] = None,
                 db_path: Optional[Path] = None,
                 manager: "UndoManager" = None):
        self.player_name = player_name
        self.action_type = action_type
        self.timestamp = timestamp
        self.dimension = dimension
        self.description = description
        self.entities = entities if entities is not None else []
        self.block_count = block_count
        if bounds is not None:
            (self.min_x, self.min_y, self.min_z,
             self.max_x, self.max_y, self.max_z) = bounds
        else:
            self.min_x = self.min_y = self.min_z = 0
            self.max_x = self.max_y = self.max_z = 0
        self.db_path = db_path
        self._manager = manager

    # ---------- 数据读取 ----------

    def get_bounds(self) -> tuple:
        """获取包围盒 ((min_x, min_y, min_z), (max_x, max_y, max_z))"""
        return ((self.min_x, self.min_y, self.min_z),
                (self.max_x, self.max_y, self.max_z))

    def _connect(self) -> Optional[sqlite3.Connection]:
        if self.db_path is None or not Path(self.db_path).exists():
            return None
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_to_record(row) -> BlockRecord:
        states_raw = row["block_states"]
        if isinstance(states_raw, str) and states_raw:
            try:
                states = json.loads(states_raw)
            except Exception:
                states = {}
        else:
            states = {}
        return BlockRecord(
            x=row["x"], y=row["y"], z=row["z"],
            block_type=row["block_type"],
            block_data=0,
            block_states=states,
            nbt_data=row["nbt_data"],
        )

    def get_chunk_list(self) -> List[tuple]:
        """获取本操作涉及的所有区块坐标 [(chunk_x, chunk_z), ...]"""
        conn = self._connect()
        if conn is None:
            return []
        try:
            cursor = conn.execute(
                "SELECT DISTINCT chunk_x, chunk_z FROM blocks"
            )
            return [(r["chunk_x"], r["chunk_z"]) for r in cursor.fetchall()]
        except Exception:
            return []
        finally:
            conn.close()

    def get_chunk_blocks(self, chunk_x: int, chunk_z: int) -> List[BlockRecord]:
        """获取指定区块内的方块记录（一次只物化一个区块，控制内存）"""
        conn = self._connect()
        if conn is None:
            return []
        try:
            cursor = conn.execute(
                "SELECT x, y, z, block_type, block_states, nbt_data "
                "FROM blocks WHERE chunk_x = ? AND chunk_z = ?",
                (chunk_x, chunk_z),
            )
            return [self._row_to_record(r) for r in cursor.fetchall()]
        except Exception:
            return []
        finally:
            conn.close()

    def iter_blocks(self, batch_size: int = 4000) -> Iterator[List[BlockRecord]]:
        """分批流式读取全部方块记录（生成器，每次产出一批）"""
        conn = self._connect()
        if conn is None:
            return
        try:
            cursor = conn.execute(
                "SELECT x, y, z, block_type, block_states, nbt_data FROM blocks"
            )
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                yield [self._row_to_record(r) for r in rows]
        finally:
            conn.close()

    def load_all_blocks(self) -> List[BlockRecord]:
        """一次性载入全部方块记录（兼容旧代码，谨慎用于大型建筑）"""
        result = []
        for batch in self.iter_blocks():
            result.extend(batch)
        return result

    # 兼容旧代码中访问 action.blocks 的写法（返回全量列表, 仅小规模场景使用）
    @property
    def blocks(self) -> List[BlockRecord]:
        return self.load_all_blocks()

    # ---------- 资源清理 ----------

    def dispose(self) -> None:
        """删除磁盘数据文件"""
        try:
            if self.db_path is not None:
                p = Path(self.db_path)
                if p.exists():
                    p.unlink()
                # 清理可能的 wal/shm 文件
                for suffix in ("-wal", "-shm"):
                    side = Path(str(p) + suffix)
                    if side.exists():
                        side.unlink()
        except Exception:
            pass
        self.db_path = None


class UndoActionWriter:
    """
    撤销记录流式写入器

    用法:
        writer = undo_mgr.open_action_writer(player_name, "load", dim, desc)
        writer.add_block(record)          # 或 writer.add_blocks([...])
        writer.commit()                   # 完成并入栈
        writer.abort()                    # 放弃并删除文件
    """

    BUFFER_SIZE = 3000

    def __init__(self, manager: "UndoManager", player_name: str,
                 action_type: str, dimension: str, description: str = ""):
        self._manager = manager
        self._player_name = player_name
        self._action_type = action_type
        self._dimension = dimension
        self._description = description
        self._entities: list = []
        self._count = 0
        self._bounds = None  # [min_x, min_y, min_z, max_x, max_y, max_z]
        self._buffer: list = []
        self._committed = False
        self._aborted = False

        self._db_path = manager.cache_dir / f"undo_{_uuid.uuid4().hex}.db"
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode = MEMORY")
        self._conn.execute("PRAGMA synchronous = OFF")
        self._conn.execute("""
            CREATE TABLE blocks (
                chunk_x INTEGER NOT NULL,
                chunk_z INTEGER NOT NULL,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                z INTEGER NOT NULL,
                block_type TEXT NOT NULL,
                block_states TEXT,
                nbt_data TEXT
            )
        """)
        self._conn.execute(
            "CREATE INDEX idx_chunk ON blocks(chunk_x, chunk_z)"
        )
        self._conn.commit()

    @property
    def block_count(self) -> int:
        return self._count

    @property
    def db_path(self) -> Path:
        return self._db_path

    def set_entities(self, entities: list) -> None:
        """设置实体快照（数量少，保留内存）"""
        self._entities = entities if entities is not None else []

    def add_block(self, record: BlockRecord) -> None:
        """添加一条方块记录"""
        if self._committed or self._aborted:
            return
        x, y, z = record.x, record.y, record.z
        if self._bounds is None:
            self._bounds = [x, y, z, x, y, z]
        else:
            b = self._bounds
            if x < b[0]: b[0] = x
            if y < b[1]: b[1] = y
            if z < b[2]: b[2] = z
            if x > b[3]: b[3] = x
            if y > b[4]: b[4] = y
            if z > b[5]: b[5] = z

        if record.block_states:
            try:
                states_str = json.dumps(record.block_states)
            except Exception:
                states_str = None
        else:
            states_str = None

        self._buffer.append((
            x // 16,
            z // 16,
            x, y, z,
            record.block_type, states_str, record.nbt_data,
        ))
        self._count += 1
        if len(self._buffer) >= self.BUFFER_SIZE:
            self._flush()

    def add_blocks(self, records) -> None:
        """批量添加方块记录（list 或迭代器）"""
        for r in records:
            self.add_block(r)

    def _flush(self) -> None:
        if not self._buffer:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO blocks (chunk_x, chunk_z, x, y, z, "
                "block_type, block_states, nbt_data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                self._buffer,
            )
            self._conn.commit()
        self._buffer.clear()

    def _finalize(self) -> Optional[UndoAction]:
        """（内部）完成写盘并生成 UndoAction，不入栈"""
        if self._committed or self._aborted:
            return None
        self._flush()
        with self._lock:
            self._conn.commit()
            self._conn.close()
        self._committed = True

        if self._count == 0:
            # 无方块记录，直接清理文件
            self._cleanup_file()
            return None

        bounds = tuple(self._bounds) if self._bounds else None
        return UndoAction(
            player_name=self._player_name,
            action_type=self._action_type,
            timestamp=time.time(),
            dimension=self._dimension,
            description=self._description,
            entities=self._entities,
            block_count=self._count,
            bounds=bounds,
            db_path=self._db_path,
            manager=self._manager,
        )

    def commit(self) -> Optional[UndoAction]:
        """完成写入，生成 UndoAction 并推入撤销栈"""
        action = self._finalize()
        if action is not None:
            self._manager._push_new_action(action)
        return action

    def commit_detached(self) -> Optional[UndoAction]:
        """完成写入并返回 UndoAction，但不自动入栈（供快照场景自行 push）"""
        return self._finalize()

    def abort(self) -> None:
        """放弃写入，删除临时文件"""
        if self._committed or self._aborted:
            return
        self._aborted = True
        try:
            with self._lock:
                self._conn.close()
        except Exception:
            pass
        self._cleanup_file()

    def _cleanup_file(self) -> None:
        try:
            if self._db_path.exists():
                self._db_path.unlink()
        except Exception:
            pass


class UndoManager:
    """撤销/重做管理器 (磁盘化存储)"""

    CACHE_DIR_NAME = "undo_cache"

    def __init__(self, max_history: int = 10, data_dir: Optional[Path] = None):
        self.max_history = max_history
        # 每个玩家维护两个栈（栈内只有元数据对象，内存占用极小）
        self.undo_stacks: dict[str, list[UndoAction]] = {}
        self.redo_stacks: dict[str, list[UndoAction]] = {}

        if data_dir is None:
            import tempfile
            data_dir = Path(tempfile.gettempdir()) / "mxcr"
        self.cache_dir = Path(data_dir) / self.CACHE_DIR_NAME
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # 启动时清理上次运行的残留缓存文件
        self._clean_cache_dir()

    def _clean_cache_dir(self) -> None:
        """清理缓存目录中的所有残留 undo 文件"""
        try:
            for f in self.cache_dir.glob("undo_*.db*"):
                try:
                    f.unlink()
                except Exception:
                    pass
        except Exception:
            pass

    # ---------- 写入接口 ----------

    def open_action_writer(self, player_name: str, action_type: str,
                           dimension: str,
                           description: str = "") -> UndoActionWriter:
        """打开流式写入器（推荐：边操作边记录，不占内存）"""
        return UndoActionWriter(self, player_name, action_type,
                                dimension, description)

    def record_action(self, player_name: str, action_type: str,
                      blocks, dimension: str,
                      description: str = "",
                      entities: list = None) -> None:
        """
        记录一个可撤销的操作（兼容旧接口）。

        blocks 可以是 list[BlockRecord] 或任意可迭代对象，
        数据将写入磁盘而非驻留内存。
        """
        writer = self.open_action_writer(player_name, action_type,
                                         dimension, description)
        if entities:
            writer.set_entities(entities)
        try:
            writer.add_blocks(blocks)
            writer.commit()
        except Exception:
            writer.abort()
            raise

    def _push_new_action(self, action: UndoAction) -> None:
        """（内部）将新操作推入撤销栈，并清空重做栈"""
        player_name = action.player_name
        if player_name not in self.undo_stacks:
            self.undo_stacks[player_name] = []
            self.redo_stacks[player_name] = []

        self.undo_stacks[player_name].append(action)

        # 限制历史记录数量（丢弃时删除磁盘文件）
        while len(self.undo_stacks[player_name]) > self.max_history:
            removed = self.undo_stacks[player_name].pop(0)
            removed.dispose()

        # 执行新操作时清空重做栈
        for old in self.redo_stacks[player_name]:
            old.dispose()
        self.redo_stacks[player_name].clear()

    # ---------- 栈操作（接口与旧版一致） ----------

    def get_last_undo_action(self, player_name: str) -> Optional[UndoAction]:
        """获取最近的可撤销操作（不移除）"""
        stack = self.undo_stacks.get(player_name)
        if not stack:
            return None
        return stack[-1]

    def pop_undo_action(self, player_name: str) -> Optional[UndoAction]:
        """弹出最近的可撤销操作"""
        stack = self.undo_stacks.get(player_name)
        if not stack:
            return None
        return stack.pop()

    def push_redo_action(self, action: UndoAction) -> None:
        """将操作推入重做栈"""
        player_name = action.player_name
        if player_name not in self.redo_stacks:
            self.redo_stacks[player_name] = []

        self.redo_stacks[player_name].append(action)

        while len(self.redo_stacks[player_name]) > self.max_history:
            removed = self.redo_stacks[player_name].pop(0)
            removed.dispose()

    def get_last_redo_action(self, player_name: str) -> Optional[UndoAction]:
        """获取最近的可重做操作（不移除）"""
        stack = self.redo_stacks.get(player_name)
        if not stack:
            return None
        return stack[-1]

    def pop_redo_action(self, player_name: str) -> Optional[UndoAction]:
        """弹出最近的可重做操作"""
        stack = self.redo_stacks.get(player_name)
        if not stack:
            return None
        return stack.pop()

    def push_undo_action(self, action: UndoAction) -> None:
        """将操作推入撤销栈（撤销后重做时使用，不清空重做栈）"""
        player_name = action.player_name
        if player_name not in self.undo_stacks:
            self.undo_stacks[player_name] = []

        self.undo_stacks[player_name].append(action)

        while len(self.undo_stacks[player_name]) > self.max_history:
            removed = self.undo_stacks[player_name].pop(0)
            removed.dispose()

    # ---------- 查询接口 ----------

    def can_undo(self, player_name: str) -> bool:
        """检查玩家是否可以撤销"""
        return bool(self.undo_stacks.get(player_name))

    def can_redo(self, player_name: str) -> bool:
        """检查玩家是否可以重做"""
        return bool(self.redo_stacks.get(player_name))

    def get_undo_count(self, player_name: str) -> int:
        """获取可撤销操作数量"""
        return len(self.undo_stacks.get(player_name, []))

    def get_redo_count(self, player_name: str) -> int:
        """获取可重做操作数量"""
        return len(self.redo_stacks.get(player_name, []))

    def clear_player_history(self, player_name: str) -> None:
        """清除玩家的所有历史记录（同时删除磁盘文件）"""
        for action in self.undo_stacks.get(player_name, []):
            action.dispose()
        for action in self.redo_stacks.get(player_name, []):
            action.dispose()
        if player_name in self.undo_stacks:
            self.undo_stacks[player_name].clear()
        if player_name in self.redo_stacks:
            self.redo_stacks[player_name].clear()

    def shutdown(self) -> None:
        """插件关闭时清理全部缓存"""
        for stacks in (self.undo_stacks, self.redo_stacks):
            for actions in stacks.values():
                for action in actions:
                    action.dispose()
            stacks.clear()
        self._clean_cache_dir()
