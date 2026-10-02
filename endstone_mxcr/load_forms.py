"""
加载建筑配置表单模块
包含预览功能的加载配置界面
"""
import json
from endstone.form import ModalForm, ActionForm, TextInput, Toggle, Dropdown, Label

from .api_compat import get_dim_y_limits, get_dim_display_name


# 颜色代码
C_GOLD = "§6"
C_GREEN = "§a"
C_AQUA = "§b"
C_RED = "§c"
C_LIGHT_PURPLE = "§d"
C_YELLOW = "§e"
C_WHITE = "§f"
C_BLUE = "§9"
C_BOLD = "§l"
C_RESET = "§r"


def _b(text: str, color: str = "") -> str:
    """加粗文字"""
    return f"{color}{C_BOLD}{text}{C_RESET}"


def show_load_config_form(form_manager, player, structure_id: str, meta,
                          source_type: str = "player", source_dir: str = ""):
    """显示加载建筑配置表单"""
    loc = player.location
    
    form = ModalForm(
        title=_b("加载建筑配置", C_GREEN),
        controls=[
            Label(
                f"{C_YELLOW}建筑: {C_WHITE}{meta.name}\n"
                f"{C_YELLOW}尺寸: {C_WHITE}{meta.max_x - meta.min_x + 1} x "
                f"{meta.max_y - meta.min_y + 1} x {meta.max_z - meta.min_z + 1}\n"
                f"{C_YELLOW}方块数: {C_WHITE}{meta.block_count}"
            ),
            Dropdown(
                f"{_b('粘贴模式', C_AQUA)}",
                options=[
                    "玩家脚下",
                    "玩家面前 3 格",
                    "自定义坐标"
                ],
                default_index=0
            ),
            TextInput(
                f"{_b('X 偏移量', C_RED)}",
                placeholder="0",
                default_value="0"
            ),
            TextInput(
                f"{_b('Y 偏移量', C_GREEN)}",
                placeholder="0",
                default_value="0"
            ),
            TextInput(
                f"{_b('Z 偏移量', C_AQUA)}",
                placeholder="0",
                default_value="0"
            ),
            Toggle(
                f"{_b('启用预览', C_LIGHT_PURPLE)}",
                default_value=True
            ),
            Toggle(
                f"{_b('传统可靠模式', C_RED)} {C_WHITE}(全部区块逐个 TP，防漏区块，速度较慢)",
                default_value=False
            ),
        ],
        on_submit=lambda p, data: _on_load_config_submit(
            form_manager, p, data, structure_id, meta, source_type, source_dir
        ),
        on_close=lambda p: p.send_message(f"{C_YELLOW}已取消加载")
    )
    player.send_form(form)


