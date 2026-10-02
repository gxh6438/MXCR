"""
建筑预览管理模块
提供粒子边界预览和位置微调功能，支持旋转/翻转变换
"""
import math
import copy
from typing import Tuple, Optional, List


# ================================================================
#  方块朝向状态变换表
# ================================================================

# facing 字符串属性的顺时针 90° 变换（绕 Y 轴向右旋转）
_FACING_CW = {
    "north": "east",
    "east":  "south",
    "south": "west",
    "west":  "north",
    "up":    "up",
    "down":  "down",
}

# facing 字符串属性的逆时针 90° 变换
_FACING_CCW = {v: k for k, v in _FACING_CW.items()}
# 修正 up/down（自身映射）
_FACING_CCW["up"] = "up"
_FACING_CCW["down"] = "down"

# direction 整数属性的顺时针 90° 变换（0=南, 1=西, 2=北, 3=东）
_DIRECTION_CW  = {0: 3, 1: 0, 2: 1, 3: 2}
_DIRECTION_CCW = {v: k for k, v in _DIRECTION_CW.items()}

# weirdo_direction 整数属性（0=东, 1=西, 2=南, 3=北）
_WEIRDO_CW  = {0: 2, 1: 3, 2: 1, 3: 0}
_WEIRDO_CCW = {v: k for k, v in _WEIRDO_CW.items()}

# pillar_axis 字符串属性的顺时针 90° 变换（绕 Y 轴旋转，X/Z 互换）
_PILLAR_CW = {"x": "z", "z": "x", "y": "y"}

# ground_sign_direction（0-15，顺时针每格 22.5°）顺时针 90° = +4
def _sign_dir_cw(v):  return (v + 4) % 16
def _sign_dir_ccw(v): return (v - 4) % 16

# 上下翻转的 facing 变换
_FACING_FLIP_Y = {
    "up":    "down",
    "down":  "up",
    "north": "north",
    "south": "south",
    "east":  "east",
    "west":  "west",
}


def _transform_block_states(states: dict, transform: str) -> dict:
    """
    对方块状态字典应用变换。

    Args:
        states: 原始方块状态字典
        transform: "cw90" | "ccw90" | "flip_y"
    Returns:
        变换后的方块状态字典（新对象）
    """
    if not states:
        return states

    new_states = dict(states)

    for key, val in states.items():
        k = key.lower()

        if transform in ("cw90", "ccw90"):
            facing_map = _FACING_CW if transform == "cw90" else _FACING_CCW
            dir_map    = _DIRECTION_CW if transform == "cw90" else _DIRECTION_CCW
            weirdo_map = _WEIRDO_CW if transform == "cw90" else _WEIRDO_CCW

            if k == "facing_direction" and isinstance(val, str):
                new_states[key] = facing_map.get(val, val)
            elif k == "minecraft:facing_direction" and isinstance(val, str):
                new_states[key] = facing_map.get(val, val)
            elif k == "facing" and isinstance(val, str):
                new_states[key] = facing_map.get(val, val)
            elif k == "direction" and isinstance(val, int):
                new_states[key] = dir_map.get(val, val)
            elif k == "weirdo_direction" and isinstance(val, int):
                new_states[key] = weirdo_map.get(val, val)
            elif k == "pillar_axis" and isinstance(val, str):
                new_states[key] = _PILLAR_CW.get(val, val)
            elif k == "ground_sign_direction" and isinstance(val, int):
                fn = _sign_dir_cw if transform == "cw90" else _sign_dir_ccw
                new_states[key] = fn(val)

        elif transform == "flip_y":
            if k in ("facing_direction", "minecraft:facing_direction", "facing") and isinstance(val, str):
                new_states[key] = _FACING_FLIP_Y.get(val, val)
            # 上下翻转时楼梯/台阶的 upside_down_bit 取反
            elif k == "upside_down_bit" and isinstance(val, bool):
                new_states[key] = not val

    return new_states


