"""
MXCR 主插件类 v1.0.0
严格基于 Endstone v0.11.0 官方文档开发

已确认的导入路径:
- from endstone.plugin import Plugin
- from endstone.command import Command, CommandSender
- from endstone.event import event_handler, PlayerInteractEvent, PlayerQuitEvent, ScriptMessageEvent
- from endstone.form import ActionForm, ModalForm, TextInput, Toggle, Dropdown, Label, MessageForm
- from endstone.level import Location, Dimension
- from endstone.block import Block, BlockData, BlockState

Player 定义在 endstone/__init__.pyi 中,通过 hasattr 判断

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
- Player.send_popup(message: str) -> None
- Player.perform_command(command: str) -> bool
- Server.online_players -> list[Player]
- Server.get_player(name: str) -> Player
- Server.scheduler -> Scheduler
- Server.dispatch_command(sender, command_line)
- Scheduler.run_task(plugin, task, delay=0, period=0) -> Task
- Task.cancel() -> None
- Task.is_cancelled -> bool
"""
import json
import uuid
from endstone.plugin import Plugin
from endstone.command import Command, CommandSender
from endstone.event import event_handler, PlayerInteractEvent, PlayerQuitEvent, ScriptMessageEvent

from .selection_manager import SelectionManager
from .database_manager import DatabaseManager
from .config_manager import ConfigManager, PermissionManager
from .structure_operator import StructureOperator
from .form_manager import FormManager
from .undo_manager import UndoManager, BlockRecord, UndoAction
from .structure_data import BlockInfo, EntityInfo
from .progress_manager import ProgressManager
from .chunk_loader import ChunkLoader
from .normal_permission_manager import NormalPermissionManager
from .preview_manager import PreviewManager
from .container_manager import ContainerManager
from .entity_manager import EntityManager


