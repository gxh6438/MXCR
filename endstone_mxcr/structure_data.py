"""
建筑数据结构定义模块
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import json
import uuid


def generate_structure_id() -> str:
    """生成建筑唯一ID (8位短UUID)"""
    return uuid.uuid4().hex[:8]


@dataclass(slots=True)
class BlockInfo:
    """方块数据"""
    x: int
    y: int
    z: int
    block_type: str
    block_states: Dict[str, Any] = field(default_factory=dict)
    nbt_data: Optional[str] = None  # JSON 序列化的容器物品数据（通过 SAPI 桥接获取）

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "block_type": self.block_type,
            "block_states": json.dumps(self.block_states),
            "nbt_data": self.nbt_data
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BlockInfo":
        """从字典创建"""
        block_states_raw = data.get("block_states", "{}")
        if isinstance(block_states_raw, str):
            block_states = json.loads(block_states_raw)
        else:
            block_states = block_states_raw
        return cls(
            x=data["x"],
            y=data["y"],
            z=data["z"],
            block_type=data["block_type"],
            block_states=block_states,
            nbt_data=data.get("nbt_data")
        )


@dataclass(slots=True)
class EntityInfo:
    """
    实体数据（保存/恢复时使用）

    Endstone 侧字段（直接读写）:
        type_id, rel_x/y/z, yaw, pitch,
        name_tag, score_tag, scoreboard_tags,
        is_name_tag_visible, is_name_tag_always_visible,
        health, max_health

    SAPI 侧字段（通过桥接补充）:
        sapi_data: 包含 equipment/effects/color/color2/properties 的字典

    辅助字段:
        runtime_id: 实体运行时 ID（用于 SAPI 侧匹配，不持久化）
    """
    type_id: str                                  # 实体类型 ID，如 "minecraft:sheep"
    rel_x: float                                  # 相对于选区 min_pos 的 X 偏移
    rel_y: float                                  # 相对于选区 min_pos 的 Y 偏移
    rel_z: float                                  # 相对于选区 min_pos 的 Z 偏移
    yaw: float = 0.0                              # 水平朝向角
    pitch: float = 0.0                            # 垂直朝向角
    name_tag: str = ""                            # 自定义命名牌
    score_tag: str = ""                           # 计分板标签
    scoreboard_tags: List[str] = field(default_factory=list)  # scoreboard 标签列表
    is_name_tag_visible: bool = True              # 命名牌是否可见
    is_name_tag_always_visible: bool = False      # 命名牌是否始终可见
    health: Optional[float] = None               # 当前生命值（None 表示不可读/不恢复）
    max_health: Optional[float] = None           # 最大生命值
    sapi_data: Optional[Dict[str, Any]] = None   # SAPI 侧扩展数据（装备/药水/颜色/属性）
    runtime_id: Optional[str] = None             # 运行时 ID（仅用于 SAPI 匹配，不持久化）

    def to_dict(self) -> Dict[str, Any]:
        """
        序列化为可持久化的字典（不包含 runtime_id）。
        优化：跳过默认值字段，减小 JSON 体积。
        """
        d: Dict[str, Any] = {
            "type_id": self.type_id,
            "rel_x": round(self.rel_x, 4),
            "rel_y": round(self.rel_y, 4),
            "rel_z": round(self.rel_z, 4),
        }
        # 仅在非默认值时写入，节省存储空间
        if self.yaw != 0.0:
            d["yaw"] = round(self.yaw, 2)
        if self.pitch != 0.0:
            d["pitch"] = round(self.pitch, 2)
        if self.name_tag:
            d["name_tag"] = self.name_tag
        if self.score_tag:
            d["score_tag"] = self.score_tag
        if self.scoreboard_tags:
            d["scoreboard_tags"] = self.scoreboard_tags
        if not self.is_name_tag_visible:
            d["is_name_tag_visible"] = False
        if self.is_name_tag_always_visible:
            d["is_name_tag_always_visible"] = True
        if self.health is not None:
            d["health"] = self.health
        if self.max_health is not None:
            d["max_health"] = self.max_health
        if self.sapi_data is not None:
            # 清理 sapi_data 中的空值，进一步减小体积
            cleaned = {}
            for k, v in self.sapi_data.items():
                if v is None:
                    continue
                if isinstance(v, list) and len(v) == 0:
                    continue
                if isinstance(v, dict) and len(v) == 0:
                    continue
                cleaned[k] = v
            if cleaned:
                d["sapi_data"] = cleaned
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EntityInfo":
        """从持久化字典反序列化"""
        return cls(
            type_id=data["type_id"],
            rel_x=float(data.get("rel_x", 0.0)),
            rel_y=float(data.get("rel_y", 0.0)),
            rel_z=float(data.get("rel_z", 0.0)),
            yaw=float(data.get("yaw", 0.0)),
            pitch=float(data.get("pitch", 0.0)),
            name_tag=data.get("name_tag", ""),
            score_tag=data.get("score_tag", ""),
            scoreboard_tags=list(data.get("scoreboard_tags", [])),
            is_name_tag_visible=bool(data.get("is_name_tag_visible", True)),
            is_name_tag_always_visible=bool(data.get("is_name_tag_always_visible", False)),
            health=data.get("health"),
            max_health=data.get("max_health"),
            sapi_data=data.get("sapi_data"),
            runtime_id=None,  # 不从持久化数据中恢复 runtime_id
        )


@dataclass(slots=True)
class StructureMetadata:
    """建筑元数据"""
    structure_id: Optional[str]  # 唯一ID字符串,如 "a1b2c3d4"
    name: str
    min_x: int
    min_y: int
    min_z: int
    max_x: int
    max_y: int
    max_z: int
    timestamp: int
    player_uuid: str
    player_name: str  # 新增: 保存时的玩家昵称
    dimension: str
    is_public: bool = False  # 新增: 是否公开
    block_count: int = 0  # 新增: 实际方块数量
    entity_count: int = 0  # 新增: 实体数量

    def get_size(self) -> tuple:
        """获取建筑尺寸"""
        return (
            self.max_x - self.min_x + 1,
            self.max_y - self.min_y + 1,
            self.max_z - self.min_z + 1
        )

    def get_volume(self) -> int:
        """获取建筑体积 (选区总体积)"""
        size = self.get_size()
        return size[0] * size[1] * size[2]


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
            max_pos[2] - min_pos[2] + 1
        )

    def get_volume(self) -> int:
        """获取选区体积"""
        size = self.get_size()
        return size[0] * size[1] * size[2]