def _apply_transform_to_blocks(blocks: list, transform: str,
                                min_x: int, min_y: int, min_z: int,
                                max_x: int, max_y: int, max_z: int) -> list:
    """
    对方块列表应用坐标变换，返回新的方块列表。
    变换在建筑本地坐标系中进行（原点为 min_x/min_y/min_z）。

    Args:
        blocks: BlockInfo 列表
        transform: "cw90" | "ccw90" | "flip_y"
        min_x/y/z, max_x/y/z: 建筑的包围盒（世界坐标或本地坐标均可，只要一致）
    """
    from .structure_data import BlockInfo  # 延迟导入，避免循环依赖

    size_x = max_x - min_x  # 本地最大 X（0-based）
    size_y = max_y - min_y
    size_z = max_z - min_z

    new_blocks = []
    for b in blocks:
        lx = b.x - min_x
        ly = b.y - min_y
        lz = b.z - min_z

        if transform == "cw90":
            # 顺时针 90°（绕 Y 轴）：(lx, lz) -> (size_z - lz, lx)
            new_lx = size_z - lz
            new_ly = ly
            new_lz = lx
        elif transform == "ccw90":
            # 逆时针 90°（绕 Y 轴）：(lx, lz) -> (lz, size_x - lx)
            new_lx = lz
            new_ly = ly
            new_lz = size_x - lx
        elif transform == "flip_y":
            # 上下翻转（Y 轴镜像）
            new_lx = lx
            new_ly = size_y - ly
            new_lz = lz
        else:
            new_lx, new_ly, new_lz = lx, ly, lz

        new_states = _transform_block_states(
            dict(b.block_states) if b.block_states else {}, transform
        )

        new_blocks.append(BlockInfo(
            x=new_lx + min_x,
            y=new_ly + min_y,
            z=new_lz + min_z,
            block_type=b.block_type,
            block_states=new_states,
            nbt_data=b.nbt_data,
        ))

    return new_blocks


def _apply_transform_to_meta(meta, transform: str):
    """
    对元数据应用变换，返回新的元数据对象（浅拷贝后修改尺寸）。
    顺/逆时针旋转会交换 X 和 Z 的范围；翻转不改变范围。
    """
    new_meta = copy.copy(meta)
    size_x = meta.max_x - meta.min_x
    size_z = meta.max_z - meta.min_z

    if transform in ("cw90", "ccw90"):
        # 旋转后 X 范围变为原 Z 范围，Z 范围变为原 X 范围
        new_meta.max_x = meta.min_x + size_z
        new_meta.max_z = meta.min_z + size_x
    # flip_y 不改变 XZ 范围，Y 范围也不变（只是内部坐标翻转）

    return new_meta


