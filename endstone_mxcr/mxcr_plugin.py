"""
MXCR 主插件类 v1.1.0
底层对接 StructBridge：保存/加载/撤销全部走引擎原生 .mcstructure 序列化。

关键 API:
- Player.is_op -> bool (可读写)
- Player.name -> str
- Player.unique_id -> uuid.UUID
- Player.dimension -> Dimension
- Player.location -> Location
- Player.teleport(location: Location) -> bool
- Player.spawn_particle(name: str, x: float, y: float, z: float) -> None
- Player.send_form(form) -> None
- Player.send_message(message: str) -> None
- Server.online_players -> list[Player]
- Server.get_player(name: str) -> Player
- Server.scheduler -> Scheduler
- Server.dispatch_command(sender, command_line)
- Server.command_sender -> CommandSender (控制台)
- Dimension.loaded_chunks -> list[Chunk]
- Scheduler.run_task(plugin, task, delay=0, period=0) -> Task
"""
import time
from endstone.plugin import Plugin
from endstone.command import Command, CommandSender
from endstone.event import event_handler, PlayerInteractEvent, PlayerQuitEvent

from .selection_manager import SelectionManager
from .config_manager import ConfigManager, PermissionManager
from .normal_permission_manager import NormalPermissionManager
from .progress_manager import ProgressManager
from .preview_manager import PreviewManager
from .form_manager import FormManager
from .metadata_store import MetadataStore
from .structbridge_backend import (
    StructBridgeBackend, STAGE_OK,
    new_building_key, new_snapshot_key, is_bundle_region,
)
from .structure_data import StructureMetadata, UndoEntry


