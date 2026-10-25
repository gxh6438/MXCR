"""
建筑预览管理模块 (v1.1.0)
提供粒子边界预览和位置微调功能。

底层迁移到 StructBridge 后：
- 旋转/翻转功能移除（引擎 structure load 不支持旋转参数）
- 不再从数据库加载方块/实体数据（数据在 .mcstructure 文件中，由引擎放置）
- 确认粘贴直接走 plugin.load_structure_for_player（引擎原生放置 + 快照撤销）
"""
import math
from typing import Tuple, Optional


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
        #                "structure_id": str, "meta": meta, "source_type": str}}
        self.active_previews = {}

    def start_preview(
        self,
        player,
        structure_id: str,
        meta,
        position: Tuple[int, int, int],
        source_type: str = "player"
    ):
        """
        开始预览建筑

        Args:
            player: 玩家对象
            structure_id: 建筑 ID
            meta: 建筑元数据
            position: 预览位置 (x, y, z)
            source_type: 来源类型 ("player" / "public" / "admin")
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
            "source_type": source_type,
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
                # 在边上每 2 格显示一个粒子
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
        """相对玩家朝向移动预览位置（使用 round() 避免截断导致位移为 0）"""
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

        # 建筑物的实际 Y 范围（粘贴原点就是建筑新包围盒的 Y 下界）
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

        self.stop_preview(player)

        # v1.1.0: 引擎原生放置（内部先做区域快照用于撤销）
        self.plugin.load_structure_for_player(
            player, structure_id, position, source_type=source_type
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
