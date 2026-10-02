"""
配置管理与权限管理模块
"""
import json
from pathlib import Path
from typing import List, Dict, Any


# ========== 默认配置 ==========
DEFAULT_CONFIG = {
    "plugin_enabled": True,  # 插件总开关
    "max_block_count": 100000,
    "particle_id": "minecraft:endrod",
    "preserve_air": True,
    "excluded_blocks": [],
    "authorized_save_limit": 20,
    "menu_item_enabled": False,
    "menu_item_id": "minecraft:compass",
    "batch_size": 1000,
    "foot_block_id": "minecraft:oak_planks",
    "selection_item_id": "minecraft:wooden_axe",
    "modify_item_id": "minecraft:stick",
    "normal_player_permissions_enabled": False,
    "chunk_wait_timeout_seconds": 60
}

DEFAULT_PERMISSIONS = {
    "authorized_players": [],
    "game_admins": [],
}


class ConfigManager:
    """配置管理器"""

    def __init__(self, data_dir: Path):
        self.config_path = data_dir / "config.json"
        self.config: Dict[str, Any] = {}
        self._load()

    def _load(self):
        """加载配置文件"""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception:
                self.config = {}

        # 合并默认值 (缺失的键用默认值补全)
        for key, val in DEFAULT_CONFIG.items():
            if key not in self.config:
                self.config[key] = val

        self._save()

    def _save(self):
        """保存配置文件"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)

    def reload(self):
        """重新加载配置"""
        self._load()

    def get(self, key: str, default=None):
        """获取配置值"""
        return self.config.get(key, default)

    def set(self, key: str, value):
        """设置配置值并保存"""
        self.config[key] = value
        self._save()

    def get_all(self) -> Dict[str, Any]:
        """获取所有配置"""
        return dict(self.config)

    def update_all(self, new_config: Dict[str, Any]):
        """批量更新配置"""
        for key, val in new_config.items():
            self.config[key] = val
        self._save()

    # ========== 便捷属性 ==========
    @property
    def plugin_enabled(self) -> bool:
        return self.config.get("plugin_enabled", True)

    @property
    def max_block_count(self) -> int:
        return self.config.get("max_block_count", 100000)

    @property
    def particle_id(self) -> str:
        return self.config.get("particle_id", "minecraft:endrod")

    @property
    def preserve_air(self) -> bool:
        return self.config.get("preserve_air", True)

    @property
    def excluded_blocks(self) -> List[str]:
        return self.config.get("excluded_blocks", [])

    @property
    def authorized_save_limit(self) -> int:
        return self.config.get("authorized_save_limit", 20)

    @property
    def menu_item_enabled(self) -> bool:
        return self.config.get("menu_item_enabled", False)

    @property
    def menu_item_id(self) -> str:
        return self.config.get("menu_item_id", "minecraft:compass")

    @property
    def foot_block_id(self) -> str:
        return self.config.get("foot_block_id", "minecraft:oak_planks")

    @property
    def selection_item_id(self) -> str:
        return self.config.get("selection_item_id", "minecraft:wooden_axe")

    @property
    def modify_item_id(self) -> str:
        return self.config.get("modify_item_id", "minecraft:stick")

    @property
    def normal_player_permissions_enabled(self) -> bool:
        return self.config.get("normal_player_permissions_enabled", False)

    @property
    def chunk_wait_timeout_seconds(self) -> int:
        val = self.config.get("chunk_wait_timeout_seconds", 60)
        # 限制范围：最小 10 秒，最大 300 秒
        try:
            return max(10, min(300, int(val)))
        except (TypeError, ValueError):
            return 60


class PermissionManager:
    """权限管理器"""

    def __init__(self, data_dir: Path):
        self.perm_path = data_dir / "permissions.json"
        self.data: Dict[str, List[str]] = {}
        self._load()

    def _load(self):
        """加载权限文件"""
        if self.perm_path.exists():
            try:
                with open(self.perm_path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}

        for key, val in DEFAULT_PERMISSIONS.items():
            if key not in self.data:
                self.data[key] = val

        self._save()

    def _save(self):
        """
        保存权限文件 (v1.0.0 修复)

        采用 read-modify-write 模式：先读取磁盘上的最新内容，
        只更新本管理器负责的键 (authorized_players / game_admins) 后写回，
        保留其他管理器 (NormalPermissionManager) 写入的键，
        修复此前直接写整个内存副本导致 default_normal_permissions 被覆盖丢失的问题。
        """
        self.perm_path.parent.mkdir(parents=True, exist_ok=True)

        # 读取磁盘上的最新内容（可能包含其他管理器写入的键）
        data: Dict[str, Any] = {}
        if self.perm_path.exists():
            try:
                with open(self.perm_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}

        # 仅更新本管理器负责的键
        for key in DEFAULT_PERMISSIONS:
            if key in self.data:
                data[key] = self.data[key]

        with open(self.perm_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    def reload(self):
        """重新加载权限"""
        self._load()

    # ========== 授权玩家 (完整权限) ==========
    def get_authorized_players(self) -> List[str]:
        """获取授权玩家列表"""
        return list(self.data.get("authorized_players", []))

    def add_authorized_player(self, name: str) -> bool:
        """添加授权玩家"""
        name_lower = name.lower()
        players = self.data.setdefault("authorized_players", [])
        if name_lower not in players:
            players.append(name_lower)
            self._save()
            return True
        return False

    def remove_authorized_player(self, name: str) -> bool:
        """移除授权玩家"""
        name_lower = name.lower()
        players = self.data.get("authorized_players", [])
        if name_lower in players:
            players.remove(name_lower)
            self._save()
            return True
        return False

    # ========== 游戏管理员 ==========
    def get_game_admins(self) -> List[str]:
        """获取游戏管理员列表"""
        return list(self.data.get("game_admins", []))

    def add_game_admin(self, name: str) -> bool:
        """添加游戏管理员"""
        name_lower = name.lower()
        admins = self.data.setdefault("game_admins", [])
        if name_lower not in admins:
            admins.append(name_lower)
            self._save()
            return True
        return False

    def remove_game_admin(self, name: str) -> bool:
        """移除游戏管理员"""
        name_lower = name.lower()
        admins = self.data.get("game_admins", [])
        if name_lower in admins:
            admins.remove(name_lower)
            self._save()
            return True
        return False

    def is_game_admin(self, player_name: str, is_op: bool) -> bool:
        """检查是否为游戏管理员 (OP 自动是游戏管理员)"""
        if is_op:
            return True
        return player_name.lower() in self.data.get("game_admins", [])

    # ========== 权限检查 ==========
    def is_authorized(self, player_name: str, is_op: bool) -> bool:
        """检查玩家是否有完整权限 (OP / 授权玩家 / 游戏管理员)"""
        if is_op:
            return True
        name_lower = player_name.lower()
        if name_lower in self.data.get("authorized_players", []):
            return True
        if name_lower in self.data.get("game_admins", []):
            return True
        return False

    def has_copy_only_permission(self, player_name: str, is_op: bool,
                                  allow_normal_copy: bool) -> bool:
        """检查玩家是否有仅复制(保存)权限"""
        if self.is_authorized(player_name, is_op):
            return True
        if allow_normal_copy:
            return True
        return False
