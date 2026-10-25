"""
建筑数据结构定义模块 (v1.1.0)

底层迁移到 StructBridge 后，方块/实体数据由引擎序列化进 .mcstructure 文件，
本模块只保留选区、建筑元数据和撤销栈条目三类轻量结构。
"""
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(slots=True)
class Selection:
    """玩家选区"""
    pos1: Optional[tuple] = None
    pos2: Optional[tuple] = None
    dimension_name: Optional[str] = None

    def is_complete(self) -> bool:
        """检查选区是否完整"""
        return self.pos1 is not None and self.pos2 is not None

    def get_bounds(self) -> tuple:
        """获取选区边界(最小点, 最大点)"""
        if not self.is_complete():
            raise ValueError("Selection is not complete")

        min_x = min(self.pos1[0], self.pos2[0])
        min_y = min(self.pos1[1], self.pos2[1])
        min_z = min(self.pos1[2], self.pos2[2])

        max_x = max(self.pos1[0], self.pos2[0])
        max_y = max(self.pos1[1], self.pos2[1])
        max_z = max(self.pos1[2], self.pos2[2])

        return (min_x, min_y, min_z), (max_x, max_y, max_z)

    def get_size(self) -> tuple:
        """获取选区尺寸"""
        min_pos, max_pos = self.get_bounds()
        return (
            max_pos[0] - min_pos[0] + 1,
            max_pos[1] - min_pos[1] + 1,
            max_pos[2] - min_pos[2] + 1,
        )

    def get_volume(self) -> int:
        """获取选区体积"""
        size = self.get_size()
        return size[0] * size[1] * size[2]


@dataclass(slots=True)
class StructureMetadata:
    """建筑元数据（实际数据在 StructBridge 管理的 .mcstructure/.bundle 文件中）"""
    structure_id: str          # 唯一 ID（如 "a1b2c3d4"）
    sb_key: str                # StructBridge 结构键名（如 "mxcr_a1b2c3d4"）
    name: str                  # 显示名称（可为中文，仅存数据库）
    min_x: int
    min_y: int
    min_z: int
    max_x: int
    max_y: int
    max_z: int
    timestamp: int
    player_uuid: str
    player_name: str
    dimension: str
    is_public: bool = False    # 是否公开
    is_bundle: bool = False    # 是否为大区域结构束

    def get_size(self) -> tuple:
        """获取建筑尺寸"""
        return (
            self.max_x - self.min_x + 1,
            self.max_y - self.min_y + 1,
            self.max_z - self.min_z + 1,
        )

    def get_volume(self) -> int:
        """获取建筑区域体积"""
        size = self.get_size()
        return size[0] * size[1] * size[2]


@dataclass(slots=True)
class UndoEntry:
    """
    撤销/重做栈条目 (v1.1.0 快照式)

    语义：把 restore_key 指向的结构放置回 bounds 区域（bounds.min 为锚点）。
    - 粘贴建筑前的预快照（撤销 = 贴回快照）
    - 搬迁清除前的原建筑（撤销 = 贴回建筑）
    - 每次撤销/重做执行时新捕获的当前状态（反向条目）
    """
    restore_key: str                  # 要放置回去的结构键名
    bounds: Tuple[int, int, int, int, int, int]  # (min_x, min_y, min_z, max_x, max_y, max_z)
    dimension: str
    description: str = ""
    timestamp: int = 0
    entry_id: int = 0                 # 数据库行 ID（push 后回填，仅调试用）
