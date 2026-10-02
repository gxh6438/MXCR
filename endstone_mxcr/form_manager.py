"""
表单管理模块 - 所有 UI 表单
使用 Minecraft 颜色代码 § 美化文字

确认的 API:
- from endstone.form import ActionForm, ModalForm, MessageForm, TextInput, Toggle, Dropdown, Label
- ActionForm.on_submit: Callable[[Player, int], None]
- ActionForm.add_button(text, icon="", on_click: Callable[[Player], None])
- ModalForm.on_submit: Callable[[Player, str], None]  (str 是 JSON)
- ModalForm.on_close: Callable[[Player], None]
- MessageForm.on_submit: Callable[[Player, int], None]
- TextInput(label, placeholder="", default_value="")
- Toggle(label, default_value=False)
- Dropdown(label, options=None, default_index=None)
- Label(text)
"""
import json
import time

from endstone.form import (
    ActionForm, ModalForm, MessageForm,
    TextInput, Toggle, Dropdown, Label
)

from . import load_forms


# ========== 颜色代码常量 ==========
C_GOLD = "§6"
C_GREEN = "§a"
C_AQUA = "§b"
C_RED = "§c"
C_LIGHT_PURPLE = "§d"
C_YELLOW = "§e"
C_WHITE = "§f"
C_BLUE = "§9"
C_DARK_GREEN = "§2"
C_DARK_AQUA = "§3"
C_DARK_RED = "§4"
C_BOLD = "§l"
C_RESET = "§r"


def _b(text: str, color: str = "") -> str:
    """加粗文字"""
    return f"{color}{C_BOLD}{text}{C_RESET}"


