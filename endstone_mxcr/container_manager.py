"""
容器管理器模块 - MXCR v1.0.0

通过 SAPI + /scriptevent 双向通信，实现对箱子、漏斗、熔炉等
容器方块的物品数据异步读写。

v1.0.0 变更:
  - 实现消息分片传输协议，突破 scriptevent 2048 字符限制
  - 移除末影箱（每个玩家独立，SAPI 无法读取）
  - 补全各色潜影盒、合成器等新版容器方块

通信协议:
  Endstone → SAPI:
    /scriptevent mxcr:read_container  <JSON>
    /scriptevent mxcr:write_container <JSON>          (短消息，≤2000 字符)
    /scriptevent mxcr:write_chunk     <JSON>          (分片写入)

  SAPI → Endstone (ScriptMessageEvent):
    message_id: "mxcr:container_data"   (读取结果 - 短消息)
    message_id: "mxcr:container_chunk"  (读取结果 - 分片)
    message_id: "mxcr:write_result"     (写入结果)

分片协议:
  当完整 JSON 超过 MAX_PAYLOAD_LEN 时，发送方将其拆分为多个片段：
  片段信封: {"req_id":"xxx","part":0,"total":3,"data":"...部分JSON..."}
  接收方收集所有片段后重组原始 JSON 再处理。

使用方式:
    # 保存时（在 copy_structure 循环中）
    mgr = ContainerManager(plugin)
    mgr.request_read(x, y, z, dim_name, callback)

    # 加载时（在 paste_structure 后）
    mgr.request_write(x, y, z, dim_name, slots, callback)
"""

import json
import math
import uuid
import threading
from typing import Callable, Dict, Optional, Any, List


