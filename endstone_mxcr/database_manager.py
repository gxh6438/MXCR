"""
数据库管理模块

文件结构:
data/
├── {UUID}_{玩家昵称}/
│   ├── mxcr_{唯一ID}.db       ← 每个建筑单独一个 db 文件
│   └── ...
├── __public__/                 ← 公开建筑文件夹
│   ├── mxcr_{唯一ID}.db
│   └── ...

每个 .db 文件中有三张表:
- metadata: 建筑元数据 (只有一行)
- blocks: 方块数据
- entities: 实体数据 (v1.0.0 新增)
"""
import sqlite3
import json
import re
import shutil
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from .structure_data import StructureMetadata, BlockInfo, EntityInfo, generate_structure_id


class StructureWriter:
    """
    建筑数据流式写入器 (v1.0.0 新增)

    用于分块保存路径：每扫完一个区块立即写入磁盘并释放内存，
    避免整个建筑的方块列表全程驻留内存。

    用法:
        writer = db_mgr.open_structure_writer(uuid, name, metadata)
        writer.add_blocks(chunk_blocks)   # 可多次调用，边扫边写
        writer.add_entities(entities)
        writer.finalize()                 # 完成，更新元数据计数
        writer.abort()                    # 放弃，删除半成品文件
    """

    BUFFER_SIZE = 3000

    def __init__(self, db_manager: "DatabaseManager", db_path: Path,
                 structure_id: str, metadata: StructureMetadata):
        self._db_mgr = db_manager
        self.db_path = db_path
        self.structure_id = structure_id
        self._metadata = metadata
        self._block_count = 0
        self._entity_count = 0
        self._buffer: list = []
        self._closed = False

        db_manager._init_structure_db(db_path)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")

        # 先插入元数据占位行（block_count/entity_count 在 finalize 时更新）
        self._conn.execute("""
            INSERT INTO metadata (structure_id, name, min_x, min_y, min_z,
                                  max_x, max_y, max_z, timestamp,
                                  player_uuid, player_name, dimension,
                                  is_public, block_count, entity_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
        """, (
            structure_id, metadata.name,
            metadata.min_x, metadata.min_y, metadata.min_z,
            metadata.max_x, metadata.max_y, metadata.max_z,
            metadata.timestamp, metadata.player_uuid,
            metadata.player_name, metadata.dimension,
            1 if metadata.is_public else 0,
        ))
        self._conn.commit()

    @property
    def block_count(self) -> int:
        return self._block_count

    def add_block(self, b: BlockInfo) -> None:
        """添加单个方块"""
        if self._closed:
            return
        if b.block_states and isinstance(b.block_states, dict) and len(b.block_states) > 0:
            try:
                states_str = json.dumps(b.block_states)
            except Exception:
                states_str = None
        else:
            states_str = None
        self._buffer.append((b.x, b.y, b.z, str(b.block_type), states_str, b.nbt_data))
        self._block_count += 1
        if len(self._buffer) >= self.BUFFER_SIZE:
            self._flush()

    def add_blocks(self, blocks) -> None:
        """批量添加方块（list 或迭代器），分批写入磁盘"""
        for b in blocks:
            self.add_block(b)

    def add_entities(self, entities: List[EntityInfo]) -> None:
        """添加实体数据"""
        if self._closed or not entities:
            return
        rows = []
        for e in entities:
            try:
                rows.append((json.dumps(e.to_dict(), ensure_ascii=False),))
            except Exception:
                pass
        if rows:
            self._conn.executemany(
                "INSERT INTO entities (entity_json) VALUES (?)", rows
            )
            self._conn.commit()
            self._entity_count += len(rows)

    def update_container_nbt(self, updates: List[tuple]) -> None:
        """
        批量更新容器方块的 NBT 数据。

        Args:
            updates: [(nbt_json_or_None, x, y, z), ...]
        """
        if self._closed or not updates:
            return
        self._flush()
        self._conn.executemany(
            "UPDATE blocks SET nbt_data = ? WHERE x = ? AND y = ? AND z = ?",
            updates,
        )
        self._conn.commit()

    def _flush(self) -> None:
        if not self._buffer:
            return
        self._conn.executemany("""
            INSERT INTO blocks (x, y, z, block_type, block_states, nbt_data)
            VALUES (?, ?, ?, ?, ?, ?)
        """, self._buffer)
        self._conn.commit()
        self._buffer.clear()

    def finalize(self) -> str:
        """完成写入，更新元数据计数，返回建筑 ID"""
        if self._closed:
            return self.structure_id
        self._flush()
        self._conn.execute(
            "UPDATE metadata SET block_count = ?, entity_count = ?",
            (self._block_count, self._entity_count),
        )
        self._conn.commit()
        self._conn.close()
        self._closed = True
        return self.structure_id

    def abort(self) -> None:
        """放弃写入，删除半成品数据库文件"""
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.close()
        except Exception:
            pass
        try:
            if self.db_path.exists():
                self.db_path.unlink()
            for suffix in ("-wal", "-shm"):
                side = Path(str(self.db_path) + suffix)
                if side.exists():
                    side.unlink()
        except Exception:
            pass


