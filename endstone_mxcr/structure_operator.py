"""
建筑操作模块
严格基于 Endstone API:
- endstone.block: Block, BlockData, BlockState
- endstone.level: Dimension, Location
- Dimension.get_block_at(x, y, z) -> Block
- Dimension.loaded_chunks -> list[Chunk]
- Dimension.actors -> list[Actor]
- Block.type -> str
- Block.data -> BlockData
- Block.set_type(type, apply_physics=True)
- Block.set_data(data, apply_physics=True)
- Block.capture_state() -> BlockState
- BlockData.block_states -> dict[str, bool | str | int]
- BlockState.type (可读写)
- BlockState.data (可读写)
- BlockState.update(force, apply_physics)
"""
import time
import json
from typing import List, Optional, Callable, Tuple

from .structure_data import Selection, StructureMetadata, BlockInfo, EntityInfo
from .database_manager import DatabaseManager
from .config_manager import ConfigManager
from .container_manager import ContainerManager


class StructureOperator:
    """建筑操作器"""

    def __init__(self, db_manager: DatabaseManager, config_manager: ConfigManager,
                 server=None):
        self.db_manager = db_manager
        self.config_manager = config_manager
        self.server = server  # Endstone Server 实例，用于 create_block_data
        self.container_manager: Optional[ContainerManager] = None  # 由插件在 on_enable 后注入
        self.entity_manager = None  # EntityManager 实例，由插件在 on_enable 后注入

    def copy_structure(
        self,
        dimension,  # endstone.level.Dimension
        selection: Selection,
        name: str,
        player_uuid_str: str,
        player_name: str,
        preserve_air: bool = True,
        excluded_blocks: Optional[List[str]] = None
    ) -> Tuple[StructureMetadata, List[BlockInfo], List[EntityInfo]]:
        """
        复制建筑（含方块和实体）

        Returns:
            (StructureMetadata, List[BlockInfo], List[EntityInfo])
            其中 EntityInfo 列表中的 sapi_data 字段为 None，
            需要调用方通过 EntityManager.request_read_sapi_data 异步补充。
        """
        if not selection.is_complete():
            raise ValueError("选区未完成")

        min_pos, max_pos = selection.get_bounds()
        excluded = excluded_blocks or []

        metadata = StructureMetadata(
            structure_id=None,
            name=name,
            min_x=min_pos[0], min_y=min_pos[1], min_z=min_pos[2],
            max_x=max_pos[0], max_y=max_pos[1], max_z=max_pos[2],
            timestamp=int(time.time()),
            player_uuid=player_uuid_str,
            player_name=player_name,
            dimension=dimension.name
        )

        blocks = []

        for x in range(min_pos[0], max_pos[0] + 1):
            for y in range(min_pos[1], max_pos[1] + 1):
                for z in range(min_pos[2], max_pos[2] + 1):
                    try:
                        block = dimension.get_block_at(x, y, z)
                        block_type = block.type

                        # 跳过空气方块
                        if not preserve_air and block_type == "minecraft:air":
                            continue

                        # 跳过排除的方块
                        if block_type in excluded:
                            continue

                        # 获取方块状态
                        block_data = block.data
                        states = dict(block_data.block_states) if block_data.block_states else {}

                        # 如果是容器方块，标记为待读取容器数据（实际读取由调用方异步完成）
                        nbt_str = None
                        if ContainerManager.is_container_block(block_type):
                            # 标记为容器方块，调用方需要异步请求容器数据
                            nbt_str = "__CONTAINER__"  # 占位符，实际数据由异步回调填充

                        blocks.append(BlockInfo(
                            x=x, y=y, z=z,
                            block_type=block_type,
                            block_states=states,
                            nbt_data=nbt_str
                        ))
                    except Exception:
                        pass

        metadata.block_count = len(blocks)

        # 扫描选区内的实体（通过 EntityManager 的 Endstone 侧读取）
        entities: List[EntityInfo] = []
        if self.entity_manager is not None:
            try:
                entities = self.entity_manager.scan_entities_in_selection(
                    dimension, min_pos, max_pos
                )
            except Exception as e:
                # 实体扫描失败不影响方块复制
                if self.server:
                    pass  # logger 在 plugin 层处理

        metadata.entity_count = len(entities)

        return metadata, blocks, entities

    def paste_structure(
        self,
        dimension,  # endstone.level.Dimension
        player_uuid_str: str,
        player_name: str,
        structure_id: str,
        target_pos: tuple,
        source_type: str = "player",
        source_dir: str = ""
    ) -> int:
        """粘贴建筑（仅方块，不含实体）

        实体的粘贴由 mxcr_plugin.py 中的 load_structure_for_player 流程处理。

        Args:
            source_type: "player" / "public" / "admin"
            source_dir: 管理员模式下的源文件夹名
        """
        if source_type == "public":
            metadata = self.db_manager.get_public_structure_metadata(structure_id)
            blocks = self.db_manager.get_public_structure_blocks(structure_id)
        elif source_type == "admin" and source_dir:
            metadata = self.db_manager.get_metadata_from_dir(source_dir, structure_id)
            blocks = self.db_manager.get_blocks_from_dir(source_dir, structure_id)
        else:
            metadata = self.db_manager.get_structure_metadata(
                player_uuid_str, structure_id, player_name
            )
            blocks = self.db_manager.get_structure_blocks(
                player_uuid_str, structure_id, player_name
            )

        if metadata is None:
            raise ValueError(f"建筑 {structure_id} 不存在")

        # 计算偏移量
        offset_x = target_pos[0] - metadata.min_x
        offset_y = target_pos[1] - metadata.min_y
        offset_z = target_pos[2] - metadata.min_z

        placed_count = 0
        for b in blocks:
            new_x = b.x + offset_x
            new_y = b.y + offset_y
            new_z = b.z + offset_z

            try:
                block = dimension.get_block_at(new_x, new_y, new_z)

                # 使用 create_block_data 正确恢复方块类型和状态
                if b.block_states and self.server:
                    try:
                        block_data = self.server.create_block_data(
                            b.block_type, b.block_states
                        )
                        block.set_data(block_data, False)
                    except Exception:
                        # 回退: 如果 create_block_data 失败，至少设置方块类型
                        block.set_type(b.block_type, False)
                else:
                    block.set_type(b.block_type, False)

                placed_count += 1
            except Exception:
                pass

        return placed_count

    def clear_area(
        self,
        dimension,  # endstone.level.Dimension
        min_pos: tuple,
        max_pos: tuple
    ) -> int:
        """清除区域内的所有方块 (用于建筑搬迁)

        将区域内所有方块设置为空气
        """
        cleared_count = 0
        for x in range(min_pos[0], max_pos[0] + 1):
            for y in range(min_pos[1], max_pos[1] + 1):
                for z in range(min_pos[2], max_pos[2] + 1):
                    try:
                        block = dimension.get_block_at(x, y, z)
                        if block.type != "minecraft:air":
                            block.set_type("minecraft:air", False)
                            cleared_count += 1
                    except Exception:
                        pass
        return cleared_count

    def check_chunks_loaded(
        self,
        dimension,  # endstone.level.Dimension
        min_pos: tuple,
        max_pos: tuple
    ) -> bool:
        """检查区域内的区块是否已加载"""
        min_chunk_x = min_pos[0] >> 4
        min_chunk_z = min_pos[2] >> 4
        max_chunk_x = max_pos[0] >> 4
        max_chunk_z = max_pos[2] >> 4

        try:
            loaded = dimension.loaded_chunks
            loaded_set = set()
            for chunk in loaded:
                loaded_set.add((chunk.x, chunk.z))

            for cx in range(min_chunk_x, max_chunk_x + 1):
                for cz in range(min_chunk_z, max_chunk_z + 1):
                    if (cx, cz) not in loaded_set:
                        return False
            return True
        except Exception:
            return True

    @staticmethod
    def get_center_position(min_pos: tuple, max_pos: tuple) -> tuple:
        """获取区域中心位置"""
        cx = (min_pos[0] + max_pos[0]) // 2
        cy = (min_pos[1] + max_pos[1]) // 2
        cz = (min_pos[2] + max_pos[2]) // 2
        return (cx, cy, cz)

    @staticmethod
    def count_actual_blocks(
        dimension,
        selection: Selection,
        preserve_air: bool,
        excluded_blocks: Optional[List[str]] = None
    ) -> int:
        """计算选区内实际方块数量 (排除空气和排除方块后)"""
        if not selection.is_complete():
            return 0

        min_pos, max_pos = selection.get_bounds()
        excluded = excluded_blocks or []
        count = 0

        for x in range(min_pos[0], max_pos[0] + 1):
            for y in range(min_pos[1], max_pos[1] + 1):
                for z in range(min_pos[2], max_pos[2] + 1):
                    try:
                        block = dimension.get_block_at(x, y, z)
                        block_type = block.type
                        if not preserve_air and block_type == "minecraft:air":
                            continue
                        if block_type in excluded:
                            continue
                        count += 1
                    except Exception:
                        pass
        return count