class ContainerManager:
    """
    容器数据管理器。

    负责：
    1. 向 SAPI 发送读/写请求（通过 /scriptevent）
    2. 接收 ScriptMessageEvent 回调并分发结果
    3. 超时清理挂起的请求
    4. 消息分片传输（突破 scriptevent 2048 字符限制）
    """

    # 请求超时时间（tick，20 tick = 1 秒）
    TIMEOUT_TICKS = 200  # 10 秒（分片传输需要更多时间）

    # scriptevent 消息最大安全长度（留余量给命令前缀和信封开销）
    # scriptevent 限制 2048 字符，命令前缀 "scriptevent mxcr:xxx " 约 30-40 字符
    # 信封 JSON 开销 {"req_id":"xxxxxxxx","part":0,"total":99,"data":""} 约 60 字符
    # 安全 payload 长度 = 2048 - 40 - 60 = ~1948，取 1800 留足余量
    MAX_PAYLOAD_LEN = 1800

    def __init__(self, plugin):
        """
        Args:
            plugin: MXCRPlugin 实例，用于访问 server、scheduler 等
        """
        self._plugin = plugin
        # 挂起的请求: req_id -> {"callback": fn, "ticks_left": int, "type": "read"|"write"}
        self._pending: Dict[str, Dict[str, Any]] = {}
        # 分片缓冲区: req_id -> {"parts": {part_index: str}, "total": int, "type": str}
        self._chunk_buffer: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ================================================================
    # 分片传输工具方法
    # ================================================================

    def _split_payload(self, payload_str: str) -> List[str]:
        """
        将超长 payload 字符串拆分为多个片段。

        每个片段长度不超过 MAX_PAYLOAD_LEN 字符。

        Args:
            payload_str: 完整的 JSON 字符串

        Returns:
            片段列表
        """
        max_len = self.MAX_PAYLOAD_LEN
        if len(payload_str) <= max_len:
            return [payload_str]

        parts = []
        for i in range(0, len(payload_str), max_len):
            parts.append(payload_str[i:i + max_len])
        return parts

    def _send_chunked(self, event_id: str, req_id: str, payload_str: str):
        """
        分片发送超长 payload。

        如果 payload 长度 ≤ MAX_PAYLOAD_LEN，直接发送完整消息。
        否则拆分为多个片段，每个片段通过单独的 scriptevent 发送。

        Args:
            event_id: scriptevent 的事件 ID，如 "mxcr:write_container"
            req_id: 请求 ID
            payload_str: 完整的 JSON 字符串
        """
        # 计算完整命令长度："scriptevent {event_id} {payload_str}"
        full_cmd = f"scriptevent {event_id} {payload_str}"
        if len(full_cmd) <= 2000:
            # 短消息，直接发送
            self._plugin.server.dispatch_command(
                self._plugin.server.command_sender,
                full_cmd
            )
            return

        # 需要分片
        parts = self._split_payload(payload_str)
        total = len(parts)
        # 分片使用专用事件 ID：在原事件 ID 后加 "_chunk" 后缀
        chunk_event_id = event_id + "_chunk"

        for i, part_data in enumerate(parts):
            envelope = json.dumps({
                "req_id": req_id,
                "part": i,
                "total": total,
                "data": part_data
            }, ensure_ascii=False)
            cmd = f"scriptevent {chunk_event_id} {envelope}"
            self._plugin.server.dispatch_command(
                self._plugin.server.command_sender,
                cmd
            )

    def _receive_chunk(self, message_id: str, data: dict) -> Optional[dict]:
        """
        处理接收到的分片消息。

        如果所有片段已收齐，返回重组后的完整 JSON 对象。
        否则返回 None（表示还需要等待更多片段）。

        Args:
            message_id: 消息 ID（用于确定请求类型）
            data: 分片信封数据 {"req_id", "part", "total", "data"}

        Returns:
            完整的 JSON 对象，或 None
        """
        req_id = data.get("req_id")
        part = data.get("part", 0)
        total = data.get("total", 1)
        part_data = data.get("data", "")

        if not req_id:
            return None

        with self._lock:
            if req_id not in self._chunk_buffer:
                self._chunk_buffer[req_id] = {
                    "parts": {},
                    "total": total,
                    "message_id": message_id
                }

            buf = self._chunk_buffer[req_id]
            buf["parts"][part] = part_data

            # 检查是否所有片段已收齐
            if len(buf["parts"]) >= buf["total"]:
                # 按序号拼接
                full_str = ""
                for i in range(buf["total"]):
                    full_str += buf["parts"].get(i, "")
                del self._chunk_buffer[req_id]

                # 重新刷新该请求的超时计时器（分片传输期间可能已消耗了一些时间）
                if req_id in self._pending:
                    self._pending[req_id]["ticks_left"] = max(
                        self._pending[req_id]["ticks_left"], 60
                    )

                try:
                    return json.loads(full_str)
                except Exception as e:
                    self._plugin.logger.warning(
                        f"[ContainerManager] 分片重组后 JSON 解析失败: {e}"
                    )
                    return None

        return None

    # ================================================================
    # 公开 API
    # ================================================================

    def request_read(
        self,
        x: int, y: int, z: int,
        dim_name: str,
        callback: Callable[[Optional[list], Optional[str]], None],
        req_id: Optional[str] = None
    ) -> str:
        """
        异步请求读取容器物品数据。

        Args:
            x, y, z: 容器方块坐标
            dim_name: 维度名称，如 "minecraft:overworld"
            callback: 回调函数，签名为 callback(slots: list | None, error: str | None)
                      slots 为物品列表（可能为空列表），error 为错误信息
            req_id: 可选，自定义请求 ID；不传则自动生成

        Returns:
            req_id: 本次请求的唯一 ID
        """
        if req_id is None:
            req_id = uuid.uuid4().hex[:8]

        payload = json.dumps({
            "x": int(x),
            "y": int(y),
            "z": int(z),
            "dim": dim_name,
            "req_id": req_id
        }, ensure_ascii=False)

        with self._lock:
            self._pending[req_id] = {
                "callback": callback,
                "ticks_left": self.TIMEOUT_TICKS,
                "type": "read"
            }

        # 读取请求 payload 很小，不需要分片，直接发送
        try:
            self._plugin.server.dispatch_command(
                self._plugin.server.command_sender,
                f"scriptevent mxcr:read_container {payload}"
            )
        except Exception as e:
            with self._lock:
                self._pending.pop(req_id, None)
            callback(None, f"dispatch_command 失败: {e}")

        return req_id

    def request_ping(
        self,
        callback: Callable[[Optional[list], Optional[str]], None],
        timeout_ticks: int = 60,
        req_id: Optional[str] = None
    ) -> str:
        """
        发送 ping 检测 SAPI Bridge 连接状态 (v1.0.0 新增公共接口)。

        Args:
            callback: 回调函数，签名为 callback(slots, error)；
                      error 为 None 表示连接正常，包含"请求超时"表示未连接
            timeout_ticks: 超时 tick 数（默认 60 = 3 秒）
            req_id: 可选自定义请求 ID

        Returns:
            req_id: 本次请求的唯一 ID
        """
        if req_id is None:
            req_id = uuid.uuid4().hex[:8]

        payload = json.dumps({"req_id": req_id}, ensure_ascii=False)

        with self._lock:
            self._pending[req_id] = {
                "callback": callback,
                "ticks_left": timeout_ticks,
                "type": "read"  # 复用读取请求的回调/超时处理逻辑
            }

        try:
            self._plugin.server.dispatch_command(
                self._plugin.server.command_sender,
                f"scriptevent mxcr:ping {payload}"
            )
        except Exception as e:
            with self._lock:
                self._pending.pop(req_id, None)
            callback(None, f"dispatch_command 失败: {e}")

        return req_id

    def request_write(
        self,
        x: int, y: int, z: int,
        dim_name: str,
        slots: list,
        callback: Optional[Callable[[bool, int, Optional[str]], None]] = None,
        req_id: Optional[str] = None
    ) -> str:
        """
        异步请求写入容器物品数据。

        如果 payload 超过 scriptevent 长度限制，自动使用分片传输。

        Args:
            x, y, z: 容器方块坐标
            dim_name: 维度名称
            slots: 物品列表（由 request_read 回调返回的格式）
            callback: 可选回调，签名为 callback(ok: bool, count: int, error: str | None)
            req_id: 可选，自定义请求 ID

        Returns:
            req_id: 本次请求的唯一 ID
        """
        if req_id is None:
            req_id = uuid.uuid4().hex[:8]

        payload = json.dumps({
            "x": int(x),
            "y": int(y),
            "z": int(z),
            "dim": dim_name,
            "req_id": req_id,
            "slots": slots
        }, ensure_ascii=False)

        with self._lock:
            self._pending[req_id] = {
                "callback": callback,
                "ticks_left": self.TIMEOUT_TICKS,
                "type": "write"
            }

        try:
            self._send_chunked("mxcr:write_container", req_id, payload)
        except Exception as e:
            with self._lock:
                self._pending.pop(req_id, None)
            if callback:
                callback(False, 0, f"dispatch_command 失败: {e}")

        return req_id

    def request_clear(
        self,
        x: int, y: int, z: int,
        dim_name: str,
        callback: Optional[Callable[[bool, Optional[str]], None]] = None,
        req_id: Optional[str] = None
    ) -> str:
        """
        异步请求清空容器物品。

        用于撤销/恢复操作前先清空容器，避免覆盖方块时物品掉落。

        Args:
            x, y, z: 容器方块坐标
            dim_name: 维度名称
            callback: 可选回调，签名为 callback(ok: bool, error: str | None)
            req_id: 可选，自定义请求 ID

        Returns:
            req_id: 本次请求的唯一 ID
        """
        if req_id is None:
            req_id = uuid.uuid4().hex[:8]

        payload = json.dumps({
            "x": int(x),
            "y": int(y),
            "z": int(z),
            "dim": dim_name,
            "req_id": req_id
        }, ensure_ascii=False)

        with self._lock:
            self._pending[req_id] = {
                "callback": callback,
                "ticks_left": self.TIMEOUT_TICKS,
                "type": "clear"
            }

        try:
            self._plugin.server.dispatch_command(
                self._plugin.server.command_sender,
                f"scriptevent mxcr:clear_container {payload}"
            )
        except Exception as e:
            with self._lock:
                self._pending.pop(req_id, None)
            if callback:
                callback(False, f"dispatch_command 失败: {e}")

        return req_id

    def on_script_message(self, message_id: str, message: str) -> bool:
        """
        处理来自 SAPI 的 ScriptMessageEvent。

        应在 MXCRPlugin 的 @event_handler(ScriptMessageEvent) 中调用此方法。

        支持的 message_id:
          - "mxcr:container_data"    读取结果（完整消息）
          - "mxcr:container_chunk"   读取结果（分片）
          - "mxcr:write_result"      写入结果（完整消息）
          - "mxcr:clear_result"      清空结果（完整消息）

        Args:
            message_id: event.message_id
            message: event.message

        Returns:
            True 表示已处理，False 表示不属于本管理器
        """
        handled_ids = (
            "mxcr:container_data",
            "mxcr:container_chunk",
            "mxcr:write_result",
            "mxcr:clear_result",
        )
        if message_id not in handled_ids:
            return False

        # ----------------------------------------------------------
        # 分片消息处理
        # ----------------------------------------------------------
        if message_id == "mxcr:container_chunk":
            try:
                chunk_data = json.loads(message)
            except Exception as e:
                self._plugin.logger.warning(
                    f"[ContainerManager] 分片 JSON 解析失败: {e}"
                )
                return True

            # 尝试重组
            full_data = self._receive_chunk("mxcr:container_data", chunk_data)
            if full_data is not None:
                # 重组完成，按完整消息处理
                self._handle_complete_message("mxcr:container_data", full_data)
            return True

        # ----------------------------------------------------------
        # 完整消息处理
        # ----------------------------------------------------------
        try:
            data = json.loads(message)
        except Exception as e:
            self._plugin.logger.warning(f"[ContainerManager] JSON 解析失败: {e}")
            return True

        self._handle_complete_message(message_id, data)
        return True

    def _handle_complete_message(self, message_id: str, data: dict):
        """
        处理完整的消息数据（无论是直接接收还是分片重组后的）。

        Args:
            message_id: 消息 ID
            data: 完整的 JSON 数据
        """
        req_id = data.get("req_id")
        if not req_id:
            return

        with self._lock:
            entry = self._pending.pop(req_id, None)

        if entry is None:
            # 可能已超时被清理
            return

        callback = entry.get("callback")
        req_type = entry.get("type")

        if req_type == "read" and message_id == "mxcr:container_data":
            if callback:
                ok = data.get("ok", False)
                if ok:
                    callback(data.get("slots", []), None)
                else:
                    callback(None, data.get("error", "未知错误"))

        elif req_type == "write" and message_id == "mxcr:write_result":
            if callback:
                ok = data.get("ok", False)
                count = data.get("count", 0)
                error = data.get("error") if not ok else None
                callback(ok, count, error)

        elif req_type == "clear" and message_id == "mxcr:clear_result":
            if callback:
                ok = data.get("ok", False)
                error = data.get("error") if not ok else None
                callback(ok, error)

    def tick(self) -> None:
        """
        每 tick 调用一次，用于超时清理挂起的请求。
        应在插件的定时任务中调用（period=1）。
        """
        timed_out_entries = []

        with self._lock:
            timed_out = []
            for req_id, entry in self._pending.items():
                entry["ticks_left"] -= 1
                if entry["ticks_left"] <= 0:
                    timed_out.append(req_id)

            for req_id in timed_out:
                entry = self._pending.pop(req_id)
                timed_out_entries.append(entry)
                # 同时清理分片缓冲区
                self._chunk_buffer.pop(req_id, None)

        # 在锁外调用回调，避免死锁
        for entry in timed_out_entries:
            callback = entry.get("callback")
            req_type = entry.get("type")
            if callback:
                if req_type == "read":
                    self._plugin.server.scheduler.run_task(
                        self._plugin,
                        lambda cb=callback: cb(None, "请求超时（SAPI 未响应）"),
                        delay=0
                    )
                elif req_type == "write":
                    self._plugin.server.scheduler.run_task(
                        self._plugin,
                        lambda cb=callback: cb(False, 0, "请求超时（SAPI 未响应）"),
                        delay=0
                    )
                elif req_type == "clear":
                    # v1.0.0 修复：clear 类型超时此前未处理，回调永不触发，
                    # 导致撤销/恢复流程在 SAPI 无响应时永久挂起
                    self._plugin.server.scheduler.run_task(
                        self._plugin,
                        lambda cb=callback: cb(False, "请求超时（SAPI 未响应）"),
                        delay=0
                    )

    # ================================================================
    # 便捷方法：同步风格的批量容器扫描（用于 copy_structure 流程）
    # ================================================================

    @staticmethod
    def is_container_block(block_type: str) -> bool:
        """
        判断方块类型是否为容器方块（需要保存物品数据）。

        注意：末影箱（minecraft:ender_chest）的内容是每个玩家独立的，
        SAPI 的 block.getComponent("minecraft:inventory") 对末影箱返回 null，
        因此不纳入容器方块列表。

        Args:
            block_type: 方块 ID，如 "minecraft:chest"

        Returns:
            True 表示是容器方块
        """
        CONTAINER_BLOCKS = {
            # 箱子类
            "minecraft:chest",
            "minecraft:trapped_chest",
            # 桶
            "minecraft:barrel",
            # 漏斗
            "minecraft:hopper",
            # 投掷器 / 发射器
            "minecraft:dropper",
            "minecraft:dispenser",
            # 熔炉类
            "minecraft:furnace",
            "minecraft:lit_furnace",
            "minecraft:blast_furnace",
            "minecraft:lit_blast_furnace",
            "minecraft:smoker",
            "minecraft:lit_smoker",
            # 潜影盒（无色 + 16 色）
            "minecraft:shulker_box",
            "minecraft:undyed_shulker_box",
            "minecraft:white_shulker_box",
            "minecraft:orange_shulker_box",
            "minecraft:magenta_shulker_box",
            "minecraft:light_blue_shulker_box",
            "minecraft:yellow_shulker_box",
            "minecraft:lime_shulker_box",
            "minecraft:pink_shulker_box",
            "minecraft:gray_shulker_box",
            "minecraft:light_gray_shulker_box",
            "minecraft:cyan_shulker_box",
            "minecraft:purple_shulker_box",
            "minecraft:blue_shulker_box",
            "minecraft:brown_shulker_box",
            "minecraft:green_shulker_box",
            "minecraft:red_shulker_box",
            "minecraft:black_shulker_box",
            # 酿造台
            "minecraft:brewing_stand",
            # 讲台（书）
            "minecraft:lectern",
            # 营火（食物）
            "minecraft:campfire",
            "minecraft:soul_campfire",
            # 唱片机
            "minecraft:jukebox",
            # 装饰罐
            "minecraft:decorated_pot",
            # 合成器 (1.21+)
            "minecraft:crafter",
        }
        return block_type in CONTAINER_BLOCKS