class DatabaseManager:
    """数据库管理器 - 每个玩家一个文件夹,每个建筑一个 db 文件"""

    PUBLIC_DIR_NAME = "__public__"

    def __init__(self, data_dir: Path):
        """初始化数据库管理器"""
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # 确保公开建筑文件夹存在
        (self.data_dir / self.PUBLIC_DIR_NAME).mkdir(parents=True, exist_ok=True)

    def _get_player_dir(self, player_uuid: str, player_name: str = "") -> Path:
        """获取玩家的数据文件夹路径"""
        safe_uuid = str(player_uuid).replace("-", "")
        safe_name = re.sub(r'[^\w\-]', '_', player_name) if player_name else ""
        target_dir_name = f"{safe_uuid}_{safe_name}" if safe_name else safe_uuid
        target_dir = self.data_dir / target_dir_name

        if target_dir.exists():
            return target_dir

        # 查找以该 UUID 开头的旧文件夹 (玩家可能改名)
        for existing in self.data_dir.iterdir():
            if existing.is_dir() and existing.name.startswith(safe_uuid):
                if existing.name != target_dir_name and safe_name:
                    try:
                        existing.rename(target_dir)
                        return target_dir
                    except Exception:
                        return existing
                return existing

        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    def _get_public_dir(self) -> Path:
        """获取公开建筑文件夹"""
        pub_dir = self.data_dir / self.PUBLIC_DIR_NAME
        pub_dir.mkdir(parents=True, exist_ok=True)
        return pub_dir

    def _get_db_path(self, player_uuid: str, structure_id: str,
                     player_name: str = "") -> Path:
        """获取建筑数据库文件路径"""
        player_dir = self._get_player_dir(player_uuid, player_name)
        return player_dir / f"mxcr_{structure_id}.db"

    def _get_connection(self, db_path: Path) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")  # WAL 模式提升写入性能
        return conn

    def _init_structure_db(self, db_path: Path):
        """初始化单个建筑的数据库表（含 entities 表）"""
        conn = self._get_connection(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                structure_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                min_x INTEGER NOT NULL,
                min_y INTEGER NOT NULL,
                min_z INTEGER NOT NULL,
                max_x INTEGER NOT NULL,
                max_y INTEGER NOT NULL,
                max_z INTEGER NOT NULL,
                timestamp INTEGER NOT NULL,
                player_uuid TEXT NOT NULL,
                player_name TEXT NOT NULL DEFAULT '',
                dimension TEXT NOT NULL,
                is_public INTEGER NOT NULL DEFAULT 0,
                block_count INTEGER NOT NULL DEFAULT 0,
                entity_count INTEGER NOT NULL DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blocks (
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                z INTEGER NOT NULL,
                block_type TEXT NOT NULL,
                block_states TEXT,
                nbt_data TEXT
            )
        """)

        # v1.0.0 新增：实体数据表
        # entity_json 存储 EntityInfo.to_dict() 序列化后的完整 JSON 字符串
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                entity_json TEXT NOT NULL
            )
        """)

        # v1.0.0: 坐标索引，支持按区块范围流式查询（加载时不再全量读入内存）
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_blocks_xz ON blocks(x, z)"
        )

        conn.commit()
        conn.close()

    def _migrate_db_if_needed(self, db_path: Path):
        """
        对旧版本数据库进行迁移，补充缺失的列和表。
        在读取操作前调用，确保兼容旧数据。
        """
        try:
            conn = self._get_connection(db_path)
            cursor = conn.cursor()

            # 检查 metadata 表是否缺少 entity_count 列
            cursor.execute("PRAGMA table_info(metadata)")
            columns = {row["name"] for row in cursor.fetchall()}
            if "entity_count" not in columns:
                cursor.execute(
                    "ALTER TABLE metadata ADD COLUMN entity_count INTEGER NOT NULL DEFAULT 0"
                )

            # 检查 entities 表是否存在
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='entities'"
            )
            if cursor.fetchone() is None:
                cursor.execute("""
                    CREATE TABLE entities (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        entity_json TEXT NOT NULL
                    )
                """)

            conn.commit()
            conn.close()
        except Exception:
            pass

    def _read_metadata_from_db(self, db_path: Path) -> Optional[StructureMetadata]:
        """从 db 文件读取元数据"""
        try:
            self._migrate_db_if_needed(db_path)
            conn = self._get_connection(db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT structure_id, name, min_x, min_y, min_z,
                       max_x, max_y, max_z, timestamp,
                       player_uuid, player_name, dimension,
                       is_public, block_count, entity_count
                FROM metadata LIMIT 1
            """)
            row = cursor.fetchone()
            conn.close()

            if row is None:
                return None

            return StructureMetadata(
                structure_id=row["structure_id"],
                name=row["name"],
                min_x=row["min_x"], min_y=row["min_y"], min_z=row["min_z"],
                max_x=row["max_x"], max_y=row["max_y"], max_z=row["max_z"],
                timestamp=row["timestamp"],
                player_uuid=row["player_uuid"],
                player_name=row["player_name"] if "player_name" in row.keys() else "",
                dimension=row["dimension"],
                is_public=bool(row["is_public"]) if "is_public" in row.keys() else False,
                block_count=row["block_count"] if "block_count" in row.keys() else 0,
                entity_count=row["entity_count"] if "entity_count" in row.keys() else 0,
            )
        except Exception:
            return None

    def init_player_dir(self, player_uuid: str, player_name: str = ""):
        """初始化玩家数据文件夹"""
        self._get_player_dir(player_uuid, player_name)

    # ========== v1.0.0 流式接口 ==========

    def resolve_db_path(self, structure_id: str, source_type: str = "player",
                        player_uuid: str = "", player_name: str = "",
                        source_dir: str = "") -> Optional[Path]:
        """解析建筑数据库文件路径（player/public/admin 三种来源）"""
        if source_type == "public":
            db_path = self._get_public_dir() / f"mxcr_{structure_id}.db"
        elif source_type == "admin" and source_dir:
            db_path = self.data_dir / source_dir / f"mxcr_{structure_id}.db"
        else:
            db_path = self._get_db_path(player_uuid, structure_id, player_name)
        return db_path if db_path.exists() else None

    def open_structure_writer(self, player_uuid: str, player_name: str,
                              metadata: StructureMetadata) -> StructureWriter:
        """打开建筑流式写入器（边扫描边写盘，内存只保留当前批次）"""
        structure_id = generate_structure_id()
        metadata.structure_id = structure_id
        metadata.player_name = player_name
        db_path = self._get_db_path(player_uuid, structure_id, player_name)
        return StructureWriter(self, db_path, structure_id, metadata)

    def ensure_xz_index(self, db_path: Path) -> None:
        """确保旧版数据库也有坐标索引（按区块查询提速）"""
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_blocks_xz ON blocks(x, z)"
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def get_blocks_in_range(self, db_path: Path,
                            x1: int, x2: int,
                            z1: int, z2: int) -> List[BlockInfo]:
        """
        按坐标范围查询方块（供分块加载使用，一次只物化一个区块的数据）。

        Args:
            db_path: 数据库文件路径
            x1, x2: 源坐标 X 范围（含边界）
            z1, z2: 源坐标 Z 范围（含边界）
        """
        if not Path(db_path).exists():
            return []
        try:
            conn = self._get_connection(db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT x, y, z, block_type, block_states, nbt_data FROM blocks "
                "WHERE x BETWEEN ? AND ? AND z BETWEEN ? AND ?",
                (x1, x2, z1, z2),
            )
            blocks = []
            for row in cursor.fetchall():
                block_states_raw = row["block_states"]
                if isinstance(block_states_raw, str):
                    try:
                        block_states = json.loads(block_states_raw)
                    except json.JSONDecodeError:
                        block_states = {}
                else:
                    block_states = {}
                blocks.append(BlockInfo(
                    x=row["x"], y=row["y"], z=row["z"],
                    block_type=row["block_type"],
                    block_states=block_states,
                    nbt_data=row["nbt_data"]
                ))
            conn.close()
            return blocks
        except Exception:
            return []

    def iter_structure_blocks(self, db_path: Path,
                              batch_size: int = 4000) -> Iterator[List[BlockInfo]]:
        """流式分批读取建筑的全部方块（生成器，每次产出一批）"""
        if not Path(db_path).exists():
            return
        conn = self._get_connection(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT x, y, z, block_type, block_states, nbt_data FROM blocks"
            )
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                batch = []
                for row in rows:
                    block_states_raw = row["block_states"]
                    if isinstance(block_states_raw, str):
                        try:
                            block_states = json.loads(block_states_raw)
                        except json.JSONDecodeError:
                            block_states = {}
                    else:
                        block_states = {}
                    batch.append(BlockInfo(
                        x=row["x"], y=row["y"], z=row["z"],
                        block_type=row["block_type"],
                        block_states=block_states,
                        nbt_data=row["nbt_data"]
                    ))
                yield batch
        finally:
            conn.close()

    def get_entities_from_path(self, db_path: Path) -> List[EntityInfo]:
        """从指定 db 文件读取实体数据"""
        if not Path(db_path).exists():
            return []
        self._migrate_db_if_needed(db_path)
        conn = self._get_connection(db_path)
        cursor = conn.cursor()
        entities = []
        try:
            cursor.execute("SELECT entity_json FROM entities")
            for row in cursor.fetchall():
                try:
                    data = json.loads(row["entity_json"])
                    entities.append(EntityInfo.from_dict(data))
                except Exception:
                    pass
        except Exception:
            pass
        conn.close()
        return entities

    # ========== 保存 ==========
    def save_structure(self, player_uuid: str, player_name: str,
                       metadata: StructureMetadata,
                       blocks: List[BlockInfo],
                       entities: Optional[List[EntityInfo]] = None) -> str:
        """
        保存建筑数据，返回建筑唯一 ID。

        Args:
            player_uuid: 玩家 UUID
            player_name: 玩家名称
            metadata: 建筑元数据
            blocks: 方块列表
            entities: 实体列表（可选，v1.0.0 新增）
        """
        structure_id = generate_structure_id()
        metadata.structure_id = structure_id
        metadata.player_name = player_name
        # v1.0.0: blocks 可为迭代器，block_count 写入完成后再统计回写
        metadata.entity_count = len(entities) if entities else 0

        db_path = self._get_db_path(player_uuid, structure_id, player_name)
        self._init_structure_db(db_path)

        conn = self._get_connection(db_path)
        conn.execute("PRAGMA synchronous = NORMAL")
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO metadata (structure_id, name, min_x, min_y, min_z,
                                      max_x, max_y, max_z, timestamp,
                                      player_uuid, player_name, dimension,
                                      is_public, block_count, entity_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                structure_id, metadata.name,
                metadata.min_x, metadata.min_y, metadata.min_z,
                metadata.max_x, metadata.max_y, metadata.max_z,
                metadata.timestamp, metadata.player_uuid,
                metadata.player_name, metadata.dimension,
                1 if metadata.is_public else 0, 0,
                metadata.entity_count
            ))

            # v1.0.0: 分批写入，避免构建整份 block_rows 副本（内存双份）
            written_count = 0
            batch = []
            for b in blocks:
                # 优化：空 block_states 存为 NULL，节省存储空间
                if b.block_states and isinstance(b.block_states, dict) and len(b.block_states) > 0:
                    states_str = json.dumps(b.block_states)
                else:
                    states_str = None
                batch.append((
                    b.x, b.y, b.z,
                    str(b.block_type), states_str, b.nbt_data
                ))
                written_count += 1
                if len(batch) >= 3000:
                    cursor.executemany("""
                        INSERT INTO blocks (x, y, z, block_type, block_states, nbt_data)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, batch)
                    batch.clear()
            if batch:
                cursor.executemany("""
                    INSERT INTO blocks (x, y, z, block_type, block_states, nbt_data)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, batch)
                batch.clear()

            # 回写真实方块数
            metadata.block_count = written_count
            cursor.execute(
                "UPDATE metadata SET block_count = ? WHERE structure_id = ?",
                (written_count, structure_id)
            )

            # 保存实体数据
            if entities:
                entity_rows = []
                for e in entities:
                    entity_rows.append((json.dumps(e.to_dict(), ensure_ascii=False),))
                cursor.executemany(
                    "INSERT INTO entities (entity_json) VALUES (?)",
                    entity_rows
                )

            conn.commit()
            # v1.0.0: 移除 VACUUM —— 新建数据库无碎片，大建筑 VACUUM 是全库重写，
            # 曾导致保存收尾时主线程长时间卡顿
            return structure_id

        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    # ========== 查询 ==========
    def get_structures(self, player_uuid: str,
                       player_name: str = "") -> List[StructureMetadata]:
        """获取玩家的所有建筑 (动态扫描文件夹)"""
        player_dir = self._get_player_dir(player_uuid, player_name)

        if not player_dir.exists():
            return []

        structures = []
        for db_file in player_dir.glob("mxcr_*.db"):
            meta = self._read_metadata_from_db(db_file)
            if meta is not None:
                structures.append(meta)

        structures.sort(key=lambda s: s.timestamp, reverse=True)
        return structures

    def get_structure_count(self, player_uuid: str,
                            player_name: str = "") -> int:
        """获取玩家的建筑数量"""
        player_dir = self._get_player_dir(player_uuid, player_name)
        if not player_dir.exists():
            return 0
        return len(list(player_dir.glob("mxcr_*.db")))

    def get_structure_blocks(self, player_uuid: str, structure_id: str,
                             player_name: str = "") -> List[BlockInfo]:
        """获取建筑的方块数据"""
        db_path = self._get_db_path(player_uuid, structure_id, player_name)

        if not db_path.exists():
            return []

        self._migrate_db_if_needed(db_path)
        conn = self._get_connection(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT x, y, z, block_type, block_states, nbt_data FROM blocks")

        blocks = []
        for row in cursor.fetchall():
            block_states_raw = row["block_states"]
            if isinstance(block_states_raw, str):
                try:
                    block_states = json.loads(block_states_raw)
                except json.JSONDecodeError:
                    block_states = {}
            else:
                block_states = {}

            blocks.append(BlockInfo(
                x=row["x"], y=row["y"], z=row["z"],
                block_type=row["block_type"],
                block_states=block_states,
                nbt_data=row["nbt_data"]
            ))

        conn.close()
        return blocks

    def get_structure_entities(self, player_uuid: str, structure_id: str,
                               player_name: str = "") -> List[EntityInfo]:
        """获取建筑的实体数据（v1.0.0 新增）"""
        db_path = self._get_db_path(player_uuid, structure_id, player_name)

        if not db_path.exists():
            return []

        self._migrate_db_if_needed(db_path)
        conn = self._get_connection(db_path)
        cursor = conn.cursor()

        entities = []
        try:
            cursor.execute("SELECT entity_json FROM entities")
            for row in cursor.fetchall():
                try:
                    data = json.loads(row["entity_json"])
                    entities.append(EntityInfo.from_dict(data))
                except Exception:
                    pass
        except Exception:
            pass

        conn.close()
        return entities

    def get_structure_metadata(self, player_uuid: str, structure_id: str,
                               player_name: str = "") -> Optional[StructureMetadata]:
        """获取建筑元数据"""
        db_path = self._get_db_path(player_uuid, structure_id, player_name)
        if not db_path.exists():
            return None
        return self._read_metadata_from_db(db_path)

    def delete_structure(self, player_uuid: str, structure_id: str,
                         player_name: str = ""):
        """删除建筑 (删除对应的 db 文件)"""
        db_path = self._get_db_path(player_uuid, structure_id, player_name)
        if db_path.exists():
            db_path.unlink()

    # ========== 公开建筑 ==========
    def publish_structure(self, player_uuid: str, structure_id: str,
                          player_name: str = "") -> bool:
        """公开建筑"""
        src_path = self._get_db_path(player_uuid, structure_id, player_name)
        if not src_path.exists():
            return False

        pub_dir = self._get_public_dir()
        dst_path = pub_dir / f"mxcr_{structure_id}.db"

        try:
            shutil.copy2(str(src_path), str(dst_path))

            # 更新公开标记
            conn = self._get_connection(dst_path)
            cursor = conn.cursor()
            try:
                cursor.execute("UPDATE metadata SET is_public = 1")
                conn.commit()
            except Exception:
                pass
            finally:
                conn.close()

            return True
        except Exception:
            return False

    def unpublish_structure(self, structure_id: str) -> bool:
        """取消公开建筑"""
        pub_dir = self._get_public_dir()
        db_path = pub_dir / f"mxcr_{structure_id}.db"
        if db_path.exists():
            db_path.unlink()
            return True
        return False

    def get_public_structures(self) -> List[StructureMetadata]:
        """获取所有公开建筑 (动态扫描)"""
        pub_dir = self._get_public_dir()
        structures = []
        for db_file in pub_dir.glob("mxcr_*.db"):
            meta = self._read_metadata_from_db(db_file)
            if meta is not None:
                meta.is_public = True
                structures.append(meta)

        structures.sort(key=lambda s: s.timestamp, reverse=True)
        return structures

    def get_public_structure_blocks(self, structure_id: str) -> List[BlockInfo]:
        """获取公开建筑的方块数据"""
        pub_dir = self._get_public_dir()
        db_path = pub_dir / f"mxcr_{structure_id}.db"
        if not db_path.exists():
            return []

        self._migrate_db_if_needed(db_path)
        conn = self._get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT x, y, z, block_type, block_states, nbt_data FROM blocks")

        blocks = []
        for row in cursor.fetchall():
            block_states_raw = row["block_states"]
            if isinstance(block_states_raw, str):
                try:
                    block_states = json.loads(block_states_raw)
                except json.JSONDecodeError:
                    block_states = {}
            else:
                block_states = {}
            blocks.append(BlockInfo(
                x=row["x"], y=row["y"], z=row["z"],
                block_type=row["block_type"],
                block_states=block_states,
                nbt_data=row["nbt_data"]
            ))
        conn.close()
        return blocks

    def get_public_structure_entities(self, structure_id: str) -> List[EntityInfo]:
        """获取公开建筑的实体数据（v1.0.0 新增）"""
        pub_dir = self._get_public_dir()
        db_path = pub_dir / f"mxcr_{structure_id}.db"
        if not db_path.exists():
            return []

        self._migrate_db_if_needed(db_path)
        conn = self._get_connection(db_path)
        cursor = conn.cursor()

        entities = []
        try:
            cursor.execute("SELECT entity_json FROM entities")
            for row in cursor.fetchall():
                try:
                    data = json.loads(row["entity_json"])
                    entities.append(EntityInfo.from_dict(data))
                except Exception:
                    pass
        except Exception:
            pass

        conn.close()
        return entities

    def get_public_structure_metadata(self, structure_id: str) -> Optional[StructureMetadata]:
        """获取公开建筑的元数据"""
        pub_dir = self._get_public_dir()
        db_path = pub_dir / f"mxcr_{structure_id}.db"
        if not db_path.exists():
            return None
        return self._read_metadata_from_db(db_path)

    # ========== 管理员功能 ==========
    def get_all_players(self) -> List[Tuple[str, str]]:
        """获取所有有数据的玩家列表,返回 [(dir_name, display_name), ...]"""
        players = []
        for d in sorted(self.data_dir.iterdir()):
            if d.is_dir() and d.name != self.PUBLIC_DIR_NAME:
                # 从文件夹名解析: {UUID}_{Name}
                parts = d.name.split("_", 1)
                display = parts[1] if len(parts) > 1 else parts[0]
                players.append((d.name, display))
        return players

    def get_all_structures_for_player_dir(self, dir_name: str) -> List[StructureMetadata]:
        """管理员: 获取指定玩家文件夹下的所有建筑"""
        player_dir = self.data_dir / dir_name
        if not player_dir.exists():
            return []

        structures = []
        for db_file in player_dir.glob("mxcr_*.db"):
            meta = self._read_metadata_from_db(db_file)
            if meta is not None:
                structures.append(meta)

        structures.sort(key=lambda s: s.timestamp, reverse=True)
        return structures

    def get_blocks_from_dir(self, dir_name: str, structure_id: str) -> List[BlockInfo]:
        """管理员: 从指定文件夹获取方块数据"""
        db_path = self.data_dir / dir_name / f"mxcr_{structure_id}.db"
        if not db_path.exists():
            return []

        self._migrate_db_if_needed(db_path)
        conn = self._get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT x, y, z, block_type, block_states, nbt_data FROM blocks")

        blocks = []
        for row in cursor.fetchall():
            block_states_raw = row["block_states"]
            if isinstance(block_states_raw, str):
                try:
                    block_states = json.loads(block_states_raw)
                except json.JSONDecodeError:
                    block_states = {}
            else:
                block_states = {}
            blocks.append(BlockInfo(
                x=row["x"], y=row["y"], z=row["z"],
                block_type=row["block_type"],
                block_states=block_states,
                nbt_data=row["nbt_data"]
            ))
        conn.close()
        return blocks

    def get_entities_from_dir(self, dir_name: str, structure_id: str) -> List[EntityInfo]:
        """管理员: 从指定文件夹获取实体数据（v1.0.0 新增）"""
        db_path = self.data_dir / dir_name / f"mxcr_{structure_id}.db"
        if not db_path.exists():
            return []

        self._migrate_db_if_needed(db_path)
        conn = self._get_connection(db_path)
        cursor = conn.cursor()

        entities = []
        try:
            cursor.execute("SELECT entity_json FROM entities")
            for row in cursor.fetchall():
                try:
                    data = json.loads(row["entity_json"])
                    entities.append(EntityInfo.from_dict(data))
                except Exception:
                    pass
        except Exception:
            pass

        conn.close()
        return entities

    def get_metadata_from_dir(self, dir_name: str,
                               structure_id: str) -> Optional[StructureMetadata]:
        """管理员: 从指定文件夹获取元数据"""
        db_path = self.data_dir / dir_name / f"mxcr_{structure_id}.db"
        if not db_path.exists():
            return None
        return self._read_metadata_from_db(db_path)

    def delete_structure_from_dir(self, dir_name: str, structure_id: str):
        """管理员: 从指定文件夹删除建筑"""
        db_path = self.data_dir / dir_name / f"mxcr_{structure_id}.db"
        if db_path.exists():
            db_path.unlink()

    def import_structure_to_player(self, source_dir: str, structure_id: str,
                                    target_uuid: str,
                                    target_name: str) -> Optional[str]:
        """管理员: 将建筑导入到目标玩家的文件夹"""
        src_path = self.data_dir / source_dir / f"mxcr_{structure_id}.db"
        if not src_path.exists():
            return None

        new_id = generate_structure_id()
        target_dir = self._get_player_dir(target_uuid, target_name)
        dst_path = target_dir / f"mxcr_{new_id}.db"

        try:
            shutil.copy2(str(src_path), str(dst_path))
            # 更新新文件中的 structure_id
            conn = self._get_connection(dst_path)
            cursor = conn.cursor()
            cursor.execute("UPDATE metadata SET structure_id = ?", (new_id,))
            conn.commit()
            conn.close()
            return new_id
        except Exception:
            return None

    def publish_from_dir(self, dir_name: str, structure_id: str) -> bool:
        """管理员: 将指定文件夹的建筑公开"""
        src_path = self.data_dir / dir_name / f"mxcr_{structure_id}.db"
        if not src_path.exists():
            return False

        pub_dir = self._get_public_dir()
        dst_path = pub_dir / f"mxcr_{structure_id}.db"

        try:
            shutil.copy2(str(src_path), str(dst_path))
            conn = self._get_connection(dst_path)
            cursor = conn.cursor()
            try:
                cursor.execute("UPDATE metadata SET is_public = 1")
                conn.commit()
            except Exception:
                pass
            finally:
                conn.close()
            return True
        except Exception:
            return False