class MXCRPlugin(Plugin):
    """MXCR 建筑复制工具插件 v1.0.0"""

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

    def on_enable(self) -> None:
        """插件启用时调用"""
        self.logger.info("MXCR 建筑复制工具 v1.0.0 正在启用...")

        # data_folder 已经是 pathlib.Path 类型
        data_dir = self.data_folder
        data_dir.mkdir(parents=True, exist_ok=True)

        # 初始化各管理器
        self._selection_mgr = SelectionManager()
        self._db_mgr = DatabaseManager(data_dir / "data")
        self._config_mgr = ConfigManager(data_dir)
        self._perm_mgr = PermissionManager(data_dir)
        self._normal_perm_mgr = NormalPermissionManager(data_dir, data_dir / "permissions.json")
        self._preview_mgr = PreviewManager(self)
        self._struct_op = StructureOperator(self._db_mgr, self._config_mgr, self.server)
        self._undo_mgr = UndoManager(max_history=10, data_dir=data_dir)
        self._progress_mgr = ProgressManager(self.server)
        self._chunk_loader = ChunkLoader(self)
        self._form_mgr = FormManager(self)
        self._container_mgr = ContainerManager(self)
        # 将 ContainerManager 注入到 StructureOperator
        self._struct_op.container_manager = self._container_mgr

        # 初始化实体管理器，并注入到 StructureOperator
        self._entity_mgr = EntityManager(self)
        self._struct_op.entity_manager = self._entity_mgr

        # 注册事件监听器
        self.register_events(self)

        # 启动容器管理器+实体管理器超时清理定时任务（每 tick 执行）
        self.server.scheduler.run_task(self, self._container_tick, delay=0, period=1)

        self.logger.info("MXCR 建筑复制工具 v1.0.0 已启用!")

    def on_disable(self) -> None:
        """插件禁用时调用"""
        try:
            self.server.scheduler.cancel_tasks(self)
        except Exception:
            pass
        # v1.0.0: 清理磁盘化撤销缓存
        try:
            self._undo_mgr.shutdown()
        except Exception:
            pass
        self.logger.info("MXCR 建筑复制工具已禁用!")

    # ========== 属性访问器 (与 form_manager 中的调用名对齐) ==========

    @property
    def selection_manager(self) -> SelectionManager:
        return self._selection_mgr

    @property
    def db_manager(self) -> DatabaseManager:
        return self._db_mgr

    @property
    def config_manager(self) -> ConfigManager:
        return self._config_mgr

    @property
    def perm_manager(self) -> PermissionManager:
        return self._perm_mgr

    @property
    def structure_operator(self) -> StructureOperator:
        return self._struct_op

    @property
    def form_manager(self) -> FormManager:
        return self._form_mgr

    @property
    def undo_manager(self) -> UndoManager:
        return self._undo_mgr

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
    def container_manager(self) -> ContainerManager:
        return self._container_mgr

    @property
    def entity_manager(self) -> EntityManager:
        return self._entity_mgr

    @property
    def chunk_loader(self) -> ChunkLoader:
        return self._chunk_loader

    # ========== 权限检查 ==========

    def _check_full_permission(self, player) -> bool:
        """检查玩家是否有完整权限 (OP / 授权玩家 / 游戏管理员)"""
        return self._perm_mgr.is_authorized(player.name, player.is_op)

    def _check_copy_permission(self, player) -> bool:
        """检查玩家是否有复制(保存)权限"""
        # 已授权玩家（OP/授权玩家/管理员）直接有权限
        if self._perm_mgr.is_authorized(player.name, player.is_op):
            return True
        # 普通玩家权限系统
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
        p_name = player.name
        count = self._db_mgr.get_structure_count(p_uuid, p_name)
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
        检查普通玩家的细粒度权限（v1.0.0 修复：此前多个权限键定义了但从未执行）

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

    # ========== 命令处理 ==========

    def on_command(self, sender: CommandSender, command: Command, args: list) -> bool:
        """
        命令处理入口（v1.0.0: 添加全局异常保护，
        任何子命令抛出异常都不会传播到服务器核心）
        """
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
                player.send_message(f"§7(服务器 OP 默认拥有所有权限)")
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
                self._check_bridge_connection(player)
                return True

            # /mxcr undo（权限校验在 undo_operation 内部，命令与表单路径统一）
            elif sub == "undo":
                self.undo_operation(player)
                return True

            # /mxcr redo（权限校验在 redo_operation 内部，命令与表单路径统一）
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

        player_uuid_str = str(player.unique_id)
        player_name = player.name

        # 初始化玩家数据文件夹
        self._db_mgr.init_player_dir(player_uuid_str, player_name)

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
            player.send_message("§7用法: /mxcr [start|ok|cancel|list|op|deop|oplist|admin|deadmin|reload]")

        return True

    # ========== 事件处理 ==========

    @event_handler
    def on_player_interact(self, event: PlayerInteractEvent):
        """玩家交互事件处理（v1.0.0: 添加全局异常保护）"""
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
                    # 初始化玩家数据文件夹
                    self._db_mgr.init_player_dir(str(player.unique_id), player_name)
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

    # 注意: on_player_quit 已合并到文件末尾的统一事件处理器中

    # ========== 粒子效果 ===========

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

    def save_structure_for_player(self, player, name: str, preserve_air: bool,
                                   relocate: bool = False, excluded_blocks: list = None,
                                   preserve_water: bool = True, preserve_lava: bool = True,
                                   reliable_mode: bool = False):
        """保存建筑"""
        player_name = player.name
        selection = self._selection_mgr.get_selection(player_name)

        if not selection.is_complete():
            player.send_message("§c[MXCR] 选区未完成!")
            return

        player_uuid_str = str(player.unique_id)
        dimension = player.dimension

        # 获取排除方块列表
        excluded = list(self._config_mgr.excluded_blocks)
        if excluded_blocks:
            excluded.extend(excluded_blocks)
        
        # 根据保留水和岩浆的设置添加到排除列表
        if not preserve_water:
            excluded.extend(["minecraft:water", "minecraft:flowing_water"])
        if not preserve_lava:
            excluded.extend(["minecraft:lava", "minecraft:flowing_lava"])

        # 检查方块数量限制 (智能计算)
        min_pos, max_pos = selection.get_bounds()
        volume = selection.get_volume()
        max_vol = self._config_mgr.max_block_count

        # v1.0.0: 普通玩家的单次复制方块数上限（授权玩家不受此限制）
        normal_copy_limit = self._get_normal_limit(
            player, "max_blocks_per_copy", default=max_vol
        )
        if normal_copy_limit > 0:
            max_vol = min(max_vol, normal_copy_limit)

        # 如果不保留空气,先估算实际方块数
        if not preserve_air:
            # 体积很大时跳过精确计算,直接用体积估算
            if volume <= max_vol * 2:
                actual = self._struct_op.count_actual_blocks(
                    dimension, selection, preserve_air, excluded
                )
                if actual > max_vol:
                    player.send_message(f"§c[MXCR] 实际方块数 ({actual}) 超过最大限制 ({max_vol})!")
                    return
            else:
                player.send_message(f"§c[MXCR] 选区体积 ({volume}) 过大!")
                return
        else:
            if volume > max_vol:
                player.send_message(f"§c[MXCR] 选区体积 ({volume}) 超过最大限制 ({max_vol})!")
                return

        # 检查区块加载
        if not self._struct_op.check_chunks_loaded(dimension, min_pos, max_pos):
            # 先显示进度条，再传送（避免传送前无进度条的空白期）
            from endstone.boss import BarColor as _BarColor
            self._progress_mgr.show_progress(player, "§e正在准备复制...", 0.05, _BarColor.YELLOW)
            player.send_message("§e[MXCR] 部分区块未加载,正在传送您到选区中心...")
            center = StructureOperator.get_center_position(min_pos, max_pos)

            from endstone.level import Location
            loc = Location(dimension, float(center[0]), float(center[1]), float(center[2]))
            player.teleport(loc)

            def delayed_save():
                # v1.0.0: 延迟任务中重新获取玩家对象，避免捕获过期的 player 引用
                p = self.server.get_player(player_name)
                if p is None:
                    self._progress_mgr.hide_progress_by_name(player_name)
                    self.logger.info(f"Player {player_name} offline, save cancelled")
                    return
                self._do_save(p, name, preserve_air, excluded, selection, relocate, reliable_mode)

            self.server.scheduler.run_task(self, delayed_save, delay=40)
            return

        self._do_save(player, name, preserve_air, excluded, selection, relocate, reliable_mode)

    def _do_save(self, player, name: str, preserve_air: bool,
                 excluded_blocks: list, selection, relocate: bool = False,
                 reliable_mode: bool = False):
        """执行保存操作（使用异步进度条，带容错处理）"""
        from endstone.boss import BarColor

        # 保存玩家信息（后续闭包中使用，避免访问已离线的 player 对象）
        player_name = player.name
        player_uuid_str = str(player.unique_id)
        dimension = player.dimension
        dim_name = dimension.name
        
        # 计算建筑覆盖的区块数量
        min_pos, max_pos = selection.get_bounds()
        chunks = self._chunk_loader.get_chunks_in_bounds(min_pos, max_pos)
        chunk_count = len(chunks)
        
        # 如果跨越多个区块，使用智能分块保存
        if chunk_count > 1:
            # 先显示进度条，再进入分块流程（避免传送前无进度条的空白期）
            self._progress_mgr.show_progress(
                player, f"§e正在准备复制 ({chunk_count} 个区块)...",
                0.0, BarColor.YELLOW
            )
            player.send_message("§e[MXCR] §l正在复制建筑,请稍候...")
            self._chunk_loader.save_with_chunk_loading(
                player, name, preserve_air, excluded_blocks, selection, relocate, reliable_mode
            )
            return
        
        # 单区块建筑，使用原有逻辑（增加容错）

        # 第1步：显示/更新进度条
        # 如果进度条已由 save_structure_for_player 提前创建（区块未加载情况），则更新而非重建
        if self._progress_mgr.has_progress(player):
            self._progress_mgr.update_progress(player, 0.1, "§e正在复制建筑...")
        else:
            self._progress_mgr.show_progress(player, "§e正在复制建筑...", 0.1, BarColor.YELLOW)
        player.send_message("§e[MXCR] §l正在复制建筑,请稍候...")

        # 第2步：延迟2tick后执行实际操作
        def do_copy():
            # 容错：检查玩家是否仍在线
            if self.server.get_player(player_name) is None:
                self._progress_mgr.hide_progress_by_name(player_name)
                self.logger.info(f"Player {player_name} offline, save cancelled")
                return

            try:
                p = self.server.get_player(player_name)
                dim = self.server.level.get_dimension(dim_name)

                self._progress_mgr.update_progress(p, 0.3, "§e正在扫描方块...")

                metadata, blocks, entities = self._struct_op.copy_structure(
                    dimension=dim,
                    selection=selection,
                    name=name,
                    player_uuid_str=player_uuid_str,
                    player_name=player_name,
                    preserve_air=preserve_air,
                    excluded_blocks=excluded_blocks
                )

                if entities:
                    self.logger.info(
                        f"[MXCR] 扫描到 {len(entities)} 个实体，待读取 SAPI 数据"
                    )

                # 更新进度
                self._progress_mgr.update_progress(p, 0.5, "§e正在读取容器数据...")

                # 定义保存函数（必须先于容器读取逻辑，因为回调中会引用它）
                def do_save_data():
                    # 容错：检查玩家是否仍在线
                    p2 = self.server.get_player(player_name)
                    if p2 is None:
                        self._progress_mgr.hide_progress_by_name(player_name)
                        self.logger.info(f"Player {player_name} offline during save")
                        return

                    try:
                        structure_id = self._db_mgr.save_structure(
                            player_uuid_str, player_name, metadata, blocks, entities
                        )

                        self._progress_mgr.update_progress(p2, 0.9, "§a保存完成!")

                        p2.send_message(f"§a[MXCR] §l建筑 §e{name} §a已保存!")
                        p2.send_message(f"§f共保存了 §e{len(blocks)} §f个方块 (ID: §b{structure_id}§f)")
                        if entities:
                            p2.send_message(f"§f共保存了 §e{len(entities)} §f个实体")

                        # 搬迁模式 - 保存后删除原方块
                        if relocate:
                            self._progress_mgr.update_progress(p2, 0.95, "§e正在清除原方块...")
                            p2.send_message("§e[MXCR] 搬迁模式: 正在删除原方块...")
                            min_pos, max_pos = selection.get_bounds()

                            dim2 = self.server.level.get_dimension(dim_name)
                            # 记录要删除的方块（用于撤销）
                            deleted_blocks = []
                            for x in range(min_pos[0], max_pos[0] + 1):
                                for y in range(min_pos[1], max_pos[1] + 1):
                                    for z in range(min_pos[2], max_pos[2] + 1):
                                        try:
                                            block = dim2.get_block_at(x, y, z)
                                            if block.type != "minecraft:air":
                                                bd = block.data
                                                states = dict(bd.block_states) if bd.block_states else {}
                                                deleted_blocks.append(BlockRecord(
                                                    x=x, y=y, z=z,
                                                    block_type=block.type,
                                                    block_data=0,
                                                    block_states=states
                                                ))
                                        except Exception:
                                            pass

                            cleared = self._struct_op.clear_area(dim2, min_pos, max_pos)
                            p2.send_message(f"§a[MXCR] 已清除 §e{cleared} §a个方块")

                            # 记录到撤销历史
                            if deleted_blocks:
                                self._undo_mgr.record_action(
                                    player_name=player_name,
                                    action_type="save_relocate",
                                    blocks=deleted_blocks,
                                    dimension=dim_name,
                                    description=f"搬迁建筑 {name}"
                                )

                        # 清除选区和粒子
                        self._selection_mgr.clear_selection(player_name)

                        # 延迟隐藏进度条（让玩家看到完成状态）
                        def hide_bar():
                            try:
                                p3 = self.server.get_player(player_name)
                                if p3 is not None:
                                    self._progress_mgr.hide_progress(p3)
                                else:
                                    self._progress_mgr.hide_progress_by_name(player_name)
                            except Exception:
                                self._progress_mgr.hide_progress_by_name(player_name)
                        self.server.scheduler.run_task(self, hide_bar, delay=30)

                    except Exception as e:
                        try:
                            p2 = self.server.get_player(player_name)
                            if p2 is not None:
                                self._progress_mgr.hide_progress(p2)
                                p2.send_message(f"§c[MXCR] 保存失败: {str(e)}")
                            else:
                                self._progress_mgr.hide_progress_by_name(player_name)
                        except Exception:
                            self._progress_mgr.hide_progress_by_name(player_name)
                        self.logger.error(f"Failed to save structure: {e}")

                # 异步读取所有容器方块的物品数据
                container_blocks = [
                    b for b in blocks if b.nbt_data == "__CONTAINER__"
                ]

                if container_blocks:
                    # 有容器方块，需要异步读取
                    pending_count = [len(container_blocks)]

                    def on_all_containers_done():
                        """all container reads finished; then read entity SAPI data if any"""
                        self._progress_mgr.update_progress(
                            self.server.get_player(player_name) or p,
                            0.55, "§e正在读取实体数据..."
                        )
                        self._do_read_entity_sapi_data(
                            entities, dim_name, min_pos, max_pos,
                            player_name, p, do_save_data
                        )

                    def make_container_callback(block_info: BlockInfo):
                        def callback(slots, error):
                            if error:
                                # 容器读取失败，保留占位符为 None
                                block_info.nbt_data = None
                                self.logger.warning(
                                    f"[MXCR] 容器读取失败 "
                                    f"({block_info.x},{block_info.y},{block_info.z}): {error}"
                                )
                            else:
                                # 将物品列表序列化为 JSON 字符串存入 nbt_data
                                block_info.nbt_data = json.dumps(
                                    slots, ensure_ascii=False
                                ) if slots else None

                            pending_count[0] -= 1
                            if pending_count[0] <= 0:
                                on_all_containers_done()
                        return callback

                    for cb in container_blocks:
                        self._container_mgr.request_read(
                            x=cb.x, y=cb.y, z=cb.z,
                            dim_name=dim_name,
                            callback=make_container_callback(cb)
                        )
                    # 不立即执行 do_save_data，由回调驱动
                else:
                    # 无容器方块，直接读取实体 SAPI 数据
                    self._progress_mgr.update_progress(p, 0.55, "§e正在读取实体数据...")
                    self._do_read_entity_sapi_data(
                        entities, dim_name, min_pos, max_pos,
                        player_name, p, do_save_data
                    )

            except Exception as e:
                try:
                    p = self.server.get_player(player_name)
                    if p is not None:
                        self._progress_mgr.hide_progress(p)
                        p.send_message(f"§c[MXCR] 复制失败: {str(e)}")
                    else:
                        self._progress_mgr.hide_progress_by_name(player_name)
                except Exception:
                    self._progress_mgr.hide_progress_by_name(player_name)
                self.logger.error(f"Failed to copy structure: {e}")

        self.server.scheduler.run_task(self, do_copy, delay=2)

    def _do_read_entity_sapi_data(
        self, entities, dim_name: str, min_pos: tuple, max_pos: tuple,
        player_name: str, progress_player, next_fn
    ):
        """
        辅助方法：异步请求 SAPI 读取选区内实体的扩展数据，完成后调用 next_fn。

        如果没有实体，直接调用 next_fn。

        Args:
            entities:       EntityInfo 列表（会就地填充 sapi_data 字段）
            dim_name:       维度名称
            min_pos:        选区最小坐标
            max_pos:        选区最大坐标
            player_name:    玩家名（用于获取在线玩家对象）
            progress_player:进度条玩家对象（可能已离线，仅用于更新进度）
            next_fn:        实体数据读取完成后调用的无参数函数
        """
        if not entities:
            # 无实体，直接进入下一步
            self._progress_mgr.update_progress(
                self.server.get_player(player_name) or progress_player,
                0.6, "§e正在保存数据..."
            )
            self.server.scheduler.run_task(self, next_fn, delay=2)
            return

        def on_sapi_entities(entities_list, error):
            """实体 SAPI 数据读取回调（通过位置匹配）"""
            if error:
                self.logger.warning(
                    f"[MXCR] 实体 SAPI 数据读取失败: {error}，将仅保存 Endstone 侧数据"
                )
            else:
                # 通过位置匹配将 SAPI 数据填充到对应的 EntityInfo
                # entities_list 是 SAPI 返回的列表，每个元素含 loc_x/y/z
                # entities 是 Endstone 侧扫描到的列表，每个元素含 rel_x/y/z
                # 两者都是相对于 min_pos 的相对坐标（Endstone 侧）或绝对坐标（SAPI 侧）
                if entities_list:
                    for sapi_ent in entities_list:
                        sx = sapi_ent.get("loc_x", 0)
                        sy = sapi_ent.get("loc_y", 0)
                        sz = sapi_ent.get("loc_z", 0)
                        best_ei = None
                        best_dist = 1.0  # 最大匹配距离（格）
                        for ei in entities:
                            # EntityInfo 中存的是相对坐标，加上 min_pos 得绝对坐标
                            ex = ei.rel_x + min_pos[0]
                            ey = ei.rel_y + min_pos[1]
                            ez = ei.rel_z + min_pos[2]
                            dist = ((ex - sx) ** 2 + (ey - sy) ** 2 + (ez - sz) ** 2) ** 0.5
                            if dist < best_dist:
                                best_dist = dist
                                best_ei = ei
                        if best_ei is not None:
                            best_ei.sapi_data = sapi_ent

            # 无论是否成功，都进入下一步
            self._progress_mgr.update_progress(
                self.server.get_player(player_name) or progress_player,
                0.6, "§e正在保存数据..."
            )
            self.server.scheduler.run_task(self, next_fn, delay=2)

        self._entity_mgr.request_read_sapi_data(
            dim_name=dim_name,
            min_pos=min_pos,
            max_pos=max_pos,
            callback=on_sapi_entities,
        )

    def load_structure_for_player(self, player, structure_id: str,
                                   target_pos: tuple,
                                   source_type: str = "player",
                                   source_dir: str = "",
                                   reliable_mode: bool = False,
                                   preloaded_blocks=None,
                                   preloaded_meta=None,
                                   preloaded_entities=None):
        """加载建筑（智能分块版本，与复制流程对称）

        Args:
            preloaded_blocks:   预变换后的方块列表（旋转/翻转后直接传入，跳过数据库读取）
            preloaded_meta:     预变换后的元数据（旋转/翻转后的尺寸信息）
            preloaded_entities: 预变换后的实体列表
        """
        from endstone.boss import BarColor

        player_uuid_str = str(player.unique_id)
        player_name = player.name
        dimension = player.dimension

        # v1.0.0: 粘贴/加载权限检查（own → allow_paste; public → allow_public_load）
        if source_type == "public":
            if not self._check_normal_permission(player, "allow_public_load"):
                player.send_message("§c[MXCR] 你没有加载公开建筑的权限!")
                return
        else:
            if not self._check_normal_permission(player, "allow_paste"):
                player.send_message("§c[MXCR] 你没有粘贴建筑的权限!")
                return

        # 如果有预加载数据（旋转/翻转后），直接使用，否则从数据库读取元数据
        if preloaded_meta is not None:
            metadata = preloaded_meta
        else:
            if source_type == "public":
                metadata = self._db_mgr.get_public_structure_metadata(structure_id)
            elif source_type == "admin" and source_dir:
                metadata = self._db_mgr.get_metadata_from_dir(source_dir, structure_id)
            else:
                metadata = self._db_mgr.get_structure_metadata(
                    player_uuid_str, structure_id, player_name
                )

        if metadata is None:
            player.send_message("§c[MXCR] 建筑不存在!")
            return

        # 计算目标区域范围
        offset_x = target_pos[0] - metadata.min_x
        offset_y = target_pos[1] - metadata.min_y
        offset_z = target_pos[2] - metadata.min_z
        target_max = (
            metadata.max_x + offset_x,
            metadata.max_y + offset_y,
            metadata.max_z + offset_z
        )

        # 计算区块数量，决定走哪条路径
        chunks = self._chunk_loader.get_chunks_in_bounds(target_pos, target_max)
        chunk_count = len(chunks)

        # 先显示进度条（无论哪条路径，立刻给玩家反馈）
        self._progress_mgr.show_progress(
            player, f"§a正在准备加载 ({chunk_count} 个区块)...", 0.05, BarColor.GREEN
        )
        player.send_message("§e[MXCR] §l正在加载建筑,请稍候...")

        if chunk_count > 1:
            # 多区块：使用智能分块加载（逐区块 TP，与复制流程对称）
            # 注意：预加载数据仅支持单区块快速路径；多区块时忽略预加载（旋转建筑通常不会跨多区块）
            self._chunk_loader.load_with_chunk_loading(
                player, structure_id, target_pos, source_type, source_dir, reliable_mode
            )
        else:
            # 单区块：使用原有快速路径，支持预加载数据
            self._do_load(
                player, structure_id, target_pos, source_type, source_dir,
                preloaded_blocks=preloaded_blocks,
                preloaded_meta=preloaded_meta,
                preloaded_entities=preloaded_entities,
            )

    def _do_load(self, player, structure_id: str, target_pos: tuple,
                 source_type: str = "player", source_dir: str = "",
                 preloaded_blocks=None, preloaded_meta=None, preloaded_entities=None):
        """执行加载操作（单区块快速路径，内存优化版）"""
        from endstone.boss import BarColor

        # 保存玩家信息（避免闭包中访问已离线的 player 对象）
        player_name = player.name
        player_uuid_str = str(player.unique_id)
        dim_name = player.dimension.name

        # 更新进度条（已由调用方提前显示）
        if self._progress_mgr.has_progress(player):
            self._progress_mgr.update_progress(player, 0.1, "§a正在加载建筑...")
        else:
            self._progress_mgr.show_progress(player, "§a正在加载建筑...", 0.1, BarColor.GREEN)

        # 延迟2tick后执行，避免阻塞主线程
        def do_load_single():
            p = self.server.get_player(player_name)
            if p is None:
                self._progress_mgr.hide_progress_by_name(player_name)
                self.logger.info(f"Player {player_name} offline, load cancelled")
                return

            try:
                dim = self.server.level.get_dimension(dim_name)
                self._progress_mgr.update_progress(p, 0.2, "§a正在读取数据...")

                # 一次性读取元数据、方块数据和实体数据
                # 如果有预加载数据（旋转/翻转后），直接使用，跳过数据库读取
                if preloaded_blocks is not None and preloaded_meta is not None:
                    metadata = preloaded_meta
                    blocks_to_place = preloaded_blocks
                    entities_to_spawn = preloaded_entities if preloaded_entities is not None else []
                elif source_type == "public":
                    metadata = self._db_mgr.get_public_structure_metadata(structure_id)
                    blocks_to_place = self._db_mgr.get_public_structure_blocks(structure_id)
                    entities_to_spawn = self._db_mgr.get_public_structure_entities(structure_id)
                elif source_type == "admin" and source_dir:
                    metadata = self._db_mgr.get_metadata_from_dir(source_dir, structure_id)
                    blocks_to_place = self._db_mgr.get_blocks_from_dir(source_dir, structure_id)
                    entities_to_spawn = self._db_mgr.get_entities_from_dir(source_dir, structure_id)
                else:
                    metadata = self._db_mgr.get_structure_metadata(
                        player_uuid_str, structure_id, player_name
                    )
                    blocks_to_place = self._db_mgr.get_structure_blocks(
                        player_uuid_str, structure_id, player_name
                    )
                    entities_to_spawn = self._db_mgr.get_structure_entities(
                        player_uuid_str, structure_id, player_name
                    )

                if metadata is None or blocks_to_place is None:
                    self._progress_mgr.hide_progress(p)
                    p.send_message("§c[MXCR] 建筑数据不存在!")
                    return

                self._progress_mgr.update_progress(p, 0.4, "§a正在放置方块...")

                # 计算偏移量
                offset_x = target_pos[0] - metadata.min_x
                offset_y = target_pos[1] - metadata.min_y
                offset_z = target_pos[2] - metadata.min_z
                struct_name = metadata.name

                # 在放置方块前，先扫描目标区域内已存在的实体（用于撤销时恢复）
                target_min_pos = (
                    metadata.min_x + offset_x,
                    metadata.min_y + offset_y,
                    metadata.min_z + offset_z,
                )
                target_max_pos = (
                    metadata.max_x + offset_x,
                    metadata.max_y + offset_y,
                    metadata.max_z + offset_z,
                )
                original_entities = self._entity_mgr.scan_entities_in_selection(
                    dim, target_min_pos, target_max_pos
                )

                # 边放置边记录原方块（避免两个大列表同时占用内存）
                original_blocks = []
                placed_count = 0
                # 收集需要写入容器物品的方块：(new_x, new_y, new_z, slots_json)
                container_writes = []

                for b in blocks_to_place:
                    nx = b.x + offset_x
                    ny = b.y + offset_y
                    nz = b.z + offset_z
                    try:
                        block = dim.get_block_at(nx, ny, nz)
                        # 记录原方块（用于撤销）
                        bd = block.data
                        states = dict(bd.block_states) if bd.block_states else {}
                        original_blocks.append(BlockRecord(
                            x=nx, y=ny, z=nz,
                            block_type=block.type,
                            block_data=0,
                            block_states=states
                        ))
                        # 如果当前位置是容器方块，先替换为屏障（不会掉落物品）再放置目标方块
                        try:
                            if self._container_mgr.is_container_block(block.type):
                                block.set_type("minecraft:barrier", False)
                        except Exception:
                            pass
                        # 放置方块
                        if b.block_states and self.server:
                            try:
                                block_data = self.server.create_block_data(
                                    b.block_type, b.block_states
                                )
                                block.set_data(block_data, False)
                            except Exception:
                                block.set_type(b.block_type, False)
                        else:
                            block.set_type(b.block_type, False)
                        placed_count += 1
                        # 如果该方块有容器物品数据，加入写入队列
                        if b.nbt_data and b.nbt_data != "__CONTAINER__":
                            container_writes.append((nx, ny, nz, b.nbt_data))
                    except Exception:
                        pass

                # 放置完成后立即释放大型列表，让 GC 可以回收
                del blocks_to_place

                # 记录到撤销历史
                if original_blocks:
                    self._undo_mgr.record_action(
                        player_name=player_name,
                        action_type="load",
                        blocks=original_blocks,
                        dimension=dim_name,
                        description=f"加载建筑 {struct_name}",
                        entities=original_entities,
                    )
                    del original_blocks  # 已交给 undo_mgr，可以释放本地引用

                # 延迟隐藏进度条（必须先于 container_writes 分支，因为回调中会引用）
                def hide_bar():
                    try:
                        p3 = self.server.get_player(player_name)
                        if p3 is not None:
                            self._progress_mgr.hide_progress(p3)
                        else:
                            self._progress_mgr.hide_progress_by_name(player_name)
                    except Exception:
                        self._progress_mgr.hide_progress_by_name(player_name)

                # 异步写入容器物品数据
                if container_writes:
                    self._progress_mgr.update_progress(p, 0.9, "§a正在还原容器物品...")
                    p.send_message(f"§e[MXCR] 正在还原 {len(container_writes)} 个容器的物品...")
                    pending_writes = [len(container_writes)]

                    def on_all_writes_done():
                        p_final = self.server.get_player(player_name)
                        if p_final:
                            self._progress_mgr.update_progress(p_final, 0.95, "§a正在生成实体...")
                        # 容器写入完成后，继续恢复实体
                        self._do_spawn_entities(
                            entities_to_spawn, dim, target_pos, metadata,
                            dim_name, player_name, placed_count, hide_bar,
                            with_containers=True
                        )

                    def make_write_callback(wx, wy, wz):
                        def cb(ok, count, error):
                            if not ok:
                                self.logger.warning(
                                    f"[MXCR] 容器写入失败 ({wx},{wy},{wz}): {error}"
                                )
                            pending_writes[0] -= 1
                            if pending_writes[0] <= 0:
                                on_all_writes_done()
                        return cb

                    for (wx, wy, wz, slots_json) in container_writes:
                        try:
                            slots = json.loads(slots_json)
                        except Exception:
                            slots = []
                            pending_writes[0] -= 1
                            if pending_writes[0] <= 0:
                                on_all_writes_done()
                            continue
                        self._container_mgr.request_write(
                            x=wx, y=wy, z=wz,
                            dim_name=dim_name,
                            slots=slots,
                            callback=make_write_callback(wx, wy, wz)
                        )
                else:
                    # 无容器物品，直接进入实体恢复
                    self._progress_mgr.update_progress(p, 0.95, "§a正在生成实体...")
                    self._do_spawn_entities(
                        entities_to_spawn, dim, target_pos, metadata,
                        dim_name, player_name, placed_count, hide_bar,
                        with_containers=False
                    )

            except Exception as e:
                try:
                    p2 = self.server.get_player(player_name)
                    if p2 is not None:
                        self._progress_mgr.hide_progress(p2)
                        p2.send_message(f"§c[MXCR] 加载失败: {str(e)}")
                    else:
                        self._progress_mgr.hide_progress_by_name(player_name)
                except Exception:
                    self._progress_mgr.hide_progress_by_name(player_name)
                self.logger.error(f"Failed to load structure: {e}")

        self.server.scheduler.run_task(self, do_load_single, delay=2)

    def _do_spawn_entities(
        self, entities_to_spawn, dimension, target_pos: tuple, metadata,
        dim_name: str, player_name: str, placed_count: int, hide_bar_fn,
        with_containers: bool
    ):
        """
        辅助方法：在目标位置逐个生成并恢复实体。

        完成后发送加载完成消息并隐藏进度条。

        Args:
            entities_to_spawn:  EntityInfo 列表（可为 None 或空列表）
            dimension:          Endstone Dimension 对象
            target_pos:         粘贴目标最小坐标
            metadata:           建筑元数据
            dim_name:           维度名称
            player_name:        玩家名
            placed_count:       已放置方块数（用于完成消息）
            hide_bar_fn:        隐藏进度条的回调函数
            with_containers:    是否包含容器物品（用于完成消息）
        """
        # 计算目标区域的实际最小坐标（用于 EntityManager.spawn_and_restore_entity）
        offset_x = target_pos[0] - metadata.min_x
        offset_y = target_pos[1] - metadata.min_y
        offset_z = target_pos[2] - metadata.min_z
        actual_min_pos = (
            metadata.min_x + offset_x,
            metadata.min_y + offset_y,
            metadata.min_z + offset_z,
        )

        def finish_load(entity_count: int):
            """all entities spawned, send final message"""
            p_final = self.server.get_player(player_name)
            if p_final:
                self._progress_mgr.update_progress(p_final, 1.0, "§a加载完成!")
                if with_containers:
                    p_final.send_message("§a[MXCR] §l建筑已加载（包含容器物品）!")
                else:
                    p_final.send_message("§a[MXCR] §l建筑已加载!")
                p_final.send_message(f"§f共放置了 §e{placed_count} §f个方块")
                if entity_count > 0:
                    p_final.send_message(f"§f共生成了 §e{entity_count} §f个实体")
            self.server.scheduler.run_task(self, hide_bar_fn, delay=30)

        if not entities_to_spawn:
            finish_load(0)
            return

        # 逐个生成实体，全部完成后调用 finish_load
        total = len(entities_to_spawn)
        pending = [total]
        spawned_count = [0]

        def make_entity_done_cb():
            def on_done(ok, error):
                if ok:
                    spawned_count[0] += 1
                elif error:
                    self.logger.warning(f"[MXCR] 实体生成失败: {error}")
                pending[0] -= 1
                if pending[0] <= 0:
                    finish_load(spawned_count[0])
            return on_done

        for entity_info in entities_to_spawn:
            self._entity_mgr.spawn_and_restore_entity(
                dimension=dimension,
                entity_info=entity_info,
                target_min_pos=actual_min_pos,
                dim_name=dim_name,
                on_done=make_entity_done_cb(),
            )

    def delete_structure_for_player(self, player, structure_id: str):
        """删除建筑"""
        try:
            # v1.0.0: 删除自己建筑的权限检查
            if not self._check_normal_permission(player, "allow_delete_own"):
                player.send_message("§c[MXCR] 你没有删除建筑的权限!")
                return

            player_uuid_str = str(player.unique_id)
            player_name = player.name
            metadata = self._db_mgr.get_structure_metadata(
                player_uuid_str, structure_id, player_name
            )
            if metadata is None:
                player.send_message("§c[MXCR] 建筑不存在!")
                return

            self._db_mgr.delete_structure(player_uuid_str, structure_id, player_name)
            player.send_message(f"§a[MXCR] 建筑 §e{metadata.name} §a已删除!")

        except Exception as e:
            player.send_message(f"§c[MXCR] 删除失败: {str(e)}")
            self.logger.error(f"Failed to delete structure: {e}")

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

    # ========== 撤销/重做操作 ==========

    def undo_operation(self, player, reliable_mode: bool = False):
        """撤销上一步操作（使用分块加载）"""
        player_name = player.name

        # v1.0.0: 权限校验放在方法内部，命令与表单两条路径统一拦截
        if not self._check_normal_permission(player, "allow_undo_redo"):
            player.send_message("§c[MXCR] 你没有撤销权限!")
            return

        if not self._undo_mgr.can_undo(player_name):
            player.send_message("§e[MXCR] 没有可撤销的操作")
            return

        action = self._undo_mgr.pop_undo_action(player_name)
        if action is None:
            player.send_message("§e[MXCR] 没有可撤销的操作")
            return

        try:
            player.send_message(f"§e[MXCR] 正在撤销操作: {action.description}...")

            from endstone.boss import BarColor as _BarColor
            self._progress_mgr.show_progress(
                player, f"§e正在撤销: {action.description}...",
                0.0, _BarColor.YELLOW
            )

            # 先异步读取当前容器物品数据，再推入重做栈并执行恢复
            self._snapshot_and_restore(
                player=player,
                action_to_restore=action,
                push_to="redo",
                description=f"撤销: {action.description}",
                bar_color=_BarColor.YELLOW,
                reliable_mode=reliable_mode,
            )

        except Exception as e:
            # v1.0.0: 撤销启动失败时把 action 放回栈，避免记录丢失
            try:
                self._undo_mgr.push_undo_action(player_name, action)
            except Exception:
                pass
            player.send_message(f"§c[MXCR] 撤销失败: {str(e)}")
            self.logger.error(f"Undo operation failed: {e}")

    def redo_operation(self, player, reliable_mode: bool = False):
        """重做撤销的操作（使用分块加载）"""
        player_name = player.name

        # v1.0.0: 权限校验放在方法内部，命令与表单两条路径统一拦截
        if not self._check_normal_permission(player, "allow_undo_redo"):
            player.send_message("§c[MXCR] 你没有重做权限!")
            return

        if not self._undo_mgr.can_redo(player_name):
            player.send_message("§e[MXCR] 没有可重做的操作")
            return

        action = self._undo_mgr.pop_redo_action(player_name)
        if action is None:
            player.send_message("§e[MXCR] 没有可重做的操作")
            return

        try:
            player.send_message(f"§e[MXCR] 正在重做操作: {action.description}...")

            from endstone.boss import BarColor as _BarColor
            self._progress_mgr.show_progress(
                player, f"§a正在重做: {action.description}...",
                0.0, _BarColor.GREEN
            )

            # 先异步读取当前容器物品数据，再推入撤销栈并执行恢复
            self._snapshot_and_restore(
                player=player,
                action_to_restore=action,
                push_to="undo",
                description=f"重做: {action.description}",
                bar_color=_BarColor.GREEN,
                reliable_mode=reliable_mode,
            )

        except Exception as e:
            # v1.0.0: 重做启动失败时把 action 放回栈，避免记录丢失
            try:
                self._undo_mgr.push_redo_action(player_name, action)
            except Exception:
                pass
            player.send_message(f"§c[MXCR] 重做失败: {str(e)}")
            self.logger.error(f"Redo operation failed: {e}")

    def _snapshot_and_restore(
        self, player, action_to_restore, push_to: str,
        description: str, bar_color, reliable_mode: bool
    ):
        """
        通用撤销/重做核心流程：
        1. 扫描当前世界中需要快照的方块（方块类型 + 容器物品）
        2. 扫描当前区域内的实体（Endstone 侧基础属性）
        3. 异步读取容器物品数据
        4. 异步读取实体 SAPI 扩展数据（颜色/装备/药水/属性）
        5. 全部读取完毕后，将快照推入对应栈，再执行方块恢复

        Args:
            player: 玩家对象
            action_to_restore: 要恢复的 UndoAction
            push_to: "redo" 或 "undo"，决定快照推入哪个栈
            description: 操作描述（用于 ChunkLoader 进度条）
            bar_color: 进度条颜色
            reliable_mode: 是否使用传统可靠模式
        """
        player_name = player.name
        dimension = player.dimension
        dim_name = action_to_restore.dimension

        # ---- 第一步：扫描当前方块状态（v1.0.0: 按区块分批读取 + 磁盘 writer 流式写入）----
        snapshot_writer = self._undo_mgr.open_action_writer(
            player_name, action_to_restore.action_type, dim_name,
            action_to_restore.description
        )
        # 需要异步读取容器物品的快照记录（数量少，暂留内存，读完后再写入 writer）
        container_snapshot_records = []

        try:
            for chunk_key in action_to_restore.get_chunk_list():
                chunk_records = action_to_restore.get_chunk_blocks(chunk_key[0], chunk_key[1])
                for block_record in chunk_records:
                    try:
                        block = dimension.get_block_at(
                            block_record.x, block_record.y, block_record.z
                        )
                        bd = block.data
                        states = dict(bd.block_states) if bd.block_states else {}
                        cur_type = str(block.type)
                        rec = BlockRecord(
                            x=block_record.x,
                            y=block_record.y,
                            z=block_record.z,
                            block_type=cur_type,
                            block_data=0,
                            block_states=states,
                            nbt_data=None,
                        )
                        # 如果当前方块是容器，标记需要读取物品（暂留内存）
                        if self._container_mgr.is_container_block(cur_type):
                            container_snapshot_records.append(rec)
                        else:
                            snapshot_writer.add_block(rec)
                    except Exception:
                        pass
                chunk_records.clear()
        except Exception as e:
            self.logger.warning(f"[MXCR] 快照扫描异常: {e}")

        # ---- 第一步①：扫描当前区域内的实体（快照，Endstone 侧基础属性）----
        snapshot_entities = []
        snap_min_pos, snap_max_pos = action_to_restore.get_bounds()
        if action_to_restore.block_count > 0:
            try:
                snapshot_entities = self._entity_mgr.scan_entities_in_selection(
                    dimension, snap_min_pos, snap_max_pos
                )
            except Exception as e:
                self.logger.warning(f"[MXCR] 实体快照扫描失败: {e}")

        def _push_snapshot_and_restore():
            """快照容器物品和实体 SAPI 数据读取完毕后，推入栈并执行恢复"""
            # 把读完物品的容器快照补写入 writer，然后提交生成快照 action
            snapshot_action = None
            try:
                for rec in container_snapshot_records:
                    snapshot_writer.add_block(rec)
                container_snapshot_records.clear()
                snapshot_writer.set_entities(snapshot_entities)
                snapshot_action = snapshot_writer.commit_detached()
            except Exception as e:
                self.logger.warning(f"[MXCR] 快照提交失败: {e}")

            if snapshot_action is not None:
                if push_to == "redo":
                    self._undo_mgr.push_redo_action(snapshot_action)
                else:
                    self._undo_mgr.push_undo_action(snapshot_action)

            # 先删除当前区域内的实体（即快照时扫描到的实体）
            # snapshot_entities 中的坐标是相对于 snap_min_pos 的偏移，需加上 snap_min_pos 得到绝对坐标
            try:
                dim_obj = self.server.level.get_dimension(dim_name)
                for ent_info in snapshot_entities:
                    try:
                        abs_x = ent_info.rel_x + snap_min_pos[0]
                        abs_y = ent_info.rel_y + snap_min_pos[1]
                        abs_z = ent_info.rel_z + snap_min_pos[2]
                        for actor in dim_obj.actors:
                            try:
                                if (abs(actor.location.x - abs_x) < 0.5 and
                                        abs(actor.location.y - abs_y) < 0.5 and
                                        abs(actor.location.z - abs_z) < 0.5 and
                                        actor.type == ent_info.type_id):
                                    actor.remove()
                                    break
                            except Exception:
                                pass
                    except Exception:
                        pass
            except Exception:
                pass

            # 执行方块恢复（v1.0.0: 传 undo_action 流式读取，不再全量载入内存）
            self._chunk_loader.restore_blocks_with_chunk_loading(
                player, None, dim_name, description, reliable_mode,
                entities_to_restore=action_to_restore.entities,
                undo_action=action_to_restore,
            )

        # ---- 第三步：异步读取容器物品 + 实体 SAPI 数据，全部完成后执行恢复 ----
        # 使用统一的 pending 计数器跟踪所有异步任务
        has_containers = bool(container_snapshot_records)
        has_entities = bool(snapshot_entities)

        # 如果无容器且无实体，直接执行
        if not has_containers and not has_entities:
            _push_snapshot_and_restore()
            return

        # 异步任务数：容器读取 + 实体 SAPI 读取（各算 1 个大任务）
        async_tasks_remaining = [0]
        if has_containers:
            async_tasks_remaining[0] += 1
        if has_entities:
            async_tasks_remaining[0] += 1

        def _on_async_task_done():
            """每个异步大任务完成后调用，全部完成后执行恢复"""
            async_tasks_remaining[0] -= 1
            if async_tasks_remaining[0] <= 0:
                _push_snapshot_and_restore()

        # ---- 异步任务 A：读取容器物品（v1.0.0: 分批节流）----
        if has_containers:
            records_list = list(container_snapshot_records)
            read_batch_size = 16

            def read_snap_batch(start_idx):
                if start_idx >= len(records_list):
                    _on_async_task_done()
                    return
                batch = records_list[start_idx:start_idx + read_batch_size]
                batch_pending = [len(batch)]

                def make_snapshot_cb(rec):
                    def cb(slots, error):
                        if not error and slots:
                            rec.nbt_data = json.dumps(slots, ensure_ascii=False)
                        batch_pending[0] -= 1
                        if batch_pending[0] <= 0:
                            self.server.scheduler.run_task(
                                self,
                                lambda: read_snap_batch(start_idx + read_batch_size),
                                delay=1
                            )
                    return cb

                for rec in batch:
                    self._container_mgr.request_read(
                        x=rec.x, y=rec.y, z=rec.z,
                        dim_name=dim_name,
                        callback=make_snapshot_cb(rec),
                    )

            read_snap_batch(0)

        # ---- 异步任务 B：读取实体 SAPI 扩展数据（颜色/装备/药水/属性）----
        if has_entities:
            def on_sapi_entities(entities_list, error):
                """实体 SAPI 数据读取回调（通过位置匹配）"""
                if error:
                    self.logger.warning(
                        f"[MXCR] 撤销/重做实体 SAPI 数据读取失败: {error}，将仅保存 Endstone 侧数据"
                    )
                elif entities_list:
                    # 通过位置匹配将 SAPI 数据填充到对应的 EntityInfo
                    for sapi_ent in entities_list:
                        sx = sapi_ent.get("loc_x", 0)
                        sy = sapi_ent.get("loc_y", 0)
                        sz = sapi_ent.get("loc_z", 0)
                        best_ei = None
                        best_dist = 1.0  # 最大匹配距离（格）
                        for ei in snapshot_entities:
                            # EntityInfo 中存的是相对坐标，加上 snap_min_pos 得绝对坐标
                            ex = ei.rel_x + snap_min_pos[0]
                            ey = ei.rel_y + snap_min_pos[1]
                            ez = ei.rel_z + snap_min_pos[2]
                            dist = ((ex - sx) ** 2 + (ey - sy) ** 2 + (ez - sz) ** 2) ** 0.5
                            if dist < best_dist:
                                best_dist = dist
                                best_ei = ei
                        if best_ei is not None:
                            best_ei.sapi_data = sapi_ent

                _on_async_task_done()

            self._entity_mgr.request_read_sapi_data(
                dim_name=dim_name,
                min_pos=snap_min_pos,
                max_pos=snap_max_pos,
                callback=on_sapi_entities,
            )

    # ================================================================
    #  事件处理: 玩家退出（合并所有清理逻辑）
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

    # ================================================================
    #  容器管理器定时任务
    # ================================================================

    def _container_tick(self):
        """每 tick 调用，用于超时清理挂起的容器请求和实体请求"""
        try:
            self._container_mgr.tick()
        except Exception:
            pass
        try:
            self._entity_mgr.tick()
        except Exception:
            pass

    # ================================================================
    #  事件处理: ScriptMessageEvent（来自 SAPI 的回传）
    # ================================================================

    @event_handler
    def on_script_message(self, event: ScriptMessageEvent):
        """处理来自 SAPI Behavior Pack 的 scriptevent 回传"""
        try:
            # 先尝试实体管理器处理（返回 True 表示已处理）
            handled = self._entity_mgr.on_script_message(event.message_id, event.message)
            if not handled:
                # 实体管理器不认识此消息，转由到容器管理器
                self._container_mgr.on_script_message(event.message_id, event.message)
        except Exception as e:
            self.logger.warning(f"[MXCR] ScriptMessageEvent 处理异常: {e}")

    def _check_bridge_connection(self, player):
        """
        检测 SAPI Bridge Behavior Pack 是否已连接。
        通过发送一个 ping scriptevent，等待 SAPI 回传 pong 来确认连接状态。
        """
        player_name = player.name

        player.send_message("\u00a7e[MXCR Bridge] \u6b63\u5728\u68c0\u6d4b SAPI \u8fde\u63a5\u72b6\u6001...")

        def on_pong(slots, error):
            p = self.server.get_player(player_name)
            if p is None:
                return
            if error and "\u8bf7\u6c42\u8d85\u65f6" in error:
                p.send_message("\u00a7c[MXCR Bridge] \u672a\u68c0\u6d4b\u5230 SAPI Bridge Behavior Pack")
                p.send_message("\u00a7c\u8bf7\u786e\u8ba4\uff1a")
                p.send_message("\u00a7f  1. Behavior Pack \u5df2\u5b89\u88c5\u5e76\u5e94\u7528\u5230\u5f53\u524d\u4e16\u754c")
                p.send_message("\u00a7f  2. \u4e16\u754c\u5df2\u5f00\u542f Beta API \u5b9e\u9a8c\u6027\u529f\u80fd")
                p.send_message("\u00a7f  3. @minecraft/server \u7248\u672c\u4e3a 2.5.0")
                p.send_message("\u00a7f  4. \u670d\u52a1\u5668\u542f\u52a8\u65f6\u804a\u5929\u680f\u5e94\u663e\u793a [MXCR Bridge] \u5df2\u52a0\u8f7d")
            elif error:
                p.send_message(f"\u00a7c[MXCR Bridge] \u8fde\u63a5\u5f02\u5e38: {error}")
            else:
                p.send_message("\u00a7a[MXCR Bridge] \u2714 SAPI Bridge \u8fde\u63a5\u6b63\u5e38!")
                p.send_message("\u00a7f  \u5bb9\u5668\u7269\u54c1\u4fdd\u5b58/\u8fd8\u539f\u529f\u80fd\u5df2\u5c31\u7eea")

        # v1.0.0: 使用 ContainerManager 公共 request_ping 接口（不再直接操作私有成员）
        self._container_mgr.request_ping(on_pong, timeout_ticks=60)

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
