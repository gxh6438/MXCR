"""
StructBridge 后端封装

MXCR 2.0 起底层全部对接 StructBridge 插件的进程内 Python 模块 `structbridge`
（由 StructBridge C++ 插件在 onLoad 时注入 sys.modules）。

本模块是 MXCR 与 structbridge 之间唯一的接触面：
- 统一错误处理与可用性检测
- 统一结构键名生成（建筑 mxcr_<hex8> / 撤销快照 mxcrsnap_<hex12>）
- 锁定本插件使用的 API 子集

结构键名约定（契约见 structbridge.cpp）:
- 必须是"裸名"（不含 ':'），structbridge 会将其归入引擎 mystructure: 命名空间
- 单结构落盘 plugins/structbridge/library/mystructure/<key>.mcstructure
- 大区域结构束落盘 plugins/structbridge/library/mystructure/<key>.bundle/
- 键名仅允许 ASCII 字母/数字/下划线（hex + 前缀天然满足），跨平台文件名安全
"""
import uuid
from typing import Callable, Optional, Tuple

# StructBridge stage 码（structbridge.cpp enum Stage，0 表示成功）
STAGE_OK = 0

# MXCR 管理的键名前缀（GC 规则：只有 mxcrsnap_ 前缀的键可被撤销栈清理）
KEY_PREFIX_BUILDING = "mxcr_"
KEY_PREFIX_SNAPSHOT = "mxcrsnap_"


def new_building_key() -> Tuple[str, str]:
    """生成建筑键名与结构 ID。返回 (structure_id, sb_key)。"""
    structure_id = uuid.uuid4().hex[:8]
    return structure_id, KEY_PREFIX_BUILDING + structure_id


def new_snapshot_key() -> str:
    """生成撤销快照键名。"""
    return KEY_PREFIX_SNAPSHOT + uuid.uuid4().hex[:12]


def is_snapshot_key(key: str) -> bool:
    """判断键名是否为 MXCR 撤销快照（可被 GC 的唯一类别）。"""
    return key.startswith(KEY_PREFIX_SNAPSHOT)


def is_bundle_region(min_x: int, min_z: int, max_x: int, max_z: int) -> bool:
    """
    判断区域是否会生成结构束 (.bundle)。
    structbridge 的分片阈值为 64x64（引擎单模板上限），超过即生成 bundle。
    """
    return (max_x - min_x + 1) > 64 or (max_z - min_z + 1) > 64


class StructBridgeBackend:
    """StructBridge Python API 的薄封装。"""

    def __init__(self):
        self._mod = None
        self._load_error = ""

    # ---------- 可用性 ----------

    def ensure(self) -> Tuple[bool, str]:
        """
        确保 structbridge 模块可用。

        Returns:
            (ok, message): ok=False 时 message 为面向用户的安装提示。
        """
        if self._mod is not None:
            return True, ""
        try:
            import structbridge  # noqa: F401  进程内注入模块
        except ImportError as e:
            self._load_error = f"import structbridge 失败: {e}"
            return False, "未找到 StructBridge 插件的 Python API"
        try:
            import structbridge as mod
            if not mod.is_available():
                self._load_error = "structbridge 模块存在但 API 未就绪"
                return False, "StructBridge 插件未正常加载"
            self._mod = mod
            return True, ""
        except Exception as e:  # is_available 等基础调用失败
            self._load_error = f"structbridge API 异常: {e}"
            return False, "StructBridge Python API 异常"

    @property
    def available(self) -> bool:
        ok, _ = self.ensure()
        return ok

    @property
    def load_error(self) -> str:
        return self._load_error

    @property
    def version(self) -> str:
        if self._mod is None:
            return ""
        try:
            return str(self._mod.plugin_version())
        except Exception:
            return ""

    def status(self) -> dict:
        """整体状态（available/version/structures）。"""
        if self._mod is None:
            return {}
        try:
            return dict(self._mod.status())
        except Exception:
            return {}

    @staticmethod
    def stage_message(stage: int) -> str:
        """stage 码转可读名称（失败提示用）。"""
        try:
            import structbridge
            return str(structbridge.stage_message(stage))
        except Exception:
            return f"stage={stage}"

    # ---------- 核心操作 ----------

    def export_region(
        self,
        key: str,
        min_pos: tuple,
        max_pos: tuple,
        dimension: str,
        tp_player: Optional[str],
        on_done: Callable[[int, str], None],
    ) -> int:
        """
        导出区域为结构/结构束（引擎原生序列化，含容器物品与实体）。

        Args:
            key: 结构键名（new_building_key/new_snapshot_key 生成）
            min_pos/max_pos: 区域包围盒（含端点）
            dimension: "overworld" / "nether" / "the_end"
            tp_player: 允许被 structbridge 传送辅助加载区块的玩家名
                       （非主世界必填；MXCR 固定传操作发起者）
            on_done: 完成回调 on_done(stage, message)，服务器线程触发

        Returns:
            任务 ID；0 表示请求被直接拒绝（回调不会触发）
        """
        if self._mod is None:
            return 0
        return int(self._mod.export_region(
            key,
            int(min_pos[0]), int(min_pos[1]), int(min_pos[2]),
            int(max_pos[0]), int(max_pos[1]), int(max_pos[2]),
            dimension=dimension,
            include_entities=True,
            include_blocks=True,
            tp_player=tp_player,
            force_bundle=False,
            on_done=on_done,
        ))

    def place_structure(
        self,
        key: str,
        pos: tuple,
        dimension: str,
        tp_player: Optional[str],
        on_done: Callable[[int, str], None],
    ) -> int:
        """
        放置结构/结构束。pos 为新包围盒最小角（与 MXCR target_pos 语义一致）。

        Returns:
            任务 ID；0 表示请求被直接拒绝（回调不会触发）
        """
        if self._mod is None:
            return 0
        return int(self._mod.place_structure(
            key,
            int(pos[0]), int(pos[1]), int(pos[2]),
            original=False,
            dimension=dimension,
            include_entities=True,
            include_blocks=True,
            tp_player=tp_player,
            on_done=on_done,
        ))

    def delete_structure(self, key: str) -> Tuple[bool, str]:
        """
        删除结构：库文件 + 行为包镜像 + 引擎内存模板一并清理。

        Returns:
            (ok, message)
        """
        if self._mod is None:
            return False, "StructBridge 不可用"
        try:
            result = self._mod.delete_structure(key)
            return bool(result.get("ok")), str(result.get("message", ""))
        except Exception as e:
            return False, f"删除异常: {e}"

    def list_structures(self) -> list:
        """列出引擎可见的结构（list_structures 原样返回，管理诊断用）。"""
        if self._mod is None:
            return []
        try:
            return list(self._mod.list_structures())
        except Exception:
            return []