class MXCRPlugin(Plugin):
    """MXCR 建筑复制工具插件 v1.1.0（StructBridge 引擎原生后端）"""

    # 插件 API 版本
    api_version = "0.11"

    # 命令定义
    commands = {
        "mxcr": {
            "description": "MXCR 建筑复制工具主命令",
            "usages": [
                "/mxcr",
                "/mxcr start",
                "/mxcr ok",
                "/mxcr cancel",
                "/mxcr stop",
                "/mxcr list",
                "/mxcr undo",
                "/mxcr redo",
                "/mxcr preview",
                "/mxcr op <name: str>",
                "/mxcr deop <name: str>",
                "/mxcr oplist",
                "/mxcr admin <name: str>",
                "/mxcr deadmin <name: str>",
                "/mxcr reload",
                "/mxcr bridge",
            ],
            "permissions": ["mxcr.command"],
        }
    }

    # 权限定义
    permissions = {
        "mxcr.command": {
            "description": "允许使用 MXCR 建筑复制工具",
            "default": True,
        }
    }

    # 搬迁模式 /fill 单次上限（原版 fill 限制 32768，留余量）
    FILL_LIMIT = 30000

    def on_enable(self) -> None:
        """插件启用时调用"""
        self.logger.info("MXCR 建筑复制工具 v1.1.0 正在启用...")

        data_dir = self.data_folder
        data_dir.mkdir(parents=True, exist_ok=True)

        # 初始化各管理器
        self._selection_mgr = SelectionManager()
        self._config_mgr = ConfigManager(data_dir)
        self._perm_mgr = PermissionManager(data_dir)
        self._normal_perm_mgr = NormalPermissionManager(data_dir, data_dir / "permissions.json")
        self._progress_mgr = ProgressManager(self.server)
        self._store = MetadataStore(data_dir / "data")
        self._backend = StructBridgeBackend()
        self._preview_mgr = PreviewManager(self)
        self._form_mgr = FormManager(self)

        # 结构文件 GC 回调（仅清理 mxcrsnap_* 快照文件）
        self._store.set_gc_callback(self._gc_snapshot_file)

        # 检测 StructBridge 后端
        ok, msg = self._backend.ensure()
        if ok:
            self.logger.info(f"StructBridge 后端已就绪 (v{self._backend.version})")
        else:
            self.logger.error(f"StructBridge 后端不可用: {msg}！")
            self.logger.error("保存/加载/撤销功能将无法使用。请安装 StructBridge 插件: "
                              "https://github.com/gxh6438/structbridge")

        # 注册事件监听器
        self.register_events(self)

        self.logger.info("MXCR 建筑复制工具 v1.1.0 已启用!")

    def on_disable(self) -> None:
        """插件禁用时调用"""
        try:
            self.server.scheduler.cancel_tasks(self)
        except Exception:
            pass
        self.logger.info("MXCR 建筑复制工具已禁用!")

    # ========== 属性访问器 (与 form_manager 中的调用名对齐) ==========

    @property
    def selection_manager(self) -> SelectionManager:
        return self._selection_mgr

    @property
    def config_manager(self) -> ConfigManager:
        return self._config_mgr

    @property
    def perm_manager(self) -> PermissionManager:
        return self._perm_mgr

    @property
    def form_manager(self) -> FormManager:
        return self._form_mgr

    @property
    def progress_manager(self) -> ProgressManager:
        return self._progress_mgr

    @property
    def normal_perm_manager(self) -> NormalPermissionManager:
        return self._normal_perm_mgr

    @property
    def preview_manager(self) -> PreviewManager:
        return self._preview_mgr

    @property
    def store(self) -> MetadataStore:
        return self._store

    @property
    def backend(self) -> StructBridgeBackend:
        return self._backend

    # ========== 权限检查 ==========

    def _check_full_permission(self, player) -> bool:
        """检查玩家是否有完整权限 (OP / 授权玩家 / 游戏管理员)"""
        return self._perm_mgr.is_authorized(player.name, player.is_op)

    def _check_copy_permission(self, player) -> bool:
        """检查玩家是否有复制(保存)权限"""
        if self._perm_mgr.is_authorized(player.name, player.is_op):
            return True
        if self._config_mgr.normal_player_permissions_enabled:
            return self.normal_perm_manager.check_permission(player.unique_id, "allow_copy")
        return False

    def _check_save_limit(self, player) -> bool:
        """检查玩家是否达到保存上限"""
        if player.is_op:
            return True  # OP 无限制
        if self._perm_mgr.is_game_admin(player.name, player.is_op):
            return True  # 管理员无限制
        p_uuid = str(player.unique_id)
        count = self._store.count_owned_structures(p_uuid)
        # 授权玩家使用全局上限；普通玩家使用普通权限系统的 max_structures
        if self._perm_mgr.is_authorized(player.name, player.is_op):
            limit = self._config_mgr.authorized_save_limit
        elif self._config_mgr.normal_player_permissions_enabled:
            limit = self._normal_perm_mgr.get_limit(
                player.unique_id, "max_structures", default=0
            )
        else:
            limit = 0
        return limit > 0 and count < limit

    def _check_normal_permission(self, player, permission_key: str) -> bool:
        """
        检查普通玩家的细粒度权限。

        OP / 授权玩家 / 游戏管理员直接放行；
        普通玩家在启用普通权限系统时按权限键判断，否则拒绝。
        """
        if self._perm_mgr.is_authorized(player.name, player.is_op):
            return True
        if self._config_mgr.normal_player_permissions_enabled:
            return self._normal_perm_mgr.check_permission(player.unique_id, permission_key)
        return False

    def _get_normal_limit(self, player, limit_key: str, default: int = 0) -> int:
        """获取普通玩家的限制值；授权玩家返回 default 表示不受限（调用方配合全局上限）"""
        if self._perm_mgr.is_authorized(player.name, player.is_op):
            return default
        if self._config_mgr.normal_player_permissions_enabled:
            return self._normal_perm_mgr.get_limit(player.unique_id, limit_key, default=default)
        return default

    def _require_backend(self, player) -> bool:
        """检查 StructBridge 后端可用性，不可用时给玩家明确提示。"""
        ok, msg = self._backend.ensure()
        if not ok:
            player.send_message("§c[MXCR] StructBridge 后端不可用，无法执行此操作!")
            player.send_message("§7请安装 StructBridge 插件并确认其为最新版 (含 Python API)")
            return False
        return True

    # ========== 命令处理 ==========

    def on_command(self, sender: CommandSender, command: Command, args: list) -> bool:
        """命令处理入口（全局异常保护）"""
        try:
            return self._dispatch_command(sender, command, args)
        except Exception as e:
            self.logger.error(f"[MXCR] 命令处理异常: {e}")
            try:
                sender.send_message("§c[MXCR] 命令执行出错，请查看后台日志")
            except Exception:
                pass
            return True

    def _dispatch_command(self, sender: CommandSender, command: Command, args: list) -> bool:
        """命令处理"""
        if command.name != "mxcr":
            return False

        # 检查是否为玩家
        if not hasattr(sender, "send_form"):
            sender.send_message("§c此命令只能由玩家执行!")
            return True

        player = sender

        # 检查插件总开关（OP 可以绕过）
        if not self._config_mgr.plugin_enabled and not player.is_op:
            player.send_message("§c[MXCR] 插件已被管理员关闭")
            player.send_message("§7请联系服务器管理员")
            return True

        # 处理 OP 专属子命令
        if len(args) >= 1:
            sub = args[0].lower()

            # /mxcr op <name>
            if sub == "op":
                if not player.is_op:
                    player.send_message("§c[MXCR] 只有服务器 OP 才能授权其他玩家!")
                    return True
                if len(args) < 2:
                    player.send_message("§c[MXCR] 用法: /mxcr op <玩家名>")
                    return True
                target_name = args[1]
                if self._perm_mgr.add_authorized_player(target_name):
                    player.send_message(f"§a[MXCR] 已授权玩家 §e{target_name} §a使用 MXCR 插件!")
                else:
                    player.send_message(f"§e[MXCR] 玩家 §b{target_name} §e已经拥有权限")
                return True

            # /mxcr deop <name>
            elif sub == "deop":
                if not player.is_op:
                    player.send_message("§c[MXCR] 只有服务器 OP 才能取消授权!")
                    return True
                if len(args) < 2:
                    player.send_message("§c[MXCR] 用法: /mxcr deop <玩家名>")
                    return True
                target_name = args[1]
                if self._perm_mgr.remove_authorized_player(target_name):
                    player.send_message(f"§a[MXCR] 已取消玩家 §e{target_name} §a的 MXCR 权限!")
                else:
                    player.send_message(f"§e[MXCR] 玩家 §b{target_name} §e不在授权列表中")
                return True

            # /mxcr oplist
            elif sub == "oplist":
                if not player.is_op:
                    player.send_message("§c[MXCR] 只有服务器 OP 才能查看授权列表!")
                    return True
                authorized = self._perm_mgr.get_authorized_players()
                admins = self._perm_mgr.get_game_admins()
                player.send_message("§b[MXCR] §l===== 权限列表 =====")
                player.send_message(f"§6授权玩家: §f{', '.join(authorized) if authorized else '无'}")
                player.send_message(f"§6游戏管理员: §f{', '.join(admins) if admins else '无'}")
                player.send_message("§7(服务器 OP 默认拥有所有权限)")
                return True

            # /mxcr admin <name>
            elif sub == "admin":
                if not player.is_op:
                    player.send_message("§c[MXCR] 只有服务器 OP 才能设置游戏管理员!")
                    return True
                if len(args) < 2:
                    player.send_message("§c[MXCR] 用法: /mxcr admin <玩家名>")
                    return True
                target_name = args[1]
                if self._perm_mgr.add_game_admin(target_name):
                    player.send_message(f"§a[MXCR] 已将 §e{target_name} §a设为游戏管理员!")
                else:
                    player.send_message(f"§e[MXCR] 玩家 §b{target_name} §e已经是游戏管理员了")
                return True

            # /mxcr deadmin <name>
            elif sub == "deadmin":
                if not player.is_op:
                    player.send_message("§c[MXCR] 只有服务器 OP 才能移除游戏管理员!")
                    return True
                if len(args) < 2:
                    player.send_message("§c[MXCR] 用法: /mxcr deadmin <玩家名>")
                    return True
                target_name = args[1]
                if self._perm_mgr.remove_game_admin(target_name):
                    player.send_message(f"§a[MXCR] 已移除 §e{target_name} §a的游戏管理员权限!")
                else:
                    player.send_message(f"§e[MXCR] 玩家 §b{target_name} §e不是游戏管理员")
                return True

            # /mxcr reload
            elif sub == "reload":
                if not player.is_op:
                    player.send_message("§c[MXCR] 只有服务器 OP 才能重载配置!")
                    return True
                self._config_mgr.reload()
                self._perm_mgr.reload()
                player.send_message("§a[MXCR] 配置和权限已重新加载!")
                return True

            # /mxcr bridge
            elif sub == "bridge":
                self._check_bridge_status(player)
                return True

            # /mxcr undo
            elif sub == "undo":
                self.undo_operation(player)
                return True

            # /mxcr redo
            elif sub == "redo":
                self.redo_operation(player)
                return True

            # /mxcr preview
            elif sub == "preview":
                self.toggle_preview_panel(player)
                return True

        # 检查是否有至少复制权限
        has_copy = self._check_copy_permission(player)
        has_full = self._check_full_permission(player)

        if not has_copy and not has_full:
            player.send_message("§c[MXCR] 你没有权限使用此插件!")
            player.send_message("§7请联系服务器管理员使用 /mxcr op <你的名字> 授权")
            return True

        # 无参数 - 打开主菜单
        if len(args) == 0:
            self._form_mgr.show_main_menu(player)
            return True

        sub = args[0].lower()

        if sub == "start":
            if has_copy or has_full:
                self.start_selection_for_player(player)
            else:
                player.send_message("§c[MXCR] 你没有选区权限!")
        elif sub == "ok":
            if has_copy or has_full:
                self.confirm_selection_for_player(player)
            else:
                player.send_message("§c[MXCR] 你没有保存权限!")
        elif sub == "cancel":
            self.cancel_selection_for_player(player)
        elif sub == "stop":
            self.stop_selection_for_player(player)
        elif sub == "list":
            self._form_mgr.show_my_structures(player)
        else:
            player.send_message(f"§c未知子命令: {sub}")
            player.send_message("§7用法: /mxcr [start|ok|cancel|list|undo|redo|op|deop|oplist|admin|deadmin|reload|bridge]")

        return True

    # ========== 事件处理 ==========

    @event_handler
    def on_player_interact(self, event: PlayerInteractEvent):
        """玩家交互事件处理（全局异常保护）"""
        try:
            self._handle_player_interact(event)
        except Exception as e:
            self.logger.error(f"[MXCR] 交互事件处理异常: {e}")

    def _handle_player_interact(self, event: PlayerInteractEvent):
        """交互事件实际处理逻辑"""
        player = event.player
        player_name = player.name

        # 检查插件总开关（OP 可以绕过）
        if not self._config_mgr.plugin_enabled and not player.is_op:
            return

        # 只处理右键点击方块
        if event.action != PlayerInteractEvent.Action.RIGHT_CLICK_BLOCK:
            return

        # 检查是否持有物品
        if not event.has_item:
            return

        item = event.item
        if item is None:
            return

        try:
            item_type_id = item.type.id
        except Exception:
            return

        # ========== 菜单物品检查 ==========
        if self._config_mgr.menu_item_enabled:
            menu_item_id = self._config_mgr.menu_item_id
            if item_type_id == menu_item_id:
                # 检查权限
                if self._check_copy_permission(player) or self._check_full_permission(player):
                    event.is_cancelled = True
                    self._form_mgr.show_main_menu(player)
                return

        # ========== 选区检查 ==========
        if not self._selection_mgr.is_selecting(player_name):
            return

        # 获取配置的选区物品和修改物品
        selection_item = self._config_mgr.selection_item_id
        modify_item = self._config_mgr.modify_item_id

        # 检查是否持有选区物品或修改物品
        if item_type_id not in [selection_item, modify_item]:
            return

        # 检查是否有点击的方块
        if not event.has_block:
            return

        # 取消默认行为
        event.is_cancelled = True

        block = event.block
        pos = (block.x, block.y, block.z)
        dim_name = block.dimension.name

        selection = self._selection_mgr.get_selection(player_name)

        # 如果持有修改物品(木棍)且已经有两个点,则修改第二个点
        if item_type_id == modify_item and selection.pos1 is not None and selection.pos2 is not None:
            self._selection_mgr.set_position(player_name, pos, dim_name, 2)
            player.send_message(f"§a[MXCR] §l第二个点已修改: §e({pos[0]}, {pos[1]}, {pos[2]})")

            size = selection.get_size()
            volume = selection.get_volume()
            player.send_message(f"§b选区尺寸: §e{size[0]}§7x§e{size[1]}§7x§e{size[2]}")
            player.send_message(f"§b选区体积: §e{volume} §b方块")

            # 重新启动粒子显示
            self._start_particle_loop(player, player_name)
            return

        # 以下是使用选区物品(木斧)的逻辑
        if item_type_id != selection_item:
            return

        if selection.pos1 is None:
            # 设置第一个点
            self._selection_mgr.set_position(player_name, pos, dim_name, 1)
            player.send_message(f"§a[MXCR] §l第一个点已设置: §e({pos[0]}, {pos[1]}, {pos[2]})")
            player.send_message("§7请右键点击设置第二个点")
            self._spawn_point_particles(player, pos)

        elif selection.pos2 is None:
            # 设置第二个点
            self._selection_mgr.set_position(player_name, pos, dim_name, 2)
            player.send_message(f"§a[MXCR] §l第二个点已设置: §e({pos[0]}, {pos[1]}, {pos[2]})")

            size = selection.get_size()
            volume = selection.get_volume()
            player.send_message(f"§b选区尺寸: §e{size[0]}§7x§e{size[1]}§7x§e{size[2]}")
            player.send_message(f"§b选区体积: §e{volume} §b方块")
            player.send_message("§a选区完成! 使用 §e/mxcr ok §a确认保存,或 §e/mxcr §a打开菜单")

            # 启动持续粒子显示
            self._start_particle_loop(player, player_name)

        else:
            # 重新选择
            self._selection_mgr.set_position(player_name, pos, dim_name, 1)
            selection.pos2 = None
            player.send_message(f"§a[MXCR] §l第一个点已重新设置: §e({pos[0]}, {pos[1]}, {pos[2]})")
            player.send_message("§7请右键点击设置第二个点")
            self._selection_mgr.cancel_particle_task(player_name)
            self._spawn_point_particles(player, pos)

    # ========== 粒子效果 ==========

    def _get_particle_id(self) -> str:
        """获取当前配置的粒子 ID"""
        return self._config_mgr.particle_id

    def _spawn_point_particles(self, player, pos: tuple):
        """在指定点生成粒子效果"""
        try:
            particle = self._get_particle_id()
            x, y, z = float(pos[0]) + 0.5, float(pos[1]) + 1.0, float(pos[2]) + 0.5
            player.spawn_particle(particle, x, y, z)
        except Exception:
            pass

    def _start_particle_loop(self, player, player_name: str):
        """启动持续粒子显示循环"""
        self._selection_mgr.cancel_particle_task(player_name)

        def particle_tick():
            try:
                online_player = None
                try:
                    online_player = self.server.get_player(player_name)
                except Exception:
                    pass

                if online_player is None:
                    self._selection_mgr.cancel_particle_task(player_name)
                    return

                selection = self._selection_mgr.get_selection(player_name)
                if not selection.is_complete():
                    if selection.pos1 is not None:
                        self._spawn_point_particles(online_player, selection.pos1)
                    return

                self._spawn_selection_particles(online_player, selection)
            except Exception:
                pass

        # period=10 每 10 tick (0.5秒) 执行一次
        task = self.server.scheduler.run_task(self, particle_tick, delay=0, period=10)
        self._selection_mgr.set_particle_task(player_name, task)

    def _spawn_selection_particles(self, player, selection):
        """在选区边界生成 3D 粒子效果"""
        try:
            min_pos, max_pos = selection.get_bounds()
            particle = self._get_particle_id()

            x1 = float(min_pos[0])
            y1 = float(min_pos[1])
            z1 = float(min_pos[2])
            x2 = float(max_pos[0]) + 1.0
            y2 = float(max_pos[1]) + 1.0
            z2 = float(max_pos[2]) + 1.0

            max_dim = max(x2 - x1, y2 - y1, z2 - z1)
            if max_dim <= 16:
                step = 1.0
            elif max_dim <= 64:
                step = 2.0
            elif max_dim <= 128:
                step = 4.0
            else:
                step = 8.0

            # 沿 X 轴的 4 条边
            x = x1
            while x <= x2:
                player.spawn_particle(particle, x, y1, z1)
                player.spawn_particle(particle, x, y2, z1)
                player.spawn_particle(particle, x, y1, z2)
                player.spawn_particle(particle, x, y2, z2)
                x += step

            # 沿 Y 轴的 4 条边
            y = y1
            while y <= y2:
                player.spawn_particle(particle, x1, y, z1)
                player.spawn_particle(particle, x2, y, z1)
                player.spawn_particle(particle, x1, y, z2)
                player.spawn_particle(particle, x2, y, z2)
                y += step

            # 沿 Z 轴的 4 条边
            z = z1
            while z <= z2:
                player.spawn_particle(particle, x1, y1, z)
                player.spawn_particle(particle, x2, y1, z)
                player.spawn_particle(particle, x1, y2, z)
                player.spawn_particle(particle, x2, y2, z)
                z += step

        except Exception:
            pass

    # ========== 核心操作 (供 form_manager 调用) ==========

    def start_selection_for_player(self, player):
        """开始选区"""
        player_name = player.name
        self._selection_mgr.start_selection(player_name)
        player.send_message("§a[MXCR] §l已开始选区模式!")
        player.send_message("§7请手持 §e木斧§7,§b右键点击§7两个方块来选择 3D 区域")

    def confirm_selection_for_player(self, player):
        """确认选区并打开保存表单"""
        player_name = player.name
        selection = self._selection_mgr.get_selection(player_name)

        if not selection.is_complete():
            player.send_message("§c[MXCR] 选区未完成! 请先选择两个点")
            return

        # 检查保存上限
        if not self._check_save_limit(player):
            limit = self._config_mgr.authorized_save_limit
            player.send_message(f"§c[MXCR] 你已达到保存上限 ({limit} 个建筑)!")
            player.send_message("§7请删除一些旧建筑后再保存")
            return

        # 显示保存表单
        self._form_mgr.show_selection_confirm(player)

    def cancel_selection_for_player(self, player):
        """取消选区"""
        player_name = player.name
        self._selection_mgr.clear_selection(player_name)
        player.send_message("§e[MXCR] 选区已取消")

    def stop_selection_for_player(self, player):
        """停止选区（清除选区和粒子效果）"""
        player_name = player.name
        self._selection_mgr.clear_selection(player_name)
        player.send_message("§a[MXCR] 已停止选区并清除粒子效果")

    # ================================================================
    #  保存 (StructBridge 引擎导出)
    # ================================================================

    def save_structure_for_player(self, player, name: str, relocate: bool = False):
        """
        保存建筑：引擎原生导出选区为 .mcstructure / .bundle。

        区块加载由 StructBridge 自行处理（tickingarea + 必要时传送本玩家）。
        """
        player_name = player.name
        selection = self._selection_mgr.get_selection(player_name)

        if not selection.is_complete():
            player.send_message("§c[MXCR] 选区未完成!")
            return

        if not self._require_backend(player):
            return

        # v1.1.0: 搬迁模式权限校验
        if relocate and not self._check_normal_permission(player, "allow_move_mode"):
            player.send_message("§c[MXCR] 你没有使用搬迁模式的权限!")
            return

        min_pos, max_pos = selection.get_bounds()
        volume = selection.get_volume()
        dim_name = player.dimension.name

        # 引擎 Y 轴上限 384 格（structbridge 硬限制）
        if (max_pos[1] - min_pos[1] + 1) > 384:
            player.send_message(f"§c[MXCR] 选区 Y 轴高度 ({max_pos[1] - min_pos[1] + 1}) 超过引擎上限 384!")
            return

        # 方块数限制（v1.1.0: 按选区体积计，引擎为全量捕获）
        max_vol = self._config_mgr.max_block_count
        normal_copy_limit = self._get_normal_limit(
            player, "max_blocks_per_copy", default=max_vol
        )
        if normal_copy_limit > 0:
            max_vol = min(max_vol, normal_copy_limit)
        if volume > max_vol:
            player.send_message(f"§c[MXCR] 选区体积 ({volume}) 超过最大限制 ({max_vol})!")
            return

        # 保存数量上限
        if not self._check_save_limit(player):
            player.send_message("§c[MXCR] 你已达到保存上限，请先删除一些旧建筑!")
            return

        structure_id, sb_key = new_building_key()
        is_bundle = is_bundle_region(min_pos[0], min_pos[2], max_pos[0], max_pos[2])
        bounds = (min_pos[0], min_pos[1], min_pos[2], max_pos[0], max_pos[1], max_pos[2])

        from endstone.boss import BarColor
        self._progress_mgr.show_progress(
            player, "§e正在通过引擎复制建筑...", 0.1, BarColor.YELLOW
        )
        player.send_message("§e[MXCR] §l正在复制建筑,请稍候...")
        if is_bundle:
            player.send_message("§7区域超过 64x64，将自动分片为结构束保存")

        def on_export_done(stage: int, message: str):
            p = self.server.get_player(player_name)
            try:
                if p is None:
                    self._progress_mgr.hide_progress_by_name(player_name)
                    self.logger.info(f"Player {player_name} offline, save cancelled")
                    return

                if stage != STAGE_OK:
                    self._progress_mgr.hide_progress(p)
                    p.send_message(f"§c[MXCR] 复制失败: {self._backend.stage_message(stage)}")
                    p.send_message(f"§7{message}")
                    self.logger.warning(f"[MXCR] export failed: {message}")
                    return

                # 写入元数据
                meta = StructureMetadata(
                    structure_id=structure_id,
                    sb_key=sb_key,
                    name=name,
                    min_x=bounds[0], min_y=bounds[1], min_z=bounds[2],
                    max_x=bounds[3], max_y=bounds[4], max_z=bounds[5],
                    timestamp=int(time.time()),
                    player_uuid=str(player.unique_id),
                    player_name=player_name,
                    dimension=dim_name,
                    is_bundle=is_bundle,
                )
                self._store.add_structure(meta)

                self._progress_mgr.update_progress(p, 0.9, "§a保存完成!")
                p.send_message(f"§a[MXCR] §l建筑 §e{name} §a已保存!")
                p.send_message(
                    f"§f区域体积 §e{volume} §f(ID: §b{structure_id}§f)"
                    + (" §7[结构束]" if is_bundle else "")
                )

                # 搬迁模式 - 保存后清除原区域
                if relocate:
                    self._progress_mgr.update_progress(p, 0.95, "§e正在清除原区域...")
                    p.send_message("§e[MXCR] 搬迁模式: 正在清除原区域...")
                    # 撤销条目：恢复 = 把刚保存的建筑贴回原区域
                    entry = UndoEntry(
                        restore_key=sb_key, bounds=bounds,
                        dimension=dim_name, description=f"搬迁建筑 {name}",
                    )
                    self._store.push_undo_entry("undo", player_name, entry)
                    self._clear_region_async(
                        p, bounds, dim_name,
                        on_done=lambda ok, cleared: self._on_relocate_cleared(
                            player_name, ok, cleared),
                    )
                    return  # 进度条由搬迁流程收尾

                # 清除选区和粒子
                self._selection_mgr.clear_selection(player_name)
                self._finish_progress(player_name, 1.0, "§a保存完成!")

            except Exception as e:
                self.logger.error(f"Failed to finish save: {e}")
                self._progress_mgr.hide_progress_by_name(player_name)

        job = self._backend.export_region(
            sb_key, min_pos, max_pos, dim_name,
            tp_player=player_name, on_done=on_export_done,
        )
        if job == 0:
            self._progress_mgr.hide_progress(player)
            player.send_message("§c[MXCR] 复制请求被拒绝，请检查选区后重试!")

    def _on_relocate_cleared(self, player_name: str, ok: bool, cleared: int):
        """搬迁清除完成回调"""
        p = self.server.get_player(player_name)
        if p is not None:
            if ok:
                p.send_message(f"§a[MXCR] 已清除 §e{cleared} §a个方块 (搬迁完成，可用 /mxcr undo 恢复)")
            else:
                p.send_message("§c[MXCR] 原区域清除未完全完成，请手动检查!")
            self._selection_mgr.clear_selection(player_name)
        self._progress_mgr.hide_progress_by_name(player_name)

    # ---------- 搬迁模式: 分片 /fill 清除区域 ----------

    def _plan_fill_slices(self, bounds: tuple):
        """
        把区域切成不超过 FILL_LIMIT 体积的填充片。
        返回 [(x1,y1,z1,x2,y2,z2), ...]
        """
        x1, y1, z1, x2, y2, z2 = bounds
        dy = y2 - y1 + 1
        dz = z2 - z1 + 1
        dx_max = max(1, self.FILL_LIMIT // (dy * dz))
        slices = []
        x = x1
        while x <= x2:
            xe = min(x + dx_max - 1, x2)
            slices.append((x, y1, z1, xe, y2, z2))
            x = xe + 1
        return slices

    def _region_chunks_loaded(self, dim, bounds: tuple) -> bool:
        """检查区域覆盖的所有区块是否已加载。"""
        x1, _, z1, x2, _, z2 = bounds
        try:
            loaded = {((c.x), (c.z)) for c in dim.loaded_chunks}
        except Exception:
            # API 不可用时按已加载处理，交给 fill 自行容忍
            return True
        for cx in range(x1 >> 4, (x2 >> 4) + 1):
            for cz in range(z1 >> 4, (z2 >> 4) + 1):
                if (cx, cz) not in loaded:
                    return False
        return True

    def _clear_region_async(self, player, bounds: tuple, dim_name: str, on_done):
        """
        清除区域为空气（搬迁模式）。

        策略：若区域区块未全部加载，先把玩家传送到区域中心等待加载，
        然后逐片派发 /fill air（每片间隔 2 tick 防止命令洪泛），完成后送回玩家。
        """
        player_name = player.name
        dim = self.server.level.get_dimension(dim_name)
        original_loc = player.location
        cx = (bounds[0] + bounds[3]) // 2
        cy = bounds[4] + 1
        cz = (bounds[2] + bounds[5]) // 2
        need_tp = not self._region_chunks_loaded(dim, bounds)
        teleported = [False]

        def dispatch_fills():
            slices = self._plan_fill_slices(bounds)
            state = {"i": 0, "cleared": 0}

            def next_fill():
                p = self.server.get_player(player_name)
                if p is None:
                    # 玩家离线：fill 不依赖玩家，继续派发
                    pass
                if state["i"] >= len(slices):
                    # 全部派发完成
                    if teleported[0]:
                        tp = self.server.get_player(player_name)
                        if tp is not None:
                            tp.teleport(original_loc)
                    on_done(True, state["cleared"])
                    return
                s = slices[state["i"]]
                state["i"] += 1
                state["cleared"] += (
                    (s[3] - s[0] + 1) * (s[4] - s[1] + 1) * (s[5] - s[2] + 1)
                )
                cmd = f"fill {s[0]} {s[1]} {s[2]} {s[3]} {s[4]} {s[5]} air"
                try:
                    self.server.dispatch_command(self.server.command_sender, cmd)
                except Exception as e:
                    self.logger.warning(f"[MXCR] fill 派发失败: {e}")
                self.server.scheduler.run_task(self, next_fill, delay=2)

            next_fill()

        if not need_tp:
            dispatch_fills()
            return

        # 传送到区域中心，轮询等待区块加载
        from endstone.level import Location
        try:
            player.teleport(Location(dim, float(cx), float(cy), float(cz)))
            teleported[0] = True
        except Exception as e:
            self.logger.warning(f"[MXCR] 搬迁传送失败: {e}")

        attempts = [0]
        max_attempts = 40  # 40 * 10 tick = 20 秒

        def wait_tick():
            attempts[0] += 1
            d = self.server.level.get_dimension(dim_name)
            if self._region_chunks_loaded(d, bounds):
                dispatch_fills()
                return
            if attempts[0] >= max_attempts:
                p = self.server.get_player(player_name)
                if p is not None:
                    p.send_message("§e[MXCR] 区块加载超时，将尽力清除已加载部分...")
                dispatch_fills()
                return
            self.server.scheduler.run_task(self, wait_tick, delay=10)

        self.server.scheduler.run_task(self, wait_tick, delay=10)

    # ================================================================
    #  加载/粘贴 (StructBridge 引擎放置)
    # ================================================================

    def load_structure_for_player(self, player, structure_id: str,
                                   target_pos: tuple, source_type: str = "player"):
        """
        粘贴建筑：先引擎导出目标区域快照（用于撤销），再放置建筑。

        Args:
            target_pos: 新包围盒最小角
            source_type: "player"(自己的) / "public"(公开) / "admin"(任意，管理员)
        """
        player_name = player.name
        if not self._require_backend(player):
            return

        # 权限与归属校验
        if source_type == "public":
            if not self._check_normal_permission(player, "allow_public_load"):
                player.send_message("§c[MXCR] 你没有加载公开建筑的权限!")
                return
            meta = self._store.list_public_structures()
            meta = next((m for m in meta if m.structure_id == structure_id), None)
        elif source_type == "admin":
            if not self._perm_mgr.is_game_admin(player.name, player.is_op):
                player.send_message("§c[MXCR] 只有管理员可以执行此操作!")
                return
            meta = self._store.get_structure(structure_id)
        else:
            if not self._check_normal_permission(player, "allow_paste"):
                player.send_message("§c[MXCR] 你没有粘贴建筑的权限!")
                return
            meta = self._store.get_owned_structure(
                str(player.unique_id), player_name, structure_id
            )

        if meta is None:
            player.send_message("§c[MXCR] 建筑不存在!")
            return

        # 目标维度 = 玩家当前维度
        dim_name = player.dimension.name
        size = meta.get_size()
        target_bounds = (
            int(target_pos[0]), int(target_pos[1]), int(target_pos[2]),
            int(target_pos[0]) + size[0] - 1,
            int(target_pos[1]) + size[1] - 1,
            int(target_pos[2]) + size[2] - 1,
        )

        from endstone.boss import BarColor
        self._progress_mgr.show_progress(player, "§a正在粘贴建筑...", 0.1, BarColor.GREEN)
        player.send_message("§a[MXCR] §l正在粘贴建筑,请稍候...")

        if meta.dimension != dim_name:
            player.send_message(
                f"§e[MXCR] 注意: 建筑原维度为 {meta.dimension}，将粘贴到当前维度 {dim_name}"
            )

        def do_place():
            self._paste_with_snapshot(
                player_name=player_name,
                sb_key=meta.sb_key,
                structure_name=meta.name,
                target_bounds=target_bounds,
                dim_name=dim_name,
                tp_player=player_name,
            )

        # 延迟 2 tick，避免阻塞表单回调线程
        self.server.scheduler.run_task(self, do_place, delay=2)

    def _paste_with_snapshot(self, player_name: str, sb_key: str,
                              structure_name: str, target_bounds: tuple,
                              dim_name: str, tp_player: str):
        """
        粘贴流程核心：
        1. 引擎导出目标区域当前状态 -> 撤销快照
        2. 引擎放置建筑
        3. 记录撤销条目（恢复 = 贴回快照）
        快照失败时警告并继续放置（该操作将不可撤销）。
        """
        from endstone.boss import BarColor

        snap_key = new_snapshot_key()
        snap_min = (target_bounds[0], target_bounds[1], target_bounds[2])
        snap_max = (target_bounds[3], target_bounds[4], target_bounds[5])

        def after_snapshot(stage: int, message: str):
            p = self.server.get_player(player_name)
            try:
                if p is None:
                    self._progress_mgr.hide_progress_by_name(player_name)
                    return

                snap_ok = stage == STAGE_OK
                if not snap_ok:
                    # 继续放置，但本操作不可撤销
                    p.send_message("§e[MXCR] 目标区域快照失败，本次粘贴将无法撤销!")
                    self.logger.warning(f"[MXCR] snapshot failed: {message}")

                self._progress_mgr.update_progress(p, 0.5, "§a正在放置方块...")

                def after_place(pstage: int, pmessage: str):
                    p2 = self.server.get_player(player_name)
                    try:
                        if p2 is None:
                            self._progress_mgr.hide_progress_by_name(player_name)
                            return
                        if pstage != STAGE_OK:
                            self._progress_mgr.hide_progress(p2)
                            p2.send_message(
                                f"§c[MXCR] 粘贴失败: {self._backend.stage_message(pstage)}")
                            p2.send_message(f"§7{pmessage}")
                            return
                        if snap_ok:
                            entry = UndoEntry(
                                restore_key=snap_key, bounds=target_bounds,
                                dimension=dim_name,
                                description=f"粘贴建筑 {structure_name}",
                            )
                            self._store.push_undo_entry("undo", player_name, entry)
                        self._finish_progress(player_name, 1.0, "§a粘贴完成!")
                        p2.send_message(f"§a[MXCR] §l建筑 §e{structure_name} §a已粘贴!")
                        p2.send_message("§7可用 /mxcr undo 撤销此操作")
                    except Exception as e:
                        self.logger.error(f"Failed to finish place: {e}")
                        self._progress_mgr.hide_progress_by_name(player_name)

                job = self._backend.place_structure(
                    sb_key, snap_min, dim_name,
                    tp_player=tp_player, on_done=after_place,
                )
                if job == 0:
                    self._progress_mgr.hide_progress(p)
                    p.send_message("§c[MXCR] 放置请求被拒绝!")
            except Exception as e:
                self.logger.error(f"Failed to place: {e}")
                self._progress_mgr.hide_progress_by_name(player_name)

        job = self._backend.export_region(
            snap_key, snap_min, snap_max, dim_name,
            tp_player=tp_player, on_done=after_snapshot,
        )
        if job == 0:
            # 快照请求被拒（不常见）：直接尝试放置
            p = self.server.get_player(player_name)
            if p is not None:
                p.send_message("§e[MXCR] 目标区域快照请求被拒，本次粘贴将无法撤销!")
            after_snapshot(-1, "export request rejected")

    # ================================================================
    #  删除
    # ================================================================

    def delete_structure_for_player(self, player, structure_id: str):
        """删除自己的建筑"""
        try:
            if not self._check_normal_permission(player, "allow_delete_own"):
                player.send_message("§c[MXCR] 你没有删除建筑的权限!")
                return

            meta = self._store.get_owned_structure(
                str(player.unique_id), player.name, structure_id
            )
            if meta is None:
                player.send_message("§c[MXCR] 建筑不存在!")
                return

            self._delete_structure_internal(meta)
            player.send_message(f"§a[MXCR] 建筑 §e{meta.name} §a已删除!")

        except Exception as e:
            player.send_message(f"§c[MXCR] 删除失败: {str(e)}")
            self.logger.error(f"Failed to delete structure: {e}")

    def admin_delete_structure(self, player, structure_id: str):
        """管理员删除任意建筑"""
        try:
            if not self._perm_mgr.is_game_admin(player.name, player.is_op):
                player.send_message("§c[MXCR] 只有管理员可以执行此操作!")
                return
            meta = self._store.get_structure(structure_id)
            if meta is None:
                player.send_message("§c[MXCR] 建筑不存在!")
                return
            self._delete_structure_internal(meta)
            player.send_message(f"§a[MXCR] 建筑 §e{meta.name} §a已删除!")
        except Exception as e:
            player.send_message(f"§c[MXCR] 删除失败: {str(e)}")
            self.logger.error(f"Failed to admin delete structure: {e}")

    def _delete_structure_internal(self, meta: StructureMetadata):
        """删除元数据 + 结构文件（库文件/镜像/引擎模板）。"""
        sb_key = self._store.delete_structure(meta.structure_id)
        if sb_key is None:
            return
        ok, msg = self._backend.delete_structure(sb_key)
        if not ok:
            # 元数据已删，文件残留仅记录
            self.logger.warning(f"[MXCR] 结构文件清理未确认: {msg}")

    def _gc_snapshot_file(self, sb_key: str):
        """撤销栈淘汰时清理快照文件（仅 mxcrsnap_*）。"""
        from .structbridge_backend import is_snapshot_key
        if not is_snapshot_key(sb_key):
            return
        if self._store.snapshot_key_referenced(sb_key):
            return
        ok, msg = self._backend.delete_structure(sb_key)
        if not ok:
            self.logger.warning(f"[MXCR] 快照清理失败 ({sb_key}): {msg}")

    # ================================================================
    #  公开 / 导入 (管理员)
    # ================================================================

    def publish_structure(self, player, structure_id: str) -> bool:
        """公开自己的建筑"""
        if not self._check_normal_permission(player, "allow_public_share"):
            player.send_message("§c[MXCR] 你没有公开建筑的权限!")
            return False
        meta = self._store.get_owned_structure(
            str(player.unique_id), player.name, structure_id)
        if meta is None:
            player.send_message("§c[MXCR] 建筑不存在!")
            return False
        return self._store.set_public(structure_id, True)

    def unpublish_structure(self, structure_id: str) -> bool:
        """管理员取消公开"""
        return self._store.set_public(structure_id, False)

    def admin_publish_structure(self, player, structure_id: str) -> bool:
        """管理员公开任意建筑"""
        if not self._perm_mgr.is_game_admin(player.name, player.is_op):
            return False
        return self._store.set_public(structure_id, True)

    def import_structure_to_me(self, player, structure_id: str) -> bool:
        """管理员将建筑转入自己名下（文件不动，键名不变）"""
        if not self._perm_mgr.is_game_admin(player.name, player.is_op):
            return False
        return self._store.transfer_ownership(
            structure_id, str(player.unique_id), player.name)

    # ================================================================
    #  撤销 / 重做 (快照式)
    # ================================================================

    def undo_operation(self, player):
        """撤销上一步操作"""
        player_name = player.name
        if not self._check_normal_permission(player, "allow_undo_redo"):
            player.send_message("§c[MXCR] 你没有撤销权限!")
            return
        if not self._require_backend(player):
            return

        entry = self._store.pop_undo_entry("undo", player_name)
        if entry is None:
            player.send_message("§e[MXCR] 没有可撤销的操作")
            return

        player.send_message(f"§e[MXCR] 正在撤销: {entry.description}...")
        self._restore_entry(player, entry, push_to="redo", redo_of=False)

    def redo_operation(self, player):
        """重做被撤销的操作"""
        player_name = player.name
        if not self._check_normal_permission(player, "allow_undo_redo"):
            player.send_message("§c[MXCR] 你没有重做权限!")
            return
        if not self._require_backend(player):
            return

        entry = self._store.pop_undo_entry("redo", player_name)
        if entry is None:
            player.send_message("§e[MXCR] 没有可重做的操作")
            return

        player.send_message(f"§a[MXCR] 正在重做: {entry.description}...")
        self._restore_entry(player, entry, push_to="undo", redo_of=True)

    def _restore_entry(self, player, entry: UndoEntry, push_to: str, redo_of: bool):
        """
        通用恢复流程（撤销与重做对称）：
        1. 引擎导出区域当前状态 -> 反向条目压入对侧栈
        2. 引擎放置 entry.restore_key 回区域
        放置失败时把条目压回原栈，避免记录丢失。
        """
        player_name = player.name
        dim_name = entry.dimension
        bounds = entry.bounds
        min_pos = (bounds[0], bounds[1], bounds[2])
        max_pos = (bounds[3], bounds[4], bounds[5])

        from endstone.boss import BarColor
        color = BarColor.GREEN if redo_of else BarColor.YELLOW
        self._progress_mgr.show_progress(
            player, f"§{'a' if redo_of else 'e'}{entry.description}...", 0.1, color
        )

        # 快照键提前生成：即使导出失败也复用同一键名逻辑
        inverse_key = new_snapshot_key()

        def after_snapshot(stage: int, message: str):
            p = self.server.get_player(player_name)
            try:
                if p is None:
                    self._progress_mgr.hide_progress_by_name(player_name)
                    return

                snap_ok = stage == STAGE_OK
                if not snap_ok:
                    p.send_message("§e[MXCR] 区域快照失败，反向操作将不可用!")
                    self.logger.warning(f"[MXCR] undo snapshot failed: {message}")

                self._progress_mgr.update_progress(p, 0.5, "§a正在恢复区域...")

                def after_place(pstage: int, pmessage: str):
                    p2 = self.server.get_player(player_name)
                    try:
                        if p2 is None:
                            self._progress_mgr.hide_progress_by_name(player_name)
                            return
                        if pstage != STAGE_OK:
                            # 放置失败：条目压回原栈
                            origin_stack = "undo" if push_to == "redo" else "redo"
                            self._store.push_undo_entry(origin_stack, player_name, entry)
                            self._progress_mgr.hide_progress(p2)
                            p2.send_message(
                                f"§c[MXCR] 恢复失败: {self._backend.stage_message(pstage)}")
                            p2.send_message("§7记录已保留，可稍后重试")
                            return
                        if snap_ok:
                            inverse = UndoEntry(
                                restore_key=inverse_key, bounds=bounds,
                                dimension=dim_name,
                                description=("重做:" if push_to == "undo" else "撤销:")
                                            + entry.description,
                            )
                            self._store.push_undo_entry(push_to, player_name, inverse)
                        self._finish_progress(player_name, 1.0, "§a操作完成!")
                        p2.send_message("§a[MXCR] §l操作完成!")
                    except Exception as e:
                        self.logger.error(f"Failed to finish restore: {e}")
                        self._progress_mgr.hide_progress_by_name(player_name)

                job = self._backend.place_structure(
                    entry.restore_key, min_pos, dim_name,
                    tp_player=player_name, on_done=after_place,
                )
                if job == 0:
                    origin_stack = "undo" if push_to == "redo" else "redo"
                    self._store.push_undo_entry(origin_stack, player_name, entry)
                    self._progress_mgr.hide_progress(p)
                    p.send_message("§c[MXCR] 恢复请求被拒绝，记录已保留!")
            except Exception as e:
                self.logger.error(f"Failed to restore: {e}")
                self._progress_mgr.hide_progress_by_name(player_name)

        job = self._backend.export_region(
            inverse_key, min_pos, max_pos, dim_name,
            tp_player=player_name, on_done=after_snapshot,
        )
        if job == 0:
            # 快照被拒：仍尝试恢复（反向条目缺失）
            p = self.server.get_player(player_name)
            if p is not None:
                p.send_message("§e[MXCR] 区域快照请求被拒，本次操作将不可回退!")
            after_snapshot(-1, "export request rejected")

    # ================================================================
    #  刷新区块
    # ================================================================

    def refresh_chunks_for_player(self, player):
        """刷新区块 - 通过传送玩家到远处再传送回来实现"""
        try:
            # v1.0.0: 刷新区块权限检查
            if not self._check_normal_permission(player, "allow_refresh_chunks"):
                player.send_message("§c[MXCR] 你没有刷新区块的权限!")
                return

            from endstone.level import Location

            original_loc = player.location
            dim = player.dimension

            # 传送到远处 (偏移 1000 格)
            far_x = original_loc.x + 1000.0
            far_z = original_loc.z + 1000.0
            far_loc = Location(dim, far_x, original_loc.y + 50.0, far_z)

            player.send_message("§e[MXCR] 正在刷新区块...")
            player.teleport(far_loc)

            # 1.5 秒后传送回来
            def teleport_back():
                try:
                    online_player = self.server.get_player(player.name)
                    if online_player is not None:
                        online_player.teleport(original_loc)
                        online_player.send_message("§a[MXCR] 区块刷新完成!")
                except Exception:
                    pass

            self.server.scheduler.run_task(self, teleport_back, delay=30)

        except Exception as e:
            player.send_message(f"§c[MXCR] 刷新区块失败: {str(e)}")

    # ================================================================
    #  预览面板切换
    # ================================================================

    def toggle_preview_panel(self, player):
        """切换预览面板显示/隐藏"""
        player_name = player.name

        # 检查是否在预览状态
        if player_name not in self.preview_manager.active_previews:
            player.send_message("§e[MXCR] 当前没有进行预览")
            return

        # 显示预览面板
        self.preview_manager._show_adjustment_panel(player)
        player.send_message("§a[MXCR] 预览面板已打开")

    # ================================================================
    #  后端状态 (/mxcr bridge)
    # ================================================================

    def _check_bridge_status(self, player):
        """报告 StructBridge 后端连接状态（v1.1.0: 替代原 SAPI 桥接检测）"""
        ok, msg = self._backend.ensure()
        if not ok:
            player.send_message("§c[MXCR 后端] StructBridge 不可用!")
            player.send_message("§c请确认:")
            player.send_message("§f  1. StructBridge 插件 (endstone_structbridge.dll/.so) 已放入 plugins/")
            player.send_message("§f  2. StructBridge 为最新版 (v0.0.3+，Windows 需含 Python API)")
            player.send_message(f"§f  3. 详细错误: {self._backend.load_error or msg}")
            return

        status = self._backend.status()
        count = status.get("structures", "?")
        player.send_message("§a[MXCR 后端] §lStructBridge 连接正常!")
        player.send_message(f"§f  版本: §e{self._backend.version}")
        player.send_message(f"§f  引擎可见结构数: §e{count}")
        player.send_message("§7保存/加载/撤销功能已就绪 (引擎原生序列化，含容器物品与实体)")

    # ================================================================
    #  进度条收尾辅助
    # ================================================================

    def _finish_progress(self, player_name: str, progress: float, text: str):
        """更新进度到完成态并延迟隐藏"""
        p = self.server.get_player(player_name)
        if p is not None:
            self._progress_mgr.update_progress(p, progress, text)

        def hide_bar():
            self._progress_mgr.hide_progress_by_name(player_name)

        self.server.scheduler.run_task(self, hide_bar, delay=30)

    # ================================================================
    #  事件处理: 玩家退出
    # ================================================================

    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent):
        """
        处理玩家退出事件
        统一清理: 选区数据、粒子任务、预览状态、进度条
        """
        player = event.player
        player_name = player.name

        # 1. 清理选区数据和粒子任务
        try:
            self._selection_mgr.clear_selection(player_name)
        except Exception:
            pass

        # 2. 清理预览状态
        try:
            if player_name in self.preview_manager.active_previews:
                self.logger.info(f"Player {player_name} quit, stopping preview")
                self.preview_manager.stop_preview(player)
        except Exception:
            pass

        # 3. 清理进度条
        try:
            if self._progress_mgr.has_progress(player):
                self._progress_mgr.hide_progress(player)
        except Exception:
            pass