def _on_load_config_submit(form_manager, player, data_str: str, structure_id: str,
                           meta, source_type: str, source_dir: str):
    """加载配置提交回调"""
    try:
        data = json.loads(data_str)
        # data[0] = Label (null)
        # data[1] = 粘贴模式 (Dropdown, int)
        # data[2] = X 偏移量 (TextInput, str)
        # data[3] = Y 偏移量 (TextInput, str)
        # data[4] = Z 偏移量 (TextInput, str)
        # data[5] = 启用预览 (Toggle, bool)
        # data[6] = 传统可靠模式 (Toggle, bool)
        
        mode = int(data[1]) if data[1] is not None else 0
        offset_x = int(data[2]) if data[2] and data[2].strip() else 0
        offset_y = int(data[3]) if data[3] and data[3].strip() else 0
        offset_z = int(data[4]) if data[4] and data[4].strip() else 0
        enable_preview = bool(data[5]) if data[5] is not None else False
        reliable_mode = bool(data[6]) if len(data) > 6 and data[6] is not None else False

        # 注意：粘贴位置模式（脚下/面前/自定义）不对应 allow_foot_block/allow_move_mode
        # 这两个权限键分别控制"脚下放置方块工具"和"保存时的搬迁模式"，已在各自入口校验
        plugin = form_manager.plugin

        # 计算目标位置
        loc = player.location

        if mode == 0:  # 玩家脚下
            target_x = int(loc.x) + offset_x
            target_y = int(loc.y) + offset_y
            target_z = int(loc.z) + offset_z
        elif mode == 1:  # 玩家面前 3 格
            # 根据玩家朝向计算前方位置
            yaw = loc.yaw
            import math
            # yaw: 0=南, 90=西, 180=北, 270=东
            forward_x = -math.sin(math.radians(yaw)) * 3
            forward_z = math.cos(math.radians(yaw)) * 3
            target_x = int(loc.x + forward_x) + offset_x
            target_y = int(loc.y) + offset_y
            target_z = int(loc.z + forward_z) + offset_z
        else:  # 自定义坐标
            # 显示自定义坐标输入表单（v1.0.0 修复：传递 reliable_mode，此前在此路径丢失）
            show_custom_coord_form(
                form_manager, player, structure_id, meta,
                offset_x, offset_y, offset_z, enable_preview,
                source_type, source_dir, reliable_mode
            )
            return

        target_pos = (target_x, target_y, target_z)

        # v1.0.0: 高度检测改为维度感知（下界 0~127 / 末地 0~255 / 主世界 -64~319）
        size = (
            meta.max_x - meta.min_x + 1,
            meta.max_y - meta.min_y + 1,
            meta.max_z - meta.min_z + 1
        )
        min_y = target_y
        max_y = target_y + size[1] - 1

        try:
            dim_key = player.location.dimension.name.lower()
        except Exception:
            dim_key = "overworld"
        y_min_limit, y_max_limit = get_dim_y_limits(dim_key)

        if min_y < y_min_limit or max_y > y_max_limit:
            # 超出高度限制，显示警告
            show_height_warning(
                form_manager, player, structure_id, meta,
                target_pos, enable_preview, source_type, source_dir,
                min_y, max_y, reliable_mode
            )
            return

        if enable_preview:
            # 启动预览（v1.0.0 修复：传递 reliable_mode）
            plugin.preview_manager.start_preview(
                player, structure_id, meta, target_pos,
                source_type, source_dir, reliable_mode
            )
        else:
            # 直接加载
            plugin.load_structure_for_player(
                player, structure_id, target_pos,
                source_type=source_type, source_dir=source_dir,
                reliable_mode=reliable_mode
            )

    except ValueError as e:
        player.send_message(f"{C_RED}输入错误: 偏移量必须是整数")
    except Exception as e:
        player.send_message(f"{C_RED}加载失败: {str(e)}")


def show_custom_coord_form(form_manager, player, structure_id: str, meta,
                           offset_x: int, offset_y: int, offset_z: int,
                           enable_preview: bool, source_type: str, source_dir: str,
                           reliable_mode: bool = False):
    """显示自定义坐标输入表单"""
    form = ModalForm(
        title=_b("自定义坐标", C_AQUA),
        controls=[
            Label(f"{C_YELLOW}请输入目标坐标（已包含偏移量）"),
            TextInput(
                f"{_b('X 坐标', C_RED)}",
                placeholder="X",
                default_value=str(meta.min_x + offset_x)
            ),
            TextInput(
                f"{_b('Y 坐标', C_GREEN)}",
                placeholder="Y",
                default_value=str(meta.min_y + offset_y)
            ),
            TextInput(
                f"{_b('Z 坐标', C_AQUA)}",
                placeholder="Z",
                default_value=str(meta.min_z + offset_z)
            ),
        ],
        on_submit=lambda p, data: _on_custom_coord_submit(
            form_manager, p, data, structure_id, meta,
            enable_preview, source_type, source_dir, reliable_mode
        ),
        on_close=lambda p: p.send_message(f"{C_YELLOW}已取消加载")
    )
    player.send_form(form)


