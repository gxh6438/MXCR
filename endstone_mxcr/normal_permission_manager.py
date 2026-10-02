"""
普通玩家权限管理模块
基于 UUID 的权限系统,支持玩家改名
"""
import json
from pathlib import Path
from typing import Dict, Any, Optional
from uuid import UUID


# ========== 默认普通玩家权限 ==========
DEFAULT_NORMAL_PERMISSIONS = {
    "allow_copy": True,
    "allow_paste": False,
    "allow_delete_own": False,
    "allow_public_view": True,
    "allow_public_load": False,
    "allow_public_share": False,
    "allow_undo_redo": False,
    "allow_refresh_chunks": False,
    "allow_foot_block": False,
    "allow_move_mode": False,
    "max_structures": 10,
    "max_blocks_per_copy": 10000
}


class NormalPermissionManager:
    """普通玩家权限管理器"""

    def __init__(self, data_dir: Path, perm_file: Path):
        """
        Args:
            data_dir: 数据目录路径
            perm_file: permissions.json 文件路径
        """
        self.data_dir = data_dir
        self.perm_file = perm_file
        self.player_perm_dir = data_dir / "player_permissions"
        self.player_perm_dir.mkdir(parents=True, exist_ok=True)
        
        # 加载默认权限
        self._load_default_permissions()

    def _load_default_permissions(self):
        """从 permissions.json 加载默认普通玩家权限"""
        if self.perm_file.exists():
            try:
                with open(self.perm_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.default_permissions = data.get(
                        "default_normal_permissions",
                        DEFAULT_NORMAL_PERMISSIONS.copy()
                    )
            except Exception:
                self.default_permissions = DEFAULT_NORMAL_PERMISSIONS.copy()
        else:
            self.default_permissions = DEFAULT_NORMAL_PERMISSIONS.copy()

    def save_default_permissions(self, permissions: Dict[str, Any]):
        """保存默认普通玩家权限到 permissions.json"""
        if self.perm_file.exists():
            try:
                with open(self.perm_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        else:
            data = {}

        data["default_normal_permissions"] = permissions
        self.default_permissions = permissions

        self.perm_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.perm_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    def get_default_permissions(self) -> Dict[str, Any]:
        """获取默认普通玩家权限"""
        return dict(self.default_permissions)

    def get_player_permission_file(self, player_uuid: UUID) -> Path:
        """获取玩家权限文件路径"""
        return self.player_perm_dir / f"{str(player_uuid)}.json"

    def has_player_permission(self, player_uuid: UUID) -> bool:
        """检查玩家是否有单独的权限设置"""
        return self.get_player_permission_file(player_uuid).exists()

    def load_player_permission(self, player_uuid: UUID) -> Optional[Dict[str, Any]]:
        """加载玩家的单独权限设置"""
        perm_file = self.get_player_permission_file(player_uuid)
        if not perm_file.exists():
            return None

        try:
            with open(perm_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def save_player_permission(
        self,
        player_uuid: UUID,
        player_name: str,
        permissions: Dict[str, bool],
        limits: Dict[str, int]
    ):
        """保存玩家的单独权限设置"""
        import time

        data = {
            "player_uuid": str(player_uuid),
            "player_name": player_name,
            "last_updated": int(time.time()),
            "permissions": permissions,
            "limits": limits
        }

        perm_file = self.get_player_permission_file(player_uuid)
        with open(perm_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    def delete_player_permission(self, player_uuid: UUID) -> bool:
        """删除玩家的单独权限设置"""
        perm_file = self.get_player_permission_file(player_uuid)
        if perm_file.exists():
            perm_file.unlink()
            return True
        return False

    def get_effective_permissions(
        self,
        player_uuid: UUID
    ) -> Dict[str, Any]:
        """
        获取玩家的有效权限
        优先使用单独权限,否则使用默认权限
        """
        player_perm = self.load_player_permission(player_uuid)
        if player_perm:
            # 合并权限和限制
            result = {}
            result.update(player_perm.get("permissions", {}))
            result.update(player_perm.get("limits", {}))
            return result
        else:
            return dict(self.default_permissions)

    def check_permission(
        self,
        player_uuid: UUID,
        permission_key: str
    ) -> bool:
        """
        检查玩家是否有某个权限
        
        Args:
            player_uuid: 玩家 UUID
            permission_key: 权限键,如 "allow_copy"
        
        Returns:
            bool: 是否有该权限
        """
        perms = self.get_effective_permissions(player_uuid)
        return perms.get(permission_key, False)

    def get_limit(
        self,
        player_uuid: UUID,
        limit_key: str,
        default: int = 0
    ) -> int:
        """
        获取玩家的限制值
        
        Args:
            player_uuid: 玩家 UUID
            limit_key: 限制键,如 "max_structures"
            default: 默认值
        
        Returns:
            int: 限制值
        """
        perms = self.get_effective_permissions(player_uuid)
        return perms.get(limit_key, default)

    def list_all_player_permissions(self) -> list[Dict[str, Any]]:
        """列出所有有单独权限设置的玩家"""
        result = []
        for perm_file in self.player_perm_dir.glob("*.json"):
            try:
                with open(perm_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    result.append({
                        "uuid": data.get("player_uuid"),
                        "name": data.get("player_name"),
                        "last_updated": data.get("last_updated", 0)
                    })
            except Exception:
                continue
        
        # 按最后更新时间排序
        result.sort(key=lambda x: x["last_updated"], reverse=True)
        return result