class FormManager:
    """表单管理器"""

    def __init__(self, plugin):
        """
        Args:
            plugin: MXCRPlugin 实例
        """
        self.plugin = plugin

    # ================================================================
    #  主菜单
    # ================================================================
    def _show_mode_select_form(self, player, title: str, callback):
        """显示区块模式选择表单（快速实验 / 传统可靠）"""
        from endstone.form import MessageForm
        form = MessageForm(
            title=_b(title, C_GOLD),
            content=(
                f"{C_YELLOW}请选择区块处理模式:\n\n"
                f"{_b('快速实验', C_GREEN)}: 已加载区块直接处理，未加载区块逐个 TP（默认）\n"
                f"{_b('传统可靠', C_RED)}: 全部区块逐个 TP，防止漏区块，速度较慢"
            ),
            button1=_b("快速实验", C_GREEN),
            button2=_b("传统可靠", C_RED),
            on_submit=lambda p, idx: callback(p, idx == 1)
        )
        player.send_form(form)

    def show_main_menu(self, player):
        """显示主菜单"""
        p_name = player.name
        is_op = player.is_op
        perm_mgr = self.plugin.perm_manager
        cfg = self.plugin.config_manager
        is_admin = perm_mgr.is_game_admin(p_name, is_op)
        is_authorized = perm_mgr.is_authorized(p_name, is_op)
        # v1.0.0: 统一使用插件的权限检查辅助方法（消除此处重复的 UUID 判断逻辑），
        # 并按权限键决定按钮可见性，与后端实际执行的权限校验保持一致
        has_copy = self.plugin._check_normal_permission(player, "allow_copy")
        has_foot_block = self.plugin._check_normal_permission(player, "allow_foot_block")
        has_undo_redo = self.plugin._check_normal_permission(player, "allow_undo_redo")
        has_public_view = self.plugin._check_normal_permission(player, "allow_public_view")
        has_refresh = self.plugin._check_normal_permission(player, "allow_refresh_chunks")

        # 检测玩家是否处于预览模式
        is_in_preview = p_name in self.plugin.preview_manager.active_previews

        form = ActionForm(
            title=_b("MXCR 建筑管理系统", C_GOLD),
            content=(
                f"{C_AQUA}欢迎使用 {_b('MXCR', C_GOLD)} 建筑复制粘贴插件!\n"
                f"{C_YELLOW}玩家: {_b(p_name, C_GREEN)}\n"
                f"{C_YELLOW}权限: {_b('管理员' if is_admin else ('已授权' if is_authorized else ('仅复制' if has_copy else '无权限')), C_LIGHT_PURPLE)}"
                + (f"\n{C_LIGHT_PURPLE}状态: {_b('预览中', C_YELLOW)}" if is_in_preview else "")
            )
        )

        # 如果玩家处于预览模式，在第一位插入“预览面板”按钮
        if is_in_preview:
            form.add_button(
                _b("预览面板", C_LIGHT_PURPLE),
                icon="textures/ui/icon_best3",
                on_click=lambda p: p.perform_command("mxcr preview")
            )

        # 所有有权限的玩家都能看到的按钮
        if has_copy or is_authorized:
            form.add_button(
                _b("开始选区", C_GREEN),
                icon="textures/ui/icon_recipe_item",
                on_click=lambda p: self._on_start_selection(p)
            )
            form.add_button(
                _b("保存建筑", C_GOLD),
                icon="textures/ui/backup_replace",
                on_click=lambda p: self._on_save_structure(p)
            )
            form.add_button(
                _b("我的建筑", C_AQUA),
                icon="textures/ui/icon_book_writable",
                on_click=lambda p: self.show_my_structures(p)
            )
            # v1.0.0: 撤销/恢复按钮按 allow_undo_redo 权限显示
            if has_undo_redo:
                form.add_button(
                    _b("撤销操作", C_YELLOW),
                    icon="textures/ui/hammer_l",
                    on_click=lambda p: self._show_mode_select_form(
                        p, "撤销操作",
                        lambda _p, reliable: self.plugin.undo_operation(_p, reliable_mode=reliable)
                    )
                )
                form.add_button(
                    _b("恢复操作", C_GREEN),
                    icon="textures/ui/hammer_r",
                    on_click=lambda p: self._show_mode_select_form(
                        p, "恢复操作",
                        lambda _p, reliable: self.plugin.redo_operation(_p, reliable_mode=reliable)
                    )
                )
            if has_foot_block:
                form.add_button(
                    _b("脚下放置方块", C_LIGHT_PURPLE),
                    icon="textures/ui/world_glyph_desaturated",
                    on_click=lambda p: self._on_place_foot_block(p)
                )
            form.add_button(
                _b("停止选区", C_RED),
                icon="textures/ui/cancel",
                on_click=lambda p: self.plugin.stop_selection_for_player(p)
            )

        # v1.0.0: 公开建筑/刷新区块按钮按对应权限键显示（授权玩家始终可见）
        if has_public_view:
            form.add_button(
                _b("公开建筑", C_YELLOW),
                icon="textures/ui/world_glyph_color",
                on_click=lambda p: self.show_public_structures(p)
            )
        if has_refresh:
            form.add_button(
                _b("刷新区块", C_LIGHT_PURPLE),
                icon="textures/ui/refresh_light",
                on_click=lambda p: self._on_refresh_chunks(p)
            )

        if is_admin:
            form.add_button(
                _b("管理员面板", C_RED),
                icon="textures/ui/op",
                on_click=lambda p: self.show_admin_panel(p)
            )

        if is_op:
            form.add_button(
                _b("插件设置", C_WHITE),
                icon="textures/ui/gear",
                on_click=lambda p: self.show_config_form(p)
            )
            form.add_button(
                _b("权限管理", C_BLUE),
                icon="textures/ui/permissions_op_crown",
                on_click=lambda p: self.show_permission_form(p)
            )
            # 普通玩家权限管理 (仅在启用时显示)
            if cfg.normal_player_permissions_enabled:
                form.add_button(
                    _b("权限管理(普通玩家)", C_LIGHT_PURPLE),
                    icon="textures/ui/permissions_member_star",
                    on_click=lambda p: self.show_normal_permission_menu(p)
                )

        player.send_form(form)

    # ================================================================
    #  选区相关
    # ================================================================
    def _on_start_selection(self, player):
        """开始选区"""
        self.plugin.start_selection_for_player(player)

    def _on_save_structure(self, player):
        """保存建筑按钮点击"""
        sel_mgr = self.plugin.selection_manager
        selection = sel_mgr.get_selection(player.name)

        if not selection or not selection.is_complete():
            player.send_message(f"{C_RED}请先完成选区!使用 '开始选区' 按钮或 /mxcr 命令选择区域")
            return

        # 显示保存表单
        self.show_selection_confirm(player)

    def _on_place_foot_block(self, player):
        """在玩家脚下放置方块"""
        try:
            # v1.0.0: 统一走插件的权限检查辅助方法
            if not self.plugin._check_normal_permission(player, "allow_foot_block"):
                player.send_message(f"{C_RED}[权限不足] 您没有脚下方块放置权限")
                return
            import math
            block_id = self.plugin.config_manager.foot_block_id
            loc = player.location
            # 脚下位置 = 玩家Y坐标减1
            # 使用 math.floor 而非 int，避免负坐标截断偏差（如 int(-0.3)=0 但 floor(-0.3)=-1）
            foot_x = math.floor(loc.x)
            foot_y = math.floor(loc.y) - 1
            foot_z = math.floor(loc.z)
            dimension = player.dimension
            block = dimension.get_block_at(foot_x, foot_y, foot_z)
            block.set_type(block_id)  # 使用set_type()方法而不是直接赋值
            player.send_message(
                f"{C_GREEN}[MXCR] 已在脚下 ({foot_x}, {foot_y}, {foot_z}) 放置 {C_YELLOW}{block_id}"
            )
        except Exception as e:
            player.send_message(f"{C_RED}[MXCR] 放置失败: {str(e)}")

    def show_selection_confirm(self, player):
        """显示选区确认表单 (选区完成后)"""
        sel_mgr = self.plugin.selection_manager
        selection = sel_mgr.get_selection(player.name)

        if not selection.is_complete():
            player.send_message(f"{C_RED}选区未完成!")
            return

        min_pos, max_pos = selection.get_bounds()
        size = selection.get_size()
        volume = selection.get_volume()

        cfg = self.plugin.config_manager

        # v1.0.0: 统一走插件的权限检查辅助方法判断搬迁模式权限
        has_move_mode = self.plugin._check_normal_permission(player, "allow_move_mode")

        controls = [
            Label(
                f"{C_AQUA}选区信息:\n"
                f"{C_YELLOW}起点: {C_WHITE}({min_pos[0]}, {min_pos[1]}, {min_pos[2]})\n"
                f"{C_YELLOW}终点: {C_WHITE}({max_pos[0]}, {max_pos[1]}, {max_pos[2]})\n"
                f"{C_YELLOW}尺寸: {C_WHITE}{size[0]} x {size[1]} x {size[2]}\n"
                f"{C_YELLOW}体积: {C_WHITE}{volume} 方块"
            ),
            TextInput(
                f"{_b('建筑名称', C_GREEN)}",
                placeholder="请输入建筑名称",
                default_value=""
            ),
            TextInput(
                f"{_b('排除方块', C_YELLOW)} (用逗号分隔)",
                placeholder="例: minecraft:dirt,minecraft:stone",
                default_value=""
            ),
            Toggle(
                f"{_b('保留空气方块', C_AQUA)}",
                default_value=cfg.preserve_air
            ),
            Toggle(
                f"{_b('保留水', C_BLUE)}",
                default_value=True
            ),
            Toggle(
                f"{_b('保留岩浆', C_RED)}",
                default_value=True
            ),
        ]

        if has_move_mode:
            controls.append(Toggle(
                f"{_b('搬迁模式', C_RED)} (保存后删除原方块)",
                default_value=False
            ))

        controls.append(Toggle(
            f"{_b('传统可靠模式', C_RED)} {C_WHITE}(全部区块逐个 TP，防漏区块，速度较慢)",
            default_value=False
        ))

        form = ModalForm(
            title=_b("保存建筑", C_GOLD),
            controls=controls,
            on_submit=lambda p, data, _hm=has_move_mode: self._on_save_submit(p, data, _hm),
            on_close=lambda p: p.send_message(f"{C_YELLOW}已取消保存")
        )

        player.send_form(form)

    def _on_save_submit(self, player, data_str: str, has_move_mode: bool = True):
        """保存建筑提交回调"""
        try:
            data = json.loads(data_str)
            # Label 在 JSON 数据中会产生一个 null 値,所以:
            # data[0] = null (Label 产生的空値)
            # data[1] = 建筑名称 (TextInput)
            # data[2] = 排除方块 (TextInput)
            # data[3] = 保留空气 (Toggle)
            # data[4] = 保留水 (Toggle)
            # data[5] = 保留岩浆 (Toggle)
            # 有搬迁模式权限时:
            #   data[6] = 搬迁模式 (Toggle)
            #   data[7] = 传统可靠模式 (Toggle)
            # 无搬迁模式权限时:
            #   data[6] = 传统可靠模式 (Toggle)

            if isinstance(data, list) and len(data) >= 6:
                name = str(data[1]).strip() if data[1] else ""
                excluded_blocks_str = str(data[2]).strip() if data[2] else ""
                preserve_air = bool(data[3])
                preserve_water = bool(data[4])
                preserve_lava = bool(data[5])
                if has_move_mode:
                    relocate = bool(data[6]) if len(data) > 6 and data[6] is not None else False
                    reliable_mode = bool(data[7]) if len(data) > 7 and data[7] is not None else False
                else:
                    relocate = False  # 无权限，强制关闭搬迁模式
                    reliable_mode = bool(data[6]) if len(data) > 6 and data[6] is not None else False
            else:
                player.send_message(f"{C_RED}表单数据格式错误!")
                return

            if not name:
                player.send_message(f"{C_RED}建筑名称不能为空!")
                return

            # 解析排除方块列表
            excluded_blocks = []
            if excluded_blocks_str:
                excluded_blocks = [b.strip() for b in excluded_blocks_str.split(",") if b.strip()]

            self.plugin.save_structure_for_player(
                player, name, preserve_air, relocate, excluded_blocks, preserve_water, preserve_lava,
                reliable_mode=reliable_mode
            )

        except Exception as e:
            player.send_message(f"{C_RED}保存失败: {str(e)}")

    # ================================================================
    #  我的建筑列表
    # ================================================================
    def show_my_structures(self, player):
        """显示我的建筑列表"""
        p_uuid = str(player.unique_id)
        p_name = player.name

        structures = self.plugin.db_manager.get_structures(p_uuid, p_name)

        if not structures:
            form = MessageForm(
                title=_b("我的建筑", C_AQUA),
                content=f"{C_YELLOW}你还没有保存任何建筑\n\n{C_WHITE}使用选区工具保存你的第一个建筑吧!",
                button1=_b("返回主菜单", C_GREEN),
                button2=_b("关闭", C_WHITE),
                on_submit=lambda p, idx: self.show_main_menu(p) if idx == 0 else None
            )
            player.send_form(form)
            return

        form = ActionForm(
            title=_b("我的建筑", C_AQUA),
            content=f"{C_YELLOW}共 {_b(str(len(structures)), C_GREEN)} 个建筑\n{C_WHITE}点击查看详情"
        )

        for s in structures:
            size = s.get_size()
            ts = time.strftime("%m/%d %H:%M", time.localtime(s.timestamp))
            btn_text = (
                f"{_b(s.name, C_GREEN)}\n"
                f"{C_WHITE}{size[0]}x{size[1]}x{size[2]} | {ts}"
            )
            sid = s.structure_id
            form.add_button(
                btn_text,
                icon="textures/blocks/structure_block",
                on_click=lambda p, _sid=sid: self.show_structure_detail(p, _sid)
            )

        form.add_button(
            _b("返回主菜单", C_YELLOW),
            icon="textures/ui/arrow_left",
            on_click=lambda p: self.show_main_menu(p)
        )

        player.send_form(form)

    def show_structure_detail(self, player, structure_id: str):
        """显示建筑详情"""
        p_uuid = str(player.unique_id)
        p_name = player.name
        meta = self.plugin.db_manager.get_structure_metadata(p_uuid, structure_id, p_name)

        if meta is None:
            player.send_message(f"{C_RED}建筑不存在!")
            return

        size = meta.get_size()
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(meta.timestamp))
        is_authorized = self.plugin.perm_manager.is_authorized(p_name, player.is_op)

        form = ActionForm(
            title=_b(meta.name, C_GOLD),
            content=(
                f"{C_AQUA}建筑ID: {C_WHITE}{meta.structure_id}\n"
                f"{C_AQUA}尺寸: {C_WHITE}{size[0]} x {size[1]} x {size[2]}\n"
                f"{C_AQUA}方块数: {C_WHITE}{meta.block_count}\n"
                f"{C_AQUA}坐标: {C_WHITE}({meta.min_x},{meta.min_y},{meta.min_z}) ~ ({meta.max_x},{meta.max_y},{meta.max_z})\n"
                f"{C_AQUA}维度: {C_WHITE}{meta.dimension}\n"
                f"{C_AQUA}保存时间: {C_WHITE}{ts}"
            )
        )

        if is_authorized:
            sid = structure_id
            # 原有按鈕：加载到原坐标
            form.add_button(
                _b("加载到原坐标", C_GREEN),
                icon="textures/ui/icon_recipe_nature",
                on_click=lambda p, _sid=sid, _m=meta: self._show_mode_select_form(
                    p, f"加载到原坐标: {_m.name}",
                    lambda _p, reliable: self.plugin.load_structure_for_player(
                        _p, _sid, (_m.min_x, _m.min_y, _m.min_z),
                        reliable_mode=reliable
                    )
                )
            )
            # 原有按钮：加载建筑（当前位置）
            form.add_button(
                _b("加载建筑 (当前位置)", C_BLUE),
                icon="textures/ui/icon_recipe_item",
                on_click=lambda p, _sid=sid: self._on_load_current_position(p, _sid)
            )
            # 原有按钮：加载到指定坐标
            form.add_button(
                _b("加载到指定坐标", C_AQUA),
                icon="textures/ui/icon_setting",
                on_click=lambda p, _sid=sid, _m=meta: self.show_load_position_form(p, _sid, _m)
            )
            # 新增按钮：加载建筑（预览模式）
            form.add_button(
                _b("加载建筑 (预览模式)", C_LIGHT_PURPLE),
                icon="textures/ui/magnifyingGlass",
                on_click=lambda p, _sid=sid, _m=meta: self.show_load_config_form(p, _sid, _m)
            )
            form.add_button(
                _b("公开此建筑", C_YELLOW),
                icon="textures/ui/world_glyph_color",
                on_click=lambda p, _sid=sid: self._on_publish(p, _sid)
            )

        sid2 = structure_id
        form.add_button(
            _b("删除建筑", C_RED),
            icon="textures/ui/trash",
            on_click=lambda p, _sid=sid2: self._confirm_delete(p, _sid)
        )
        form.add_button(
            _b("返回列表", C_YELLOW),
            icon="textures/ui/arrow_left",
            on_click=lambda p: self.show_my_structures(p)
        )

        player.send_form(form)

    def _on_load_original(self, player, structure_id: str, meta):
        """加载到原坐标（先弹出模式选择）"""
        self._show_mode_select_form(
            player,
            title=f"加载到原坐标: {meta.name}",
            callback=lambda p, reliable: self.plugin.load_structure_for_player(
                p, structure_id,
                (meta.min_x, meta.min_y, meta.min_z),
                reliable_mode=reliable
            )
        )

    def _on_load_current_position(self, player, structure_id: str):
        """加载到当前位置（先弹出模式选择）"""
        loc = player.location
        target_pos = (int(loc.x), int(loc.y), int(loc.z))
        self._show_mode_select_form(
            player,
            title="加载到当前位置",
            callback=lambda p, reliable: self.plugin.load_structure_for_player(
                p, structure_id, target_pos,
                reliable_mode=reliable
            )
        )

    def _on_load_current_position_public(self, player, structure_id: str):
        """加载公开建筑到当前位置（先弹出模式选择）"""
        loc = player.location
        target_pos = (int(loc.x), int(loc.y), int(loc.z))
        self._show_mode_select_form(
            player,
            title="加载公开建筑到当前位置",
            callback=lambda p, reliable: self.plugin.load_structure_for_player(
                p, structure_id, target_pos,
                source_type="public", reliable_mode=reliable
            )
        )

    def _on_load_current_position_admin(self, player, structure_id: str, dir_name: str):
        """管理员加载建筑到当前位置（先弹出模式选择）"""
        loc = player.location
        target_pos = (int(loc.x), int(loc.y), int(loc.z))
        self._show_mode_select_form(
            player,
            title="管理员加载建筑到当前位置",
            callback=lambda p, reliable: self.plugin.load_structure_for_player(
                p, structure_id, target_pos,
                source_type="admin", source_dir=dir_name, reliable_mode=reliable
            )
        )

    def show_load_position_form(self, player, structure_id: str, meta,
                                 source_type: str = "player",
                                 source_dir: str = ""):
        """显示加载位置输入表单（含传统可靠模式 Toggle）"""
        form = ModalForm(
            title=_b("选择加载坐标", C_GREEN),
            controls=[
                Label(
                    f"{C_YELLOW}原始坐标: {C_WHITE}({meta.min_x}, {meta.min_y}, {meta.min_z})\n"
                    f"{C_WHITE}输入新的加载起点坐标"
                ),
                TextInput(f"{_b('X 坐标', C_RED)}", placeholder="X", default_value=str(meta.min_x)),
                TextInput(f"{_b('Y 坐标', C_GREEN)}", placeholder="Y", default_value=str(meta.min_y)),
                TextInput(f"{_b('Z 坐标', C_AQUA)}", placeholder="Z", default_value=str(meta.min_z)),
                Toggle(
                    f"{_b('传统可靠模式', C_RED)} {C_WHITE}(全部区块逐个 TP，防漏区块，速度较慢)",
                    default_value=False
                ),
            ],
            on_submit=lambda p, data: self._on_load_position_submit(
                p, data, structure_id, source_type, source_dir
            ),
            on_close=lambda p: p.send_message(f"{C_YELLOW}已取消加载")
        )
        player.send_form(form)

    def _on_load_position_submit(self, player, data_str: str,
                                  structure_id: str,
                                  source_type: str = "player",
                                  source_dir: str = ""):
        """加载到指定坐标提交"""
        try:
            data = json.loads(data_str)
            # Label 在 JSON 数据中会产生 null 値,所以:
            # data[0] = null (Label)
            # data[1] = X 坐标 (TextInput)
            # data[2] = Y 坐标 (TextInput)
            # data[3] = Z 坐标 (TextInput)
            # data[4] = 传统可靠模式 (Toggle)
            x = int(data[1])
            y = int(data[2])
            z = int(data[3])
            reliable_mode = bool(data[4]) if len(data) > 4 and data[4] is not None else False

            self.plugin.load_structure_for_player(
                player, structure_id, (x, y, z),
                source_type=source_type,
                source_dir=source_dir,
                reliable_mode=reliable_mode
            )
        except (ValueError, IndexError):
            player.send_message(f"{C_RED}坐标格式错误!请输入整数")
        except Exception as e:
            player.send_message(f"{C_RED}加载失败: {str(e)}")

    def _on_publish(self, player, structure_id: str):
        """公开建筑"""
        form = MessageForm(
            title=_b("确认公开", C_YELLOW),
            content=f"{C_YELLOW}确定要公开此建筑吗?\n\n{C_WHITE}公开后,所有有权限的玩家都可以加载使用此建筑。",
            button1=_b("确认公开", C_GREEN),
            button2=_b("取消", C_RED),
            on_submit=lambda p, idx: self._do_publish(p, structure_id, idx)
        )
        player.send_form(form)

    def _do_publish(self, player, structure_id: str, button_idx: int):
        """执行公开"""
        if button_idx == 0:
            # v1.0.0: 公开建筑权限检查（此前 allow_public_share 从未被执行）
            if not self.plugin._check_normal_permission(player, "allow_public_share"):
                player.send_message(f"{C_RED}你没有公开建筑的权限!")
                return
            p_uuid = str(player.unique_id)
            p_name = player.name
            ok = self.plugin.db_manager.publish_structure(p_uuid, structure_id, p_name)
            if ok:
                player.send_message(f"{C_GREEN}建筑已公开!")
                self.show_my_structures(player)  # 刷新我的建筑列表
            else:
                player.send_message(f"{C_RED}公开失败!")

    def _confirm_delete(self, player, structure_id: str):
        """确认删除"""
        form = MessageForm(
            title=_b("确认删除", C_RED),
            content=f"{C_RED}{C_BOLD}警告!{C_RESET}\n\n{C_YELLOW}删除后无法恢复,确定要删除此建筑吗?",
            button1=_b("确认删除", C_RED),
            button2=_b("取消", C_GREEN),
            on_submit=lambda p, idx: self._do_delete(p, structure_id, idx)
        )
        player.send_form(form)

    def _do_delete(self, player, structure_id: str, button_idx: int):
        """执行删除（v1.0.0: 走统一入口，确保 allow_delete_own 权限检查生效）"""
        if button_idx == 0:
            self.plugin.delete_structure_for_player(player, structure_id)
            self.show_my_structures(player)

    # ================================================================
    #  公开建筑
    # ================================================================
    def show_public_structures(self, player):
        """显示公开建筑列表"""
        # v1.0.0: 查看公开建筑权限检查（此前 allow_public_view 从未被执行）
        if not self.plugin._check_normal_permission(player, "allow_public_view"):
            player.send_message(f"{C_RED}你没有查看公开建筑的权限!")
            return

        structures = self.plugin.db_manager.get_public_structures()

        if not structures:
            form = MessageForm(
                title=_b("公开建筑", C_YELLOW),
                content=f"{C_WHITE}暂无公开建筑",
                button1=_b("返回主菜单", C_GREEN),
                button2=_b("关闭", C_WHITE),
                on_submit=lambda p, idx: self.show_main_menu(p) if idx == 0 else None
            )
            player.send_form(form)
            return

        form = ActionForm(
            title=_b("公开建筑", C_YELLOW),
            content=f"{C_AQUA}共 {_b(str(len(structures)), C_GREEN)} 个公开建筑"
        )

        for s in structures:
            size = s.get_size()
            owner = s.player_name or "未知"
            btn_text = (
                f"{_b(s.name, C_GREEN)}\n"
                f"{C_WHITE}by {owner} | {size[0]}x{size[1]}x{size[2]}"
            )
            sid = s.structure_id
            form.add_button(
                btn_text,
                icon="textures/blocks/structure_block",
                on_click=lambda p, _sid=sid: self.show_public_structure_detail(p, _sid)
            )

        form.add_button(
            _b("返回主菜单", C_YELLOW),
            icon="textures/ui/arrow_left",
            on_click=lambda p: self.show_main_menu(p)
        )

        player.send_form(form)

    def show_public_structure_detail(self, player, structure_id: str):
        """显示公开建筑详情"""
        meta = self.plugin.db_manager.get_public_structure_metadata(structure_id)
        if meta is None:
            player.send_message(f"{C_RED}建筑不存在!")
            return

        size = meta.get_size()
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(meta.timestamp))
        owner = meta.player_name or "未知"

        form = ActionForm(
            title=_b(meta.name, C_GOLD),
            content=(
                f"{C_AQUA}作者: {C_WHITE}{owner}\n"
                f"{C_AQUA}尺寸: {C_WHITE}{size[0]} x {size[1]} x {size[2]}\n"
                f"{C_AQUA}方块数: {C_WHITE}{meta.block_count}\n"
                f"{C_AQUA}维度: {C_WHITE}{meta.dimension}\n"
                f"{C_AQUA}时间: {C_WHITE}{ts}"
            )
        )

        sid = structure_id
        # 原有按鈕：加载到原坐标
        form.add_button(
            _b("加载到原坐标", C_GREEN),
            icon="textures/ui/icon_recipe_nature",
            on_click=lambda p, _sid=sid, _m=meta: self._show_mode_select_form(
                p, f"加载到原坐标: {_m.name}",
                lambda _p, reliable: self.plugin.load_structure_for_player(
                    _p, _sid, (_m.min_x, _m.min_y, _m.min_z),
                    source_type="public", reliable_mode=reliable
                )
            )
        )
        # 原有按钮：加载建筑（当前位置）
        form.add_button(
            _b("加载建筑 (当前位置)", C_BLUE),
            icon="textures/ui/icon_recipe_item",
            on_click=lambda p, _sid=sid: self._on_load_current_position_public(p, _sid)
        )
        # 原有按钮：加载到指定坐标
        form.add_button(
            _b("加载到指定坐标", C_AQUA),
            icon="textures/ui/icon_setting",
            on_click=lambda p, _sid=sid, _m=meta: self.show_load_position_form(
                p, _sid, _m, source_type="public"
            )
        )
        # 新增按钮：加载建筑（预览模式）
        form.add_button(
            _b("加载建筑 (预览模式)", C_LIGHT_PURPLE),
            icon="textures/ui/magnifyingGlass",
            on_click=lambda p, _sid=sid, _m=meta: self.show_load_config_form(
                p, _sid, _m, source_type="public"
            )
        )

        is_admin = self.plugin.perm_manager.is_game_admin(player.name, player.is_op)
        if is_admin:
            form.add_button(
                _b("取消公开", C_RED),
                icon="textures/ui/trash",
                on_click=lambda p, _sid=sid: self._confirm_unpublish(p, _sid)
            )

        form.add_button(
            _b("返回列表", C_YELLOW),
            icon="textures/ui/arrow_left",
            on_click=lambda p: self.show_public_structures(p)
        )

        player.send_form(form)

    def _confirm_unpublish(self, player, structure_id: str):
        """确认取消公开"""
        form = MessageForm(
            title=_b("确认取消公开", C_RED),
            content=f"{C_YELLOW}确定要取消公开此建筑吗?",
            button1=_b("确认", C_RED),
            button2=_b("取消", C_GREEN),
            on_submit=lambda p, idx: self._do_unpublish(p, structure_id, idx)
        )
        player.send_form(form)

    def _do_unpublish(self, player, structure_id: str, button_idx: int):
        """执行取消公开"""
        if button_idx == 0:
            ok = self.plugin.db_manager.unpublish_structure(structure_id)
            if ok:
                player.send_message(f"{C_GREEN}已取消公开!")
                self.show_public_structures(player)  # 刺新公开建筑列表
            else:
                player.send_message(f"{C_RED}操作失败!")

    # ================================================================
    #  刷新区块
    # ================================================================
    def _on_refresh_chunks(self, player):
        """刷新区块 - 通过传送玩家实现"""
        form = MessageForm(
            title=_b("刷新区块", C_LIGHT_PURPLE),
            content=(
                f"{C_YELLOW}刷新区块将会:\n\n"
                f"{C_WHITE}1. 将你传送到远处\n"
                f"{C_WHITE}2. 等待区块卸载\n"
                f"{C_WHITE}3. 传送回原位\n\n"
                f"{C_WHITE}这可以强制重新加载周围的区块"
            ),
            button1=_b("开始刷新", C_GREEN),
            button2=_b("取消", C_RED),
            on_submit=lambda p, idx: self.plugin.refresh_chunks_for_player(p) if idx == 0 else None
        )
        player.send_form(form)

    # ================================================================
    #  配置表单 (仅 OP)
    # ================================================================
    def show_config_form(self, player):
        """显示配置表单"""
        cfg = self.plugin.config_manager

        form = ModalForm(
            title=_b("插件设置", C_GOLD),
            controls=[
                Toggle(
                    f"{_b('启用插件', C_RED)} (关闭后普通玩家无法使用)",
                    default_value=cfg.plugin_enabled
                ),
                TextInput(
                    f"{_b('最大方块数', C_YELLOW)}",
                    placeholder="最大方块数",
                    default_value=str(cfg.max_block_count)
                ),
                TextInput(
                    f"{_b('粒子效果ID', C_AQUA)}",
                    placeholder="minecraft:endrod",
                    default_value=cfg.particle_id
                ),
                Toggle(
                    f"{_b('默认保留空气', C_GREEN)}",
                    default_value=cfg.preserve_air
                ),
                TextInput(
                    f"{_b('排除方块列表', C_RED)} (逗号分隔)",
                    placeholder="minecraft:grass,minecraft:dirt",
                    default_value=",".join(cfg.excluded_blocks)
                ),
                TextInput(
                    f"{_b('授权玩家保存上限', C_LIGHT_PURPLE)}",
                    placeholder="20",
                    default_value=str(cfg.authorized_save_limit)
                ),
                Toggle(
                    f"{_b('启用菜单物品', C_DARK_AQUA)}",
                    default_value=cfg.menu_item_enabled
                ),
                TextInput(
                    f"{_b('菜单物品ID', C_DARK_GREEN)}",
                    placeholder="minecraft:compass",
                    default_value=cfg.menu_item_id
                ),
                TextInput(
                    f"{_b('脚下方块ID', C_LIGHT_PURPLE)}",
                    placeholder="minecraft:oak_planks",
                    default_value=cfg.foot_block_id
                ),
                TextInput(
                    f"{_b('选点物品ID', C_YELLOW)}",
                    placeholder="minecraft:wooden_axe",
                    default_value=cfg.selection_item_id
                ),
                TextInput(
                    f"{_b('修改物品ID', C_AQUA)}",
                    placeholder="minecraft:stick",
                    default_value=cfg.modify_item_id
                ),
                Toggle(
                    f"{_b('启用普通玩家权限管理', C_LIGHT_PURPLE)}",
                    default_value=cfg.normal_player_permissions_enabled
                ),
                TextInput(
                    f"{_b('区块等待超时时间（秒）', C_YELLOW)} 快速模式上限×0.5，可靠模式即此值，范围10-300",
                    placeholder="60",
                    default_value=str(cfg.chunk_wait_timeout_seconds)
                ),
            ],
            on_submit=lambda p, data: self._on_config_submit(p, data),
            on_close=lambda p: p.send_message(f"{C_YELLOW}已取消设置")
        )
        player.send_form(form)

    def _on_config_submit(self, player, data_str: str):
        """配置提交回调"""
        try:
            data = json.loads(data_str)
            cfg = self.plugin.config_manager

            # data[0]=plugin_enabled, data[1]=max_block, data[2]=particle
            # data[3]=preserve_air, data[4]=excluded, data[5]=save_limit
            # data[6]=menu_enabled, data[7]=menu_item, data[8]=foot_block
            # data[9]=selection_item, data[10]=modify_item, data[11]=normal_perm_enabled
            # data[12]=chunk_wait_timeout_seconds
            plugin_enabled = bool(data[0])
            max_block = int(data[1]) if data[1] else 100000
            particle = str(data[2]).strip() if data[2] else "minecraft:endrod"
            preserve_air = bool(data[3])
            excluded_str = str(data[4]).strip()
            excluded = [b.strip() for b in excluded_str.split(",") if b.strip()] if excluded_str else []
            save_limit = int(data[5]) if data[5] else 20
            menu_enabled = bool(data[6])
            menu_item = str(data[7]).strip() if data[7] else "minecraft:compass"
            foot_block = str(data[8]).strip() if len(data) > 8 and data[8] else "minecraft:oak_planks"
            selection_item = str(data[9]).strip() if len(data) > 9 and data[9] else "minecraft:wooden_axe"
            modify_item = str(data[10]).strip() if len(data) > 10 and data[10] else "minecraft:stick"
            normal_perm_enabled = bool(data[11]) if len(data) > 11 else False
            try:
                chunk_timeout = max(10, min(300, int(data[12]))) if len(data) > 12 and data[12] else 60
            except (ValueError, TypeError):
                chunk_timeout = 60

            cfg.update_all({
                "plugin_enabled": plugin_enabled,
                "max_block_count": max_block,
                "particle_id": particle,
                "preserve_air": preserve_air,
                "excluded_blocks": excluded,
                "authorized_save_limit": save_limit,
                "menu_item_enabled": menu_enabled,
                "menu_item_id": menu_item,
                "foot_block_id": foot_block,
                "selection_item_id": selection_item,
                "modify_item_id": modify_item,
                "normal_player_permissions_enabled": normal_perm_enabled,
                "chunk_wait_timeout_seconds": chunk_timeout,
            })

            player.send_message(f"{C_GREEN}配置已保存并实时生效!")

        except Exception as e:
            player.send_message(f"{C_RED}保存配置失败: {str(e)}")

    # ================================================================
    #  权限管理表单 (仅 OP)
    # ================================================================
    def show_permission_form(self, player):
        """显示权限管理表单"""
        perm = self.plugin.perm_manager
        auth_list = perm.get_authorized_players()
        admin_list = perm.get_game_admins()

        form = ActionForm(
            title=_b("权限管理", C_BLUE),
            content=(
                f"{C_YELLOW}授权玩家: {C_WHITE}{', '.join(auth_list) if auth_list else '无'}\n"
                f"{C_YELLOW}游戏管理员: {C_WHITE}{', '.join(admin_list) if admin_list else '无'}"
            )
        )

        form.add_button(
            _b("添加授权玩家", C_GREEN),
            icon="textures/ui/icon_new",
            on_click=lambda p: self._show_add_auth_form(p)
        )
        form.add_button(
            _b("移除授权玩家", C_RED),
            icon="textures/ui/trash",
            on_click=lambda p: self._show_remove_auth_form(p)
        )
        form.add_button(
            _b("添加游戏管理员", C_GOLD),
            icon="textures/ui/op",
            on_click=lambda p: self._show_add_admin_form(p)
        )
        form.add_button(
            _b("移除游戏管理员", C_DARK_RED),
            icon="textures/ui/trash",
            on_click=lambda p: self._show_remove_admin_form(p)
        )
        form.add_button(
            _b("返回主菜单", C_YELLOW),
            icon="textures/ui/arrow_left",
            on_click=lambda p: self.show_main_menu(p)
        )

        player.send_form(form)

    def _show_add_auth_form(self, player):
        """添加授权玩家表单"""
        form = ModalForm(
            title=_b("添加授权玩家", C_GREEN),
            controls=[
                TextInput(f"{_b('玩家名称', C_YELLOW)}", placeholder="输入玩家名称"),
            ],
            on_submit=lambda p, data: self._do_add_auth(p, data),
            on_close=lambda p: self.show_permission_form(p)
        )
        player.send_form(form)

    def _do_add_auth(self, player, data_str: str):
        """执行添加授权"""
        try:
            data = json.loads(data_str)
            name = str(data[0]).strip()
            if not name:
                player.send_message(f"{C_RED}玩家名称不能为空!")
                return
            ok = self.plugin.perm_manager.add_authorized_player(name)
            if ok:
                player.send_message(f"{C_GREEN}已授权玩家: {_b(name, C_YELLOW)}")
            else:
                player.send_message(f"{C_YELLOW}玩家 {name} 已经被授权了")
        except Exception as e:
            player.send_message(f"{C_RED}操作失败: {str(e)}")

    def _show_remove_auth_form(self, player):
        """移除授权玩家表单"""
        auth_list = self.plugin.perm_manager.get_authorized_players()
        if not auth_list:
            player.send_message(f"{C_YELLOW}没有已授权的玩家")
            return

        form = ActionForm(
            title=_b("移除授权玩家", C_RED),
            content=f"{C_YELLOW}选择要移除的玩家"
        )

        for name in auth_list:
            form.add_button(
                f"{C_WHITE}{name}",
                icon="textures/ui/icon_steve",
                on_click=lambda p, _n=name: self._confirm_remove_auth(p, _n)
            )

        form.add_button(
            _b("返回", C_YELLOW),
            icon="textures/ui/arrow_left",
            on_click=lambda p: self.show_permission_form(p)
        )
        player.send_form(form)

    def _confirm_remove_auth(self, player, name: str):
        """确认移除授权"""
        form = MessageForm(
            title=_b("确认移除", C_RED),
            content=f"{C_YELLOW}确定要移除 {_b(name, C_WHITE)} 的授权吗?",
            button1=_b("确认", C_RED),
            button2=_b("取消", C_GREEN),
            on_submit=lambda p, idx: self._do_remove_auth(p, name, idx)
        )
        player.send_form(form)

    def _do_remove_auth(self, player, name: str, idx: int):
        """执行移除授权"""
        if idx == 0:
            self.plugin.perm_manager.remove_authorized_player(name)
            player.send_message(f"{C_GREEN}已移除 {name} 的授权")

    def _show_add_admin_form(self, player):
        """添加游戏管理员表单"""
        form = ModalForm(
            title=_b("添加游戏管理员", C_GOLD),
            controls=[
                TextInput(f"{_b('玩家名称', C_YELLOW)}", placeholder="输入玩家名称"),
            ],
            on_submit=lambda p, data: self._do_add_admin(p, data),
            on_close=lambda p: self.show_permission_form(p)
        )
        player.send_form(form)

    def _do_add_admin(self, player, data_str: str):
        """执行添加管理员"""
        try:
            data = json.loads(data_str)
            name = str(data[0]).strip()
            if not name:
                player.send_message(f"{C_RED}玩家名称不能为空!")
                return
            ok = self.plugin.perm_manager.add_game_admin(name)
            if ok:
                player.send_message(f"{C_GREEN}已添加游戏管理员: {_b(name, C_GOLD)}")
            else:
                player.send_message(f"{C_YELLOW}玩家 {name} 已经是管理员了")
        except Exception as e:
            player.send_message(f"{C_RED}操作失败: {str(e)}")

    def _show_remove_admin_form(self, player):
        """移除游戏管理员表单"""
        admin_list = self.plugin.perm_manager.get_game_admins()
        if not admin_list:
            player.send_message(f"{C_YELLOW}没有游戏管理员")
            return

        form = ActionForm(
            title=_b("移除游戏管理员", C_DARK_RED),
            content=f"{C_YELLOW}选择要移除的管理员"
        )

        for name in admin_list:
            form.add_button(
                f"{C_WHITE}{name}",
                icon="textures/ui/icon_steve",
                on_click=lambda p, _n=name: self._confirm_remove_admin(p, _n)
            )

        form.add_button(
            _b("返回", C_YELLOW),
            icon="textures/ui/arrow_left",
            on_click=lambda p: self.show_permission_form(p)
        )
        player.send_form(form)

    def _confirm_remove_admin(self, player, name: str):
        """确认移除管理员"""
        form = MessageForm(
            title=_b("确认移除", C_RED),
            content=f"{C_YELLOW}确定要移除 {_b(name, C_WHITE)} 的管理员权限吗?",
            button1=_b("确认", C_RED),
            button2=_b("取消", C_GREEN),
            on_submit=lambda p, idx: self._do_remove_admin(p, name, idx)
        )
        player.send_form(form)

    def _do_remove_admin(self, player, name: str, idx: int):
        """执行移除管理员"""
        if idx == 0:
            self.plugin.perm_manager.remove_game_admin(name)
            player.send_message(f"{C_GREEN}已移除 {name} 的管理员权限")

    # ================================================================
    #  管理员面板
    # ================================================================
    def show_admin_panel(self, player):
        """显示管理员面板"""
        form = ActionForm(
            title=_b("管理员面板", C_GOLD),
            content=f"{C_YELLOW}管理所有玩家的建筑数据"
        )

        form.add_button(
            _b("浏览玩家数据", C_AQUA),
            icon="textures/ui/icon_book_writable",
            on_click=lambda p: self.show_admin_player_list(p)
        )
        form.add_button(
            _b("管理公开建筑", C_YELLOW),
            icon="textures/ui/world_glyph_color",
            on_click=lambda p: self.show_public_structures(p)
        )
        form.add_button(
            _b("返回主菜单", C_YELLOW),
            icon="textures/ui/arrow_left",
            on_click=lambda p: self.show_main_menu(p)
        )

        player.send_form(form)

    def show_admin_player_list(self, player):
        """管理员: 显示所有玩家列表"""
        players = self.plugin.db_manager.get_all_players()

        if not players:
            player.send_message(f"{C_YELLOW}暂无玩家数据")
            return

        form = ActionForm(
            title=_b("玩家数据", C_AQUA),
            content=f"{C_YELLOW}共 {_b(str(len(players)), C_GREEN)} 个玩家有数据"
        )

        for dir_name, display_name in players:
            form.add_button(
                f"{_b(display_name, C_GREEN)}\n{C_WHITE}{dir_name}",
                icon="textures/ui/icon_steve",
                on_click=lambda p, _dn=dir_name, _dp=display_name: self.show_admin_player_structures(p, _dn, _dp)
            )

        form.add_button(
            _b("返回", C_YELLOW),
            icon="textures/ui/arrow_left",
            on_click=lambda p: self.show_admin_panel(p)
        )

        player.send_form(form)

    def show_admin_player_structures(self, player, dir_name: str, display_name: str):
        """管理员: 显示指定玩家的建筑列表"""
        structures = self.plugin.db_manager.get_all_structures_for_player_dir(dir_name)

        if not structures:
            player.send_message(f"{C_YELLOW}该玩家没有保存的建筑")
            return

        form = ActionForm(
            title=_b(f"{display_name} 的建筑", C_AQUA),
            content=f"{C_YELLOW}共 {_b(str(len(structures)), C_GREEN)} 个建筑"
        )

        for s in structures:
            size = s.get_size()
            ts = time.strftime("%m/%d %H:%M", time.localtime(s.timestamp))
            btn_text = (
                f"{_b(s.name, C_GREEN)}\n"
                f"{C_WHITE}{size[0]}x{size[1]}x{size[2]} | {ts}"
            )
            sid = s.structure_id
            form.add_button(
                btn_text,
                icon="textures/blocks/structure_block",
                on_click=lambda p, _sid=sid, _dn=dir_name, _dp=display_name: self.show_admin_structure_detail(p, _sid, _dn, _dp)
            )

        form.add_button(
            _b("返回玩家列表", C_YELLOW),
            icon="textures/ui/arrow_left",
            on_click=lambda p: self.show_admin_player_list(p)
        )

        player.send_form(form)

    def show_admin_structure_detail(self, player, structure_id: str,
                                     dir_name: str, display_name: str):
        """管理员: 显示建筑详情"""
        meta = self.plugin.db_manager.get_metadata_from_dir(dir_name, structure_id)
        if meta is None:
            player.send_message(f"{C_RED}建筑不存在!")
            return

        size = meta.get_size()
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(meta.timestamp))

        form = ActionForm(
            title=_b(meta.name, C_GOLD),
            content=(
                f"{C_AQUA}所有者: {C_WHITE}{display_name}\n"
                f"{C_AQUA}建筑ID: {C_WHITE}{meta.structure_id}\n"
                f"{C_AQUA}尺寸: {C_WHITE}{size[0]} x {size[1]} x {size[2]}\n"
                f"{C_AQUA}方块数: {C_WHITE}{meta.block_count}\n"
                f"{C_AQUA}坐标: {C_WHITE}({meta.min_x},{meta.min_y},{meta.min_z}) ~ ({meta.max_x},{meta.max_y},{meta.max_z})\n"
                f"{C_AQUA}维度: {C_WHITE}{meta.dimension}\n"
                f"{C_AQUA}时间: {C_WHITE}{ts}"
            )
        )

        sid = structure_id
        dn = dir_name
        dp = display_name

        form.add_button(
            _b("加载到原坐标", C_GREEN),
            icon="textures/ui/icon_recipe_nature",
            on_click=lambda p, _sid=sid, _m=meta, _dn=dn: self._show_mode_select_form(
                p, f"加载到原坐标: {_m.name}",
                lambda _p, reliable: self.plugin.load_structure_for_player(
                    _p, _sid, (_m.min_x, _m.min_y, _m.min_z),
                    source_type="admin", source_dir=_dn, reliable_mode=reliable
                )
            )
        )
        form.add_button(
            _b("加载建筑 (当前位置)", C_BLUE),
            icon="textures/ui/icon_recipe_item",
            on_click=lambda p, _sid=sid, _dn=dn: self._on_load_current_position_admin(p, _sid, _dn)
        )
        form.add_button(
            _b("加载到指定坐标", C_AQUA),
            icon="textures/ui/icon_setting",
            on_click=lambda p, _sid=sid, _m=meta, _dn=dn: self.show_load_position_form(
                p, _sid, _m, source_type="admin", source_dir=_dn
            )
        )
        form.add_button(
            _b("加载建筑 (预览模式)", C_LIGHT_PURPLE),
            icon="textures/ui/magnifyingGlass",
            on_click=lambda p, _sid=sid, _m=meta, _dn=dn: self.show_load_config_form(
                p, _sid, _m, source_type="admin", source_dir=_dn
            )
        )
        form.add_button(
            _b("导入到我的建筑", C_LIGHT_PURPLE),
            icon="textures/ui/import",
            on_click=lambda p, _sid=sid, _dn=dn: self._confirm_import(p, _sid, _dn)
        )
        form.add_button(
            _b("公开此建筑", C_YELLOW),
            icon="textures/ui/world_glyph_color",
            on_click=lambda p, _sid=sid, _dn=dn: self._confirm_admin_publish(p, _sid, _dn)
        )
        form.add_button(
            _b("删除此建筑", C_RED),
            icon="textures/ui/trash",
            on_click=lambda p, _sid=sid, _dn=dn, _dp=dp: self._confirm_admin_delete(p, _sid, _dn, _dp)
        )
        form.add_button(
            _b("返回列表", C_YELLOW),
            icon="textures/ui/arrow_left",
            on_click=lambda p, _dn=dn, _dp=dp: self.show_admin_player_structures(p, _dn, _dp)
        )

        player.send_form(form)

    def _confirm_import(self, player, structure_id: str, source_dir: str):
        """确认导入"""
        form = MessageForm(
            title=_b("确认导入", C_LIGHT_PURPLE),
            content=f"{C_YELLOW}确定要将此建筑导入到你的建筑库吗?",
            button1=_b("确认", C_GREEN),
            button2=_b("取消", C_RED),
            on_submit=lambda p, idx: self._do_import(p, structure_id, source_dir, idx)
        )
        player.send_form(form)

    def _do_import(self, player, structure_id: str, source_dir: str, idx: int):
        """执行导入"""
        if idx == 0:
            new_id = self.plugin.db_manager.import_structure_to_player(
                source_dir, structure_id, str(player.unique_id), player.name
            )
            ok = new_id is not None
            if ok:
                player.send_message(f"{C_GREEN}建筑已导入到你的建筑库!")
                self.show_my_structures(player)  # 刺新我的建筑列表
            else:
                player.send_message(f"{C_RED}导入失败!")

    def _confirm_admin_publish(self, player, structure_id: str, source_dir: str):
        """管理员确认公开"""
        form = MessageForm(
            title=_b("确认公开", C_YELLOW),
            content=f"{C_YELLOW}确定要公开此建筑吗?",
            button1=_b("确认", C_GREEN),
            button2=_b("取消", C_RED),
            on_submit=lambda p, idx: self._do_admin_publish(p, structure_id, source_dir, idx)
        )
        player.send_form(form)

    def _do_admin_publish(self, player, structure_id: str, source_dir: str, idx: int):
        """管理员执行公开"""
        if idx == 0:
            ok = self.plugin.db_manager.admin_publish_structure(structure_id, source_dir)
            if ok:
                player.send_message(f"{C_GREEN}建筑已公开!")
                self.show_public_structures(player)  # 刺新公开建筑列表
            else:
                player.send_message(f"{C_RED}公开失败!")

    def _confirm_admin_delete(self, player, structure_id: str, dir_name: str, display_name: str):
        """管理员确认删除"""
        form = MessageForm(
            title=_b("确认删除", C_RED),
            content=f"{C_RED}{C_BOLD}警告!{C_RESET}\n\n{C_YELLOW}删除后无法恢复,确定要删除此建筑吗?",
            button1=_b("确认删除", C_RED),
            button2=_b("取消", C_GREEN),
            on_submit=lambda p, idx: self._do_admin_delete(p, structure_id, dir_name, display_name, idx)
        )
        player.send_form(form)

    def _do_admin_delete(self, player, structure_id: str, dir_name: str, display_name: str, idx: int):
        """管理员执行删除"""
        if idx == 0:
            self.plugin.db_manager.delete_structure_from_dir(dir_name, structure_id)
            player.send_message(f"{C_GREEN}建筑已删除!")
            self.show_admin_player_structures(player, dir_name, display_name)

    # ================================================================
    #  普通玩家权限管理
    # ================================================================
    def show_normal_permission_menu(self, player):
        """显示普通玩家权限管理菜单"""
        form = ActionForm(
            title=_b("普通玩家权限管理", C_LIGHT_PURPLE),
            content=f"{C_YELLOW}管理普通玩家的细分权限"
        )

        form.add_button(
            _b("所有玩家权限管理", C_YELLOW),
            icon="textures/ui/world_glyph_color",
            on_click=lambda p: self.show_default_normal_permissions(p)
        )

        form.add_button(
            _b("单独玩家权限管理", C_AQUA),
            icon="textures/ui/icon_steve",
            on_click=lambda p: self.show_player_permission_select(p)
        )

        form.add_button(
            _b("返回主菜单", C_WHITE),
            icon="textures/ui/arrow_left",
            on_click=lambda p: self.show_main_menu(p)
        )

        player.send_form(form)

    def show_default_normal_permissions(self, player):
        """显示默认普通玩家权限设置表单"""
        normal_perm_mgr = self.plugin.normal_perm_manager
        default_perms = normal_perm_mgr.get_default_permissions()

        form = ModalForm(
            title=_b("默认普通玩家权限", C_GOLD),
            controls=[
                Label(
                    f"{C_AQUA}以下权限将应用于所有未单独设置权限的普通玩家\n"
                    f"{C_YELLOW}(授权玩家和管理员不受此限制)"
                ),
                Label(f"{_b('功能权限', C_GREEN)}"),
                Toggle(
                    f"{C_WHITE}允许复制(保存建筑)",
                    default_value=default_perms.get("allow_copy", True)
                ),
                Toggle(
                    f"{C_WHITE}允许粘贴(加载建筑)",
                    default_value=default_perms.get("allow_paste", False)
                ),
                Toggle(
                    f"{C_WHITE}允许删除自己的建筑",
                    default_value=default_perms.get("allow_delete_own", False)
                ),
                Toggle(
                    f"{C_WHITE}允许查看公开建筑",
                    default_value=default_perms.get("allow_public_view", True)
                ),
                Toggle(
                    f"{C_WHITE}允许加载公开建筑",
                    default_value=default_perms.get("allow_public_load", False)
                ),
                Toggle(
                    f"{C_WHITE}允许公开自己的建筑",
                    default_value=default_perms.get("allow_public_share", False)
                ),
                Toggle(
                    f"{C_WHITE}允许撤销/恢复操作",
                    default_value=default_perms.get("allow_undo_redo", False)
                ),
                Toggle(
                    f"{C_WHITE}允许刷新区块",
                    default_value=default_perms.get("allow_refresh_chunks", False)
                ),
                Toggle(
                    f"{C_WHITE}允许脚下方块放置 {C_RED}(默认关闭，可用于破坏基岩)",
                    default_value=default_perms.get("allow_foot_block", False)
                ),
                Toggle(
                    f"{C_WHITE}允许搬迁模式 {C_RED}(默认关闭，可删除原始方块，谨慎开启)",
                    default_value=default_perms.get("allow_move_mode", False)
                ),
                Label(f"{_b('数量限制', C_YELLOW)}"),
                TextInput(
                    f"{C_WHITE}最大保存数量",
                    placeholder="10",
                    default_value=str(default_perms.get("max_structures", 10))
                ),
                TextInput(
                    f"{C_WHITE}单次复制最大方块数",
                    placeholder="10000",
                    default_value=str(default_perms.get("max_blocks_per_copy", 10000))
                ),
            ],
            on_submit=lambda p, data: self._on_default_normal_perms_submit(p, data),
            on_close=lambda p: self.show_normal_permission_menu(p)
        )

        player.send_form(form)

    def _on_default_normal_perms_submit(self, player, data_str: str):
        """默认普通玩家权限提交回调"""
        try:
            data = json.loads(data_str)
            # data[0] = null (Label)
            # data[1] = null (Label - 功能权限)
            # data[2]  = allow_copy
            # data[3]  = allow_paste
            # data[4]  = allow_delete_own
            # data[5]  = allow_public_view
            # data[6]  = allow_public_load
            # data[7]  = allow_public_share
            # data[8]  = allow_undo_redo
            # data[9]  = allow_refresh_chunks
            # data[10] = allow_foot_block
            # data[11] = allow_move_mode
            # data[12] = null (Label - 数量限制)
            # data[13] = 最大保存数量 (TextInput)
            # data[14] = 单次复制最大方块数 (TextInput)

            if isinstance(data, list) and len(data) >= 15:
                permissions = {
                    "allow_copy": bool(data[2]),
                    "allow_paste": bool(data[3]),
                    "allow_delete_own": bool(data[4]),
                    "allow_public_view": bool(data[5]),
                    "allow_public_load": bool(data[6]),
                    "allow_public_share": bool(data[7]),
                    "allow_undo_redo": bool(data[8]),
                    "allow_refresh_chunks": bool(data[9]),
                    "allow_foot_block": bool(data[10]),
                    "allow_move_mode": bool(data[11]),
                    "max_structures": int(data[13]) if data[13] else 10,
                    "max_blocks_per_copy": int(data[14]) if data[14] else 10000
                }

                normal_perm_mgr = self.plugin.normal_perm_manager
                normal_perm_mgr.save_default_permissions(permissions)
                player.send_message(f"{C_GREEN}[MXCR] 默认普通玩家权限已更新!")
                self.show_normal_permission_menu(player)
            else:
                player.send_message(f"{C_RED}表单数据格式错误!")
        except Exception as e:
            player.send_message(f"{C_RED}保存失败: {str(e)}")

    def show_player_permission_select(self, player):
        """显示玩家选择界面（在线玩家 + 手动输入）"""
        form = ActionForm(
            title=_b("选择玩家", C_AQUA),
            content=f"{C_YELLOW}选择要设置权限的玩家"
        )

        # 显示在线玩家
        online_players = self.plugin.server.online_players
        if online_players:
            for p in online_players:
                uuid_short = str(p.unique_id)[:8]
                form.add_button(
                    f"{_b(p.name, C_GREEN)}\n{C_WHITE}UUID: {uuid_short}",
                    icon="textures/ui/icon_steve",
                    on_click=lambda _p, target=p: self.show_player_permission_form(_p, target.unique_id, target.name)
                )

        # 手动输入玩家名
        form.add_button(
            _b("手动输入玩家名", C_YELLOW),
            icon="textures/ui/icon_book_writable",
            on_click=lambda p: self.show_manual_player_input(p)
        )

        form.add_button(
            _b("返回", C_WHITE),
            icon="textures/ui/arrow_left",
            on_click=lambda p: self.show_normal_permission_menu(p)
        )

        player.send_form(form)

    def show_manual_player_input(self, player):
        """手动输入玩家名表单"""
        form = ModalForm(
            title=_b("手动输入玩家名", C_YELLOW),
            controls=[
                Label(
                    f"{C_AQUA}请输入要设置权限的玩家名称\n"
                    f"{C_YELLOW}注意: 玩家必须至少登录过一次"
                ),
                TextInput(
                    f"{_b('玩家名称', C_GREEN)}",
                    placeholder="请输入玩家名称",
                    default_value=""
                ),
            ],
            on_submit=lambda p, data: self._on_manual_player_input_submit(p, data),
            on_close=lambda p: self.show_player_permission_select(p)
        )

        player.send_form(form)

    def _on_manual_player_input_submit(self, player, data_str: str):
        """手动输入玩家名提交回调"""
        try:
            data = json.loads(data_str)
            # data[0] = null (Label)
            # data[1] = 玩家名称 (TextInput)

            if isinstance(data, list) and len(data) >= 2:
                target_name = str(data[1]).strip() if data[1] else ""
                if not target_name:
                    player.send_message(f"{C_RED}玩家名称不能为空!")
                    return

                # 尝试从数据库查找玩家 UUID
                # 这里需要遍历所有玩家文件夹来查找
                players = self.plugin.db_manager.get_all_players()
                target_uuid = None
                for dir_name, display_name in players:
                    if display_name.lower() == target_name.lower():
                        # 从文件夹名提取 UUID (格式: UUID_昵称)
                        uuid_str = dir_name.split("_")[0]
                        from uuid import UUID
                        target_uuid = UUID(uuid_str)
                        break

                if target_uuid:
                    self.show_player_permission_form(player, target_uuid, target_name)
                else:
                    player.send_message(f"{C_RED}未找到玩家 {target_name} 的数据!")
                    player.send_message(f"{C_YELLOW}该玩家可能从未登录过服务器")
            else:
                player.send_message(f"{C_RED}表单数据格式错误!")
        except Exception as e:
            player.send_message(f"{C_RED}操作失败: {str(e)}")

    def show_player_permission_form(self, player, target_uuid, target_name: str):
        """显示单独玩家权限设置表单"""
        from uuid import UUID
        if isinstance(target_uuid, str):
            target_uuid = UUID(target_uuid)

        normal_perm_mgr = self.plugin.normal_perm_manager
        
        # 获取玩家当前权限（如果有单独设置则使用，否则使用默认）
        current_perms = normal_perm_mgr.get_effective_permissions(target_uuid)

        form = ModalForm(
            title=_b(f"玩家权限设置 - {target_name}", C_GOLD),
            controls=[
                Label(
                    f"{C_AQUA}为 {_b(target_name, C_GREEN)} 设置单独权限\n"
                    f"{C_YELLOW}将覆盖默认普通玩家权限"
                ),
                Label(f"{_b('功能权限', C_GREEN)}"),
                Toggle(
                    f"{C_WHITE}允许复制(保存建筑)",
                    default_value=current_perms.get("allow_copy", True)
                ),
                Toggle(
                    f"{C_WHITE}允许粘贴(加载建筑)",
                    default_value=current_perms.get("allow_paste", False)
                ),
                Toggle(
                    f"{C_WHITE}允许删除自己的建筑",
                    default_value=current_perms.get("allow_delete_own", False)
                ),
                Toggle(
                    f"{C_WHITE}允许查看公开建筑",
                    default_value=current_perms.get("allow_public_view", True)
                ),
                Toggle(
                    f"{C_WHITE}允许加载公开建筑",
                    default_value=current_perms.get("allow_public_load", False)
                ),
                Toggle(
                    f"{C_WHITE}允许公开自己的建筑",
                    default_value=current_perms.get("allow_public_share", False)
                ),
                Toggle(
                    f"{C_WHITE}允许撤销/恢复操作",
                    default_value=current_perms.get("allow_undo_redo", False)
                ),
                Toggle(
                    f"{C_WHITE}允许刷新区块",
                    default_value=current_perms.get("allow_refresh_chunks", False)
                ),
                Toggle(
                    f"{C_WHITE}允许脚下方块放置 {C_RED}(默认关闭，可用于破坏基岩)",
                    default_value=current_perms.get("allow_foot_block", False)
                ),
                Toggle(
                    f"{C_WHITE}允许搬迁模式 {C_RED}(默认关闭，可删除原始方块，谨慎开启)",
                    default_value=current_perms.get("allow_move_mode", False)
                ),
                Label(f"{_b('数量限制', C_YELLOW)}"),
                TextInput(
                    f"{C_WHITE}最大保存数量",
                    placeholder="10",
                    default_value=str(current_perms.get("max_structures", 10))
                ),
                TextInput(
                    f"{C_WHITE}单次复制最大方块数",
                    placeholder="10000",
                    default_value=str(current_perms.get("max_blocks_per_copy", 10000))
                ),
            ],
            on_submit=lambda p, data: self._on_player_permission_submit(p, data, target_uuid, target_name),
            on_close=lambda p: self.show_player_permission_select(p)
        )

        player.send_form(form)

    def _on_player_permission_submit(self, player, data_str: str, target_uuid, target_name: str):
        """单独玩家权限提交回调"""
        try:
            from uuid import UUID
            if isinstance(target_uuid, str):
                target_uuid = UUID(target_uuid)

            data = json.loads(data_str)
            # data[0] = null (Label)
            # data[1] = null (Label - 功能权限)
            # data[2]  = allow_copy
            # data[3]  = allow_paste
            # data[4]  = allow_delete_own
            # data[5]  = allow_public_view
            # data[6]  = allow_public_load
            # data[7]  = allow_public_share
            # data[8]  = allow_undo_redo
            # data[9]  = allow_refresh_chunks
            # data[10] = allow_foot_block
            # data[11] = allow_move_mode
            # data[12] = null (Label - 数量限制)
            # data[13] = 最大保存数量 (TextInput)
            # data[14] = 单次复制最大方块数 (TextInput)

            if isinstance(data, list) and len(data) >= 15:
                permissions = {
                    "allow_copy": bool(data[2]),
                    "allow_paste": bool(data[3]),
                    "allow_delete_own": bool(data[4]),
                    "allow_public_view": bool(data[5]),
                    "allow_public_load": bool(data[6]),
                    "allow_public_share": bool(data[7]),
                    "allow_undo_redo": bool(data[8]),
                    "allow_refresh_chunks": bool(data[9]),
                    "allow_foot_block": bool(data[10]),
                    "allow_move_mode": bool(data[11])
                }

                limits = {
                    "max_structures": int(data[13]) if data[13] else 10,
                    "max_blocks_per_copy": int(data[14]) if data[14] else 10000
                }

                normal_perm_mgr = self.plugin.normal_perm_manager
                normal_perm_mgr.save_player_permission(target_uuid, target_name, permissions, limits)
                player.send_message(f"{C_GREEN}[MXCR] 已为 {target_name} 设置单独权限!")
                self.show_player_permission_select(player)
            else:
                player.send_message(f"{C_RED}表单数据格式错误!")
        except Exception as e:
            player.send_message(f"{C_RED}保存失败: {str(e)}")

    # ================================================================
    #  加载建筑配置表单（包含预览功能）
    # ================================================================
    def show_load_config_form(self, player, structure_id: str, meta,
                              source_type: str = "player", source_dir: str = ""):
        """显示加载建筑配置表单（包含预览选项）"""
        load_forms.show_load_config_form(
            self, player, structure_id, meta, source_type, source_dir
        )