def _on_custom_coord_submit(form_manager, player, data_str: str, structure_id: str,
                            meta, enable_preview: bool, source_type: str, source_dir: str,
                            reliable_mode: bool = False):
    """自定义坐标提交回调"""
    try:
        data = json.loads(data_str)
        # data[0] = Label (null)
        # data[1] = X 坐标
        # data[2] = Y 坐标
        # data[3] = Z 坐标
        
        x = int(data[1]) if data[1] else meta.min_x
        y = int(data[2]) if data[2] else meta.min_y
        z = int(data[3]) if data[3] else meta.min_z

        target_pos = (x, y, z)

        # v1.0.0: 高度检测改为维度感知
        size = (
            meta.max_x - meta.min_x + 1,
            meta.max_y - meta.min_y + 1,
            meta.max_z - meta.min_z + 1
        )
        min_y = y
        max_y = y + size[1] - 1

        try:
            dim_key = player.location.dimension.name.lower()
        except Exception:
            dim_key = "overworld"
        y_min_limit, y_max_limit = get_dim_y_limits(dim_key)

        if min_y < y_min_limit or max_y > y_max_limit:
            # 超出高度限制，显示警告
            show_height_warning(
                form_manager, player, structure_id, meta,
                target_pos, enable_preview, source_type, source_dir,
                min_y, max_y, reliable_mode
            )
            return

        if enable_preview:
            # 启动预览（v1.0.0 修复：传递 reliable_mode）
            form_manager.plugin.preview_manager.start_preview(
                player, structure_id, meta, target_pos,
                source_type, source_dir, reliable_mode
            )
        else:
            # 直接加载
            form_manager.plugin.load_structure_for_player(
                player, structure_id, target_pos,
                source_type=source_type, source_dir=source_dir,
                reliable_mode=reliable_mode
            )
            
    except ValueError:
        player.send_message(f"{C_RED}坐标格式错误: 必须是整数")
    except Exception as e:
        player.send_message(f"{C_RED}加载失败: {str(e)}")



def show_height_warning(form_manager, player, structure_id: str, meta,
                        target_pos: tuple, enable_preview: bool,
                        source_type: str, source_dir: str,
                        min_y: int, max_y: int, reliable_mode: bool = False):
    """显示高度警告窗口"""
    from endstone.form import MessageForm

    # v1.0.0: 按当前维度显示真实高度限制
    try:
        dim_key = player.location.dimension.name.lower()
    except Exception:
        dim_key = "overworld"
    y_min_limit, y_max_limit = get_dim_y_limits(dim_key)
    dim_display = get_dim_display_name(dim_key)

    form = MessageForm(
        title=_b("建筑高度警告", C_RED),
        content=(
            f"{C_RED}{C_BOLD}警告！建筑物高度超过 Minecraft 限制！{C_RESET}\n\n"
            f"{C_YELLOW}当前维度: {C_WHITE}{dim_display}\n"
            f"{C_YELLOW}建筑高度范围: {C_WHITE}Y {min_y} ~ {max_y}\n"
            f"{C_YELLOW}Minecraft 限制: {C_WHITE}Y {y_min_limit} ~ {y_max_limit}\n\n"
            f"{C_AQUA}超出范围的方块将无法放置，可能导致建筑不完整。\n"
            f"{C_AQUA}建议调整 Y 偏移量或选择其他位置。"
        ),
        button1=_b("取消", C_GREEN),
        button2=_b("我已确认服务器高度状况，仍要加载", C_RED),
        on_submit=lambda p, idx: _on_height_warning_submit(
            form_manager, p, idx, structure_id, meta,
            target_pos, enable_preview, source_type, source_dir, reliable_mode
        )
    )
    player.send_form(form)


def _on_height_warning_submit(form_manager, player, button_idx: int,
                               structure_id: str, meta, target_pos: tuple,
                               enable_preview: bool, source_type: str, source_dir: str,
                               reliable_mode: bool = False):
    """高度警告提交回调"""
    if button_idx == 0:
        # 取消
        player.send_message(f"{C_YELLOW}已取消加载")
    else:
        # 确认加载
        if enable_preview:
            # 启动预览（v1.0.0 修复：传递 reliable_mode）
            form_manager.plugin.preview_manager.start_preview(
                player, structure_id, meta, target_pos,
                source_type, source_dir, reliable_mode
            )
        else:
            # 直接加载
            form_manager.plugin.load_structure_for_player(
                player, structure_id, target_pos,
                source_type=source_type, source_dir=source_dir,
                reliable_mode=reliable_mode
            )