class PreviewManager:
    """建筑预览管理器"""

    def __init__(self, plugin):
        """
        Args:
            plugin: MXCRPlugin 实例
        """
        self.plugin = plugin
        # 存储每个玩家的预览状态
        # {player_name: {"task": Task, "position": (x, y, z), "size": (w, h, l),
        #                "structure_id": str, "meta": meta, "blocks": list|None,
        #                "entities": list|None, "source_type": str, "source_dir": str}}
        self.active_previews = {}

    def start_preview(
        self,
        player,
        structure_id: str,
        meta,
        position: Tuple[int, int, int],
        source_type: str = "player",
        source_dir: str = "",
        reliable_mode: bool = False
    ):
        """
        开始预览建筑

        Args:
            player: 玩家对象
            structure_id: 建筑 ID
            meta: 建筑元数据
            position: 预览位置 (x, y, z)
            source_type: 来源类型
            source_dir: 来源目录
            reliable_mode: 传统可靠模式（v1.0.0 修复：此前该参数在预览路径丢失）
        """
        player_name = player.name

        # 如果已有预览,先停止
        if player_name in self.active_previews:
            self.stop_preview(player)

        # 计算建筑尺寸
        size = (
            meta.max_x - meta.min_x + 1,
            meta.max_y - meta.min_y + 1,
            meta.max_z - meta.min_z + 1
        )

        # 先保存预览状态（任务执行时需要读取状态，必须先保存）
        self.active_previews[player_name] = {
            "task": None,
            "position": list(position),
            "size": size,
            "structure_id": structure_id,
            "meta": meta,
            "blocks": None,       # None 表示未变换，直接从数据库读取
            "entities": None,     # None 表示未变换，直接从数据库读取
            "source_type": source_type,
            "source_dir": source_dir,
            "reliable_mode": reliable_mode,
        }

        # 启动粒子显示任务（通过 player_name 动态获取最新玩家对象，避免使用过期引用）
        task = self.plugin.server.scheduler.run_task(
            self.plugin,
            lambda: self._show_preview_particles_dynamic(player_name),
            delay=0,
            period=20  # 每秒更新一次
        )

        # 更新任务引用
        self.active_previews[player_name]["task"] = task

        # 显示微调面板
        self._show_adjustment_panel(player)

    def stop_preview(self, player):
        """停止预览"""
        player_name = player.name
        if player_name in self.active_previews:
            preview = self.active_previews[player_name]
            if preview["task"] and not preview["task"].is_cancelled:
                preview["task"].cancel()
            del self.active_previews[player_name]

    def _get_online_player(self, player_name: str):
        """通过玩家名从在线玩家列表中查找玩家对象（比 get_player 更可靠）"""
        try:
            for p in self.plugin.server.online_players:
                if p.name == player_name:
                    return p
        except Exception:
            pass
        return None

    def _show_preview_particles_dynamic(self, player_name: str):
        """通过玩家名动态获取最新玩家对象并显示粒子"""
        if player_name not in self.active_previews:
            return

        online_player = self._get_online_player(player_name)
        if online_player is None:
            return

        preview = self.active_previews[player_name]
        position = tuple(preview["position"])  # 动态读取最新位置
        size = preview["size"]

        self._show_preview_particles(online_player, position, size)

    def _show_preview_particles(
        self,
        player,
        position: Tuple[int, int, int],
        size: Tuple[int, int, int]
    ):
        """显示预览粒子"""
        if not hasattr(player, "spawn_particle"):
            return

        x, y, z = position
        w, h, l = size

        try:
            # 底部四角 (绿色)
            corners_bottom = [
                (x, y, z),
                (x + w, y, z),
                (x, y, z + l),
                (x + w, y, z + l)
            ]
            for cx, cy, cz in corners_bottom:
                player.spawn_particle("minecraft:villager_happy", cx, cy, cz)

            # 顶部四角 (蓝色)
            corners_top = [
                (x, y + h, z),
                (x + w, y + h, z),
                (x, y + h, z + l),
                (x + w, y + h, z + l)
            ]
            for cx, cy, cz in corners_top:
                player.spawn_particle("minecraft:soul", cx, cy, cz)

            # 12 条边框线 (白色)
            edges = [
                # 底部 4 条边
                ((x, y, z), (x + w, y, z)),
                ((x, y, z), (x, y, z + l)),
                ((x + w, y, z), (x + w, y, z + l)),
                ((x, y, z + l), (x + w, y, z + l)),
                # 顶部 4 条边
                ((x, y + h, z), (x + w, y + h, z)),
                ((x, y + h, z), (x, y + h, z + l)),
                ((x + w, y + h, z), (x + w, y + h, z + l)),
                ((x, y + h, z + l), (x + w, y + h, z + l)),
                # 4 条竖边
                ((x, y, z), (x, y + h, z)),
                ((x + w, y, z), (x + w, y + h, z)),
                ((x, y, z + l), (x, y + h, z + l)),
                ((x + w, y, z + l), (x + w, y + h, z + l)),
            ]

            for (x1, y1, z1), (x2, y2, z2) in edges:
                # 在边上每险4格显示一个粒子
                steps = max(abs(x2 - x1), abs(y2 - y1), abs(z2 - z1)) // 2 + 1
                for i in range(steps):
                    t = i / max(steps - 1, 1)
                    px = x1 + (x2 - x1) * t
                    py = y1 + (y2 - y1) * t
                    pz = z1 + (z2 - z1) * t
                    player.spawn_particle("minecraft:endrod", px, py, pz)

            # 中心点 (黄色/橙色)
            center_x = x + w / 2
            center_y = y + h / 2
            center_z = z + l / 2
            player.spawn_particle("minecraft:lava", center_x, center_y, center_z)

        except Exception:
            pass

    def _show_adjustment_panel(self, player):
        """显示微调面板"""
        from endstone.form import ActionForm

        player_name = player.name
        if player_name not in self.active_previews:
            return

        preview = self.active_previews[player_name]
        x, y, z = preview["position"]
        w, h, l = preview["size"]
        volume = w * h * l

        form = ActionForm(
            title="§6§l建筑预览",
            content=(
                f"§b当前位置: §e({x}, {y}, {z})\n"
                f"§b建筑尺寸: §e{w}§7x§e{h}§7x§e{l} §7(宽x高x长)\n"
                f"§b体积: §e{volume} §b方块"
            )
        )

        # 向上移动
        form.add_button(
            "§f向上移动",
            icon="textures/ui/up_arrow",
            on_click=lambda p: self._move_preview(p, 0, 1, 0)
        )

        # 向下移动
        form.add_button(
            "§f向下移动",
            icon="textures/ui/down_arrow",
            on_click=lambda p: self._move_preview(p, 0, -1, 0)
        )

        # 向左移动
        form.add_button(
            "§f向左移动",
            icon="textures/ui/arrow_dark_left_stretch",
            on_click=lambda p: self._move_preview_relative(p, -1, 0)
        )

        # 向右移动
        form.add_button(
            "§f向右移动",
            icon="textures/ui/arrow_dark_right_stretch",
            on_click=lambda p: self._move_preview_relative(p, 1, 0)
        )

        # 向前移动
        form.add_button(
            "§f向前移动",
            icon="textures/ui/TabTopFront",
            on_click=lambda p: self._move_preview_relative(p, 0, 1)
        )

        # 向后移动
        form.add_button(
            "§f向后移动",
            icon="textures/ui/TabTopFrontHover",
            on_click=lambda p: self._move_preview_relative(p, 0, -1)
        )

        # ---- 旋转/翻转按钮 ----

        # 上下翻转
        form.add_button(
            "§e翻转建筑",
            icon="textures/ui/grey_gamepad_icon_button",
            on_click=lambda p: self._rotate_preview(p, "flip_y")
        )

        # 顺时针旋转 90°
        form.add_button(
            "§e顺时针旋转 90°",
            icon="textures/ui/hammer_l",
            on_click=lambda p: self._rotate_preview(p, "cw90")
        )

        # 逆时针旋转 90°
        form.add_button(
            "§e逆时针旋转 90°",
            icon="textures/ui/hammer_r",
            on_click=lambda p: self._rotate_preview(p, "ccw90")
        )

        # 确认粘贴
        form.add_button(
            "§a§l确认粘贴",
            icon="textures/ui/gear",
            on_click=lambda p: self._confirm_preview(p)
        )

        # 取消预览
        form.add_button(
            "§c§l取消预览",
            icon="textures/ui/cancel",
            on_click=lambda p: self._cancel_preview(p)
        )

        player.send_form(form)

    # ================================================================
    #  旋转/翻转
    # ================================================================

    def _rotate_preview(self, player, transform: str):
        """
        对预览建筑应用旋转/翻转变换。

        Args:
            player: 玩家对象
            transform: "cw90" | "ccw90" | "flip_y"
        """
        player_name = player.name
        if player_name not in self.active_previews:
            player.send_message("§c[MXCR] 预览已结束")
            return

        preview = self.active_previews[player_name]
        meta = preview["meta"]

        # 如果 blocks 还未加载（首次旋转），先从数据库读取
        if preview["blocks"] is None:
            try:
                blocks = self._load_blocks_for_preview(preview)
            except Exception as e:
                player.send_message(f"§c[MXCR] 读取建筑数据失败: {e}")
                return
            if blocks is None:
                player.send_message("§c[MXCR] 建筑数据不存在!")
                return
            preview["blocks"] = blocks

        if preview["entities"] is None:
            try:
                entities = self._load_entities_for_preview(preview)
            except Exception as e:
                entities = []
            preview["entities"] = entities if entities is not None else []

        # 应用坐标变换到方块
        transformed_blocks = _apply_transform_to_blocks(
            preview["blocks"], transform,
            meta.min_x, meta.min_y, meta.min_z,
            meta.max_x, meta.max_y, meta.max_z,
        )
        preview["blocks"] = transformed_blocks

        # 应用变换到元数据（更新尺寸）
        new_meta = _apply_transform_to_meta(meta, transform)
        preview["meta"] = new_meta

        # 更新 size（用于粒子显示）
        new_size = (
            new_meta.max_x - new_meta.min_x + 1,
            new_meta.max_y - new_meta.min_y + 1,
            new_meta.max_z - new_meta.min_z + 1,
        )
        preview["size"] = new_size

        transform_names = {
            "cw90":   "顺时针旋转 90°",
            "ccw90":  "逆时针旋转 90°",
            "flip_y": "上下翻转",
        }
        player.send_message(
            f"§a[MXCR] 已应用变换: §e{transform_names.get(transform, transform)}"
        )

        # 立即刷新粒子
        pos = preview["position"]
        fresh_player = self._get_online_player(player_name) or player
        self._show_preview_particles(fresh_player, tuple(pos), new_size)

        # 重新显示微调面板
        self._show_adjustment_panel(player)

    def _load_blocks_for_preview(self, preview: dict):
        """从数据库读取方块数据（用于首次旋转时懒加载）"""
        plugin = self.plugin
        structure_id = preview["structure_id"]
        source_type = preview["source_type"]
        source_dir = preview["source_dir"]

        if source_type == "public":
            return plugin._db_mgr.get_public_structure_blocks(structure_id)
        elif source_type == "admin" and source_dir:
            return plugin._db_mgr.get_blocks_from_dir(source_dir, structure_id)
        else:
            # 需要玩家 UUID，但 preview 中没有存储；通过 meta 中的 player_uuid 获取
            meta = preview["meta"]
            player_uuid = getattr(meta, "player_uuid", None)
            player_name_db = getattr(meta, "player_name", "")
            if player_uuid is None:
                return None
            return plugin._db_mgr.get_structure_blocks(
                player_uuid, structure_id, player_name_db
            )

    def _load_entities_for_preview(self, preview: dict):
        """从数据库读取实体数据（用于首次旋转时懒加载）"""
        plugin = self.plugin
        structure_id = preview["structure_id"]
        source_type = preview["source_type"]
        source_dir = preview["source_dir"]

        if source_type == "public":
            return plugin._db_mgr.get_public_structure_entities(structure_id)
        elif source_type == "admin" and source_dir:
            return plugin._db_mgr.get_entities_from_dir(source_dir, structure_id)
        else:
            meta = preview["meta"]
            player_uuid = getattr(meta, "player_uuid", None)
            player_name_db = getattr(meta, "player_name", "")
            if player_uuid is None:
                return []
            return plugin._db_mgr.get_structure_entities(
                player_uuid, structure_id, player_name_db
            )

    # ================================================================
    #  移动
    # ================================================================

    def _move_preview(self, player, dx: int, dy: int, dz: int):
        """移动预览位置（绝对坐标偏移）"""
        player_name = player.name
        if player_name not in self.active_previews:
            player.send_message("§c[MXCR] 预览已结束")
            return

        preview = self.active_previews[player_name]
        pos = preview["position"]  # 直接操作 list，避免元组/列表混用
        pos[0] += dx
        pos[1] += dy
        pos[2] += dz

        player.send_message(
            f"§a[MXCR] 预览位置已移动到: §e({pos[0]}, {pos[1]}, {pos[2]})"
        )

        # 立即刷新一次粒子，让玩家看到新位置
        fresh_player = self._get_online_player(player_name) or player
        self._show_preview_particles(fresh_player, tuple(pos), preview["size"])

        # 重新显示微调面板
        self._show_adjustment_panel(player)

    def _move_preview_relative(self, player, left_right: int, forward_back: int):
        """相对玩家朝向移动预览位置（修复 int() 截断导致位移为 0 的问题）"""
        player_name = player.name
        if player_name not in self.active_previews:
            player.send_message("§c[MXCR] 预览已结束")
            return

        # 获取玩家朝向（yaw: 0=南, 90=西, 180=北, 270=东）
        try:
            yaw = player.location.yaw
        except Exception:
            yaw = 0.0

        yaw_rad = math.radians(yaw)

        # 计算前后方向偏移（forward_back > 0 为向前）
        dx_forward = -math.sin(yaw_rad) * forward_back
        dz_forward = math.cos(yaw_rad) * forward_back

        # 计算左右方向偏移（left_right > 0 为向右）
        dx_right = math.cos(yaw_rad) * left_right
        dz_right = math.sin(yaw_rad) * left_right

        # 合并偏移，使用 round() 而非 int()，避免截断导致位移为 0
        raw_dx = dx_forward + dx_right
        raw_dz = dz_forward + dz_right

        dx = int(round(raw_dx))
        dz = int(round(raw_dz))

        # 极端情况：朝向恰好 45° 对角，round 后两轴都为 0，强制保留较大分量方向
        if dx == 0 and dz == 0:
            if abs(raw_dx) >= abs(raw_dz):
                dx = 1 if raw_dx >= 0 else -1
            else:
                dz = 1 if raw_dz >= 0 else -1

        self._move_preview(player, dx, 0, dz)

    # ================================================================
    #  确认 / 取消
    # ================================================================

    # ================================================================
    #  高度限制检测
    # ================================================================

    def _check_height_limit(self, player, position, meta) -> bool:
        """
        检测建筑物在当前维度下是否超过高度限制。

        如果超限，则弹出确认表单让玩家选择。
        返回 True 表示可以继续（未超限），返回 False 表示已弹出表单等待玩家确认。
        """
        from endstone.form import MessageForm
        from .api_compat import get_dim_y_limits, get_dim_display_name

        try:
            dim_name = player.location.dimension.name.lower()
        except Exception:
            dim_name = "overworld"

        y_min_limit, y_max_limit = get_dim_y_limits(dim_name)

        # 建筑物的实际 Y 范围（v1.0.0 修复：粘贴原点就是建筑新包围盒的 Y 下界，
        # 高度 = max_y - min_y + 1；此前错误地把原始绝对坐标 min_y/max_y 加到粘贴原点上，
        # 导致保存位置较高/较低的建筑在粘贴时高度检测全部误报）
        place_y = position[1]
        struct_height = (meta.max_y - meta.min_y + 1) if hasattr(meta, "max_y") else 1
        struct_y_min = place_y
        struct_y_max = place_y + struct_height - 1

        if struct_y_min < y_min_limit or struct_y_max > y_max_limit:
            # 超限，弹出确认表单
            dim_display = get_dim_display_name(dim_name)

            form = MessageForm(
                title="§c§l高度超限警告",
                content=(
                    f"§e建筑物已超过高度限制！\n\n"
                    f"§b当前维度：§f{dim_display}§b，允许范围：§fY {y_min_limit} ~ {y_max_limit}\n"
                    f"§b建筑物范围：§fY {struct_y_min} ~ {struct_y_max}\n\n"
                    f"§c超限的方块将无法放置，可能导致建筑不完整。"
                ),
                button1="§a我清楚服务器的高度限制",  # 继续加载
                button2="§c取消",                         # 取消
            )

            # 保存当前预览状态到临时变量，以便回调中使用
            player_name = player.name
            preview_snapshot = self.active_previews.get(player_name)

            def on_height_confirm(p, result):
                if result == 0:  # 按钮 1：继续
                    self._do_confirm_paste(p)
                else:            # 按钮 2：取消
                    p.send_message("§e[MXCR] 已取消加载")
                    # 重新显示预览面板
                    self._show_adjustment_panel(p)

            form.on_submit = on_height_confirm
            player.send_form(form)
            return False  # 已弹出表单，等待玩家确认

        return True  # 未超限，可以继续

    def _do_confirm_paste(self, player):
        """实际执行粘贴（高度检测通过后调用）"""
        player_name = player.name
        if player_name not in self.active_previews:
            player.send_message("§c[MXCR] 预览已结束")
            return

        preview = self.active_previews[player_name]
        position = tuple(preview["position"])
        structure_id = preview["structure_id"]
        source_type = preview["source_type"]
        source_dir = preview["source_dir"]
        transformed_blocks = preview["blocks"]
        transformed_entities = preview["entities"]
        transformed_meta = preview["meta"]
        reliable_mode = bool(preview.get("reliable_mode", False))

        self.stop_preview(player)

        if transformed_blocks is not None:
            self.plugin.load_structure_for_player(
                player, structure_id, position,
                source_type=source_type, source_dir=source_dir,
                reliable_mode=reliable_mode,
                preloaded_blocks=transformed_blocks,
                preloaded_meta=transformed_meta,
                preloaded_entities=transformed_entities or [],
            )
        else:
            self.plugin.load_structure_for_player(
                player, structure_id, position,
                source_type=source_type, source_dir=source_dir,
                reliable_mode=reliable_mode
            )

    def _confirm_preview(self, player):
        """确认粘贴（先进行高度检测）"""
        player_name = player.name
        if player_name not in self.active_previews:
            player.send_message("§c[MXCR] 预览已结束")
            return

        preview = self.active_previews[player_name]
        position = tuple(preview["position"])
        meta = preview["meta"]

        # 高度限制检测：如果超限则弹出确认表单，不继续执行
        if not self._check_height_limit(player, position, meta):
            return

        # 未超限，直接执行粘贴
        self._do_confirm_paste(player)

    def _cancel_preview(self, player):
        """取消预览"""
        player_name = player.name
        if player_name not in self.active_previews:
            player.send_message("§c[MXCR] 预览已结束")
            return

        # 停止预览
        self.stop_preview(player)

        player.send_message("§e[MXCR] 已取消预览")

    def get_preview_position(self, player) -> Optional[Tuple[int, int, int]]:
        """获取当前预览位置"""
        player_name = player.name
        if player_name in self.active_previews:
            return self.active_previews[player_name]["position"]
        return None
