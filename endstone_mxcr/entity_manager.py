"""
实体管理器模块 - MXCR v1.0.0

通过 Endstone 原生 API 与 SAPI 桥接双端互补，实现对生物实体的
完整数据保存与恢复。

双端分工:
  Endstone 原生 API（直接读写）:
    - actor.type          → 实体类型 ID
    - actor.location      → 位置（用于偏移计算）
    - actor.name_tag      ← 可读写
    - actor.score_tag     ← 可读写
    - actor.scoreboard_tags ← 可读写（add/remove）
    - actor.is_name_tag_visible       ← 可读写
    - actor.is_name_tag_always_visible ← 可读写
    - mob.health          ← 可读写
    - mob.max_health      ← 可读写
    - dimension.spawn_actor(location, type_id) → 生成实体

  SAPI 桥接（通过 scriptevent 补充）:
    - 装备（头盔/胸甲/护腿/靴子/主手/副手）
    - 药水效果
    - 颜色（羊/狗/猫等）
    - 实体属性（幼年/坐下等）

通信协议:
  Endstone → SAPI:
    /scriptevent mxcr:read_entities       <JSON>
    /scriptevent mxcr:apply_entity_data   <JSON>          (短消息，≤2000 字符)
    /scriptevent mxcr:apply_entity_data_chunk <JSON>      (分片写入)

  SAPI → Endstone (ScriptMessageEvent):
    message_id: "mxcr:entity_data"         (读取结果 - 短消息)
    message_id: "mxcr:entity_chunk"        (读取结果 - 分片)
    message_id: "mxcr:apply_entity_result" (写入结果)
"""

import json
import uuid
import threading
from typing import Callable, Dict, Optional, Any, List

from .structure_data import EntityInfo


# 不保存的实体类型（与 main.js 中的 EXCLUDED_ENTITY_TYPES 保持一致）
EXCLUDED_ENTITY_TYPES = {
    "minecraft:player",
    "minecraft:item",
    "minecraft:xp_orb",
    "minecraft:arrow",
    "minecraft:spectral_arrow",
    "minecraft:snowball",
    "minecraft:egg",
    "minecraft:ender_pearl",
    "minecraft:fireball",
    "minecraft:small_fireball",
    "minecraft:wither_skull",
    "minecraft:dragon_fireball",
    "minecraft:fireworks_rocket",
    "minecraft:thrown_trident",
    "minecraft:fishing_hook",
    "minecraft:llama_spit",
    "minecraft:shulker_bullet",
    "minecraft:area_effect_cloud",
    "minecraft:lightning_bolt",
    "minecraft:evocation_fang",
}


class EntityManager:
    """
    实体数据管理器。

    负责：
    1. 扫描选区内的实体，通过 Endstone API 读取基础属性
    2. 向 SAPI 发送读取请求，获取装备/药水/颜色/属性等扩展数据
    3. 合并两侧数据，构造完整的 EntityInfo 列表
    4. 粘贴时先用 Endstone API 生成实体并设置基础属性，再通过 SAPI 设置扩展数据
    5. 消息分片传输（突破 scriptevent 2048 字符限制）
    6. 超时清理挂起的请求
    """

    # 请求超时时间（tick，20 tick = 1 秒）
    TIMEOUT_TICKS = 200  # 10 秒

    # scriptevent 消息最大安全长度（与 ContainerManager 保持一致）
    MAX_PAYLOAD_LEN = 1800

    def __init__(self, plugin):
        """
        Args:
            plugin: MXCRPlugin 实例，用于访问 server、scheduler 等
        """
        self._plugin = plugin
        # 挂起的请求: req_id -> {"callback": fn, "ticks_left": int, "type": "read"|"write"}
        self._pending: Dict[str, Dict[str, Any]] = {}
        # 分片缓冲区: req_id -> {"parts": {part_index: str}, "total": int}
        self._chunk_buffer: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ================================================================
    # 分片传输工具方法（与 ContainerManager 相同的实现）
    # ================================================================

    def _split_payload(self, payload_str: str) -> List[str]:
        """将超长 payload 字符串拆分为多个片段"""
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

        如果 payload 长度 ≤ 2000（含命令前缀），直接发送完整消息。
        否则拆分为多个片段，每个片段通过单独的 scriptevent 发送。
        """
        full_cmd = f"scriptevent {event_id} {payload_str}"
        if len(full_cmd) <= 2000:
            self._plugin.server.dispatch_command(
                self._plugin.server.command_sender,
                full_cmd
            )
            return

        parts = self._split_payload(payload_str)
        total = len(parts)
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
        否则返回 None。
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

            if len(buf["parts"]) >= buf["total"]:
                full_str = ""
                for i in range(buf["total"]):
                    full_str += buf["parts"].get(i, "")
                del self._chunk_buffer[req_id]

                if req_id in self._pending:
                    self._pending[req_id]["ticks_left"] = max(
                        self._pending[req_id]["ticks_left"], 60
                    )

                try:
                    return json.loads(full_str)
                except Exception as e:
                    self._plugin.logger.warning(
                        f"[EntityManager] 分片重组后 JSON 解析失败: {e}"
                    )
                    return None

        return None

    # ================================================================
    # 公开 API
    # ================================================================

    def scan_entities_in_selection(
        self,
        dimension,
        min_pos: tuple,
        max_pos: tuple
    ) -> List["EntityInfo"]:
        """
        同步扫描选区内的实体，通过 Endstone 原生 API 读取基础属性。

        返回的 EntityInfo 列表中，sapi_data 字段为 None（需要后续通过
        request_read_sapi_data 异步补充）。

        Args:
            dimension: Endstone Dimension 对象
            min_pos: 选区最小坐标 (x, y, z)
            max_pos: 选区最大坐标 (x, y, z)

        Returns:
            EntityInfo 列表（仅含 Endstone 侧数据）
        """
        entity_list = []

        try:
            actors = dimension.actors
        except Exception as e:
            self._plugin.logger.warning(f"[EntityManager] 获取实体列表失败: {e}")
            return entity_list

        for actor in actors:
            try:
                # 过滤排除的实体类型
                if actor.type in EXCLUDED_ENTITY_TYPES:
                    continue

                # 检查是否在选区内
                loc = actor.location
                ax, ay, az = loc.x, loc.y, loc.z
                if not (min_pos[0] - 0.5 <= ax <= max_pos[0] + 0.5):
                    continue
                if not (min_pos[1] - 0.5 <= ay <= max_pos[1] + 1.5):
                    continue
                if not (min_pos[2] - 0.5 <= az <= max_pos[2] + 0.5):
                    continue

                # 读取 Endstone 侧基础属性
                entity_info = EntityInfo(
                    type_id=actor.type,
                    rel_x=ax - min_pos[0],
                    rel_y=ay - min_pos[1],
                    rel_z=az - min_pos[2],
                    yaw=loc.yaw if hasattr(loc, "yaw") else 0.0,
                    pitch=loc.pitch if hasattr(loc, "pitch") else 0.0,
                    name_tag=actor.name_tag or "",
                    score_tag=actor.score_tag or "",
                    scoreboard_tags=list(actor.scoreboard_tags) if actor.scoreboard_tags else [],
                    is_name_tag_visible=actor.is_name_tag_visible,
                    is_name_tag_always_visible=actor.is_name_tag_always_visible,
                    health=None,
                    max_health=None,
                    sapi_data=None,  # 待 SAPI 异步补充
                    runtime_id=str(actor.runtime_id),
                )

                # 尝试读取血量（Mob 类才有，Actor 基类没有）
                try:
                    entity_info.health = actor.health
                    entity_info.max_health = actor.max_health
                except AttributeError:
                    pass

                entity_list.append(entity_info)

            except Exception as e:
                self._plugin.logger.warning(
                    f"[EntityManager] 读取实体数据失败: {e}"
                )

        return entity_list

    def request_read_sapi_data(
        self,
        dim_name: str,
        min_pos: tuple,
        max_pos: tuple,
        callback: Callable[[Optional[dict], Optional[str]], None],
        req_id: Optional[str] = None
    ) -> str:
        """
        异步请求通过 SAPI 读取选区内所有实体的扩展数据。

        Args:
            dim_name: 维度名称
            min_pos: 选区最小坐标
            max_pos: 选区最大坐标
            callback: 回调函数，签名为 callback(entities_by_id: dict | None, error: str | None)
                      entities_by_id 是 {runtime_id: sapi_data_dict} 的字典
            req_id: 可选，自定义请求 ID

        Returns:
            req_id
        """
        if req_id is None:
            req_id = uuid.uuid4().hex[:8]

        payload = json.dumps({
            "dim": dim_name,
            "req_id": req_id,
            "min_x": int(min_pos[0]),
            "min_y": int(min_pos[1]),
            "min_z": int(min_pos[2]),
            "max_x": int(max_pos[0]),
            "max_y": int(max_pos[1]),
            "max_z": int(max_pos[2]),
        }, ensure_ascii=False)

        with self._lock:
            self._pending[req_id] = {
                "callback": callback,
                "ticks_left": self.TIMEOUT_TICKS,
                "type": "read"
            }

        try:
            self._plugin.server.dispatch_command(
                self._plugin.server.command_sender,
                f"scriptevent mxcr:read_entities {payload}"
            )
        except Exception as e:
            with self._lock:
                self._pending.pop(req_id, None)
            callback(None, f"dispatch_command 失败: {e}")

        return req_id

    def request_apply_sapi_data(
        self,
        dim_name: str,
        type_id: str,
        abs_x: float,
        abs_y: float,
        abs_z: float,
        sapi_data: dict,
        callback: Optional[Callable[[bool, Optional[str]], None]] = None,
        req_id: Optional[str] = None
    ) -> str:
        """
        异步请求通过 SAPI 向指定坐标附近的实体应用扩展数据（装备/药水/颜色/属性）。
        通过坐标+类型匹配实体，避免 Endstone runtime_id 与 SAPI entity.id 不一致的问题。

        Args:
            dim_name: 维度名称
            type_id: 实体类型 ID（如 "minecraft:sheep"）
            abs_x/abs_y/abs_z: 实体的绝对坐标（用于 SAPI 侧匹配查找）
            sapi_data: 序列化的 SAPI 侧实体数据
            callback: 可选回调，签名为 callback(ok: bool, error: str | None)
            req_id: 可选，自定义请求 ID

        Returns:
            req_id
        """
        if req_id is None:
            req_id = uuid.uuid4().hex[:8]

        payload = json.dumps({
            "dim": dim_name,
            "req_id": req_id,
            "type_id": type_id,
            "loc_x": abs_x,
            "loc_y": abs_y,
            "loc_z": abs_z,
            "data": sapi_data,
        }, ensure_ascii=False)

        with self._lock:
            self._pending[req_id] = {
                "callback": callback,
                "ticks_left": self.TIMEOUT_TICKS,
                "type": "write"
            }

        try:
            self._send_chunked("mxcr:apply_entity_data", req_id, payload)
        except Exception as e:
            with self._lock:
                self._pending.pop(req_id, None)
            if callback:
                callback(False, f"dispatch_command 失败: {e}")

        return req_id

    def on_script_message(self, message_id: str, message: str) -> bool:
        """
        处理来自 SAPI 的 ScriptMessageEvent。

        应在 MXCRPlugin 的 on_script_message 中调用此方法。

        支持的 message_id:
          - "mxcr:entity_data"          读取结果（完整消息）
          - "mxcr:entity_chunk"         读取结果（分片）
          - "mxcr:apply_entity_result"  写入结果

        Returns:
            True 表示已处理，False 表示不属于本管理器
        """
        handled_ids = (
            "mxcr:entity_data",
            "mxcr:entity_chunk",
            "mxcr:apply_entity_result",
        )
        if message_id not in handled_ids:
            return False

        # 分片消息处理
        if message_id == "mxcr:entity_chunk":
            try:
                chunk_data = json.loads(message)
            except Exception as e:
                self._plugin.logger.warning(
                    f"[EntityManager] entity_chunk JSON 解析失败: {e}"
                )
                return True

            full_data = self._receive_chunk("mxcr:entity_data", chunk_data)
            if full_data is not None:
                self._handle_complete_message("mxcr:entity_data", full_data)
            return True

        # 完整消息处理
        try:
            data = json.loads(message)
        except Exception as e:
            self._plugin.logger.warning(f"[EntityManager] JSON 解析失败: {e}")
            return True

        self._handle_complete_message(message_id, data)
        return True

    def _handle_complete_message(self, message_id: str, data: dict):
        """处理完整的消息数据"""
        req_id = data.get("req_id")
        if not req_id:
            return

        with self._lock:
            entry = self._pending.pop(req_id, None)

        if entry is None:
            return

        callback = entry.get("callback")
        req_type = entry.get("type")

        if req_type == "read" and message_id == "mxcr:entity_data":
            if callback:
                ok = data.get("ok", False)
                if ok:
                    # 将实体列表转换为位置匹配用的列表（每个元素含 loc_x/y/z 和 sapi_id）
                    entities_list = data.get("entities", [])
                    callback(entities_list, None)
                else:
                    callback(None, data.get("error", "未知错误"))

        elif req_type == "write" and message_id == "mxcr:apply_entity_result":
            if callback:
                ok = data.get("ok", False)
                error = data.get("error") if not ok else None
                callback(ok, error)

    def tick(self) -> None:
        """
        每 tick 调用一次，用于超时清理挂起的请求。
        应在插件的定时任务中调用（与 ContainerManager.tick 合并调用）。
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
                self._chunk_buffer.pop(req_id, None)

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
                        lambda cb=callback: cb(False, "请求超时（SAPI 未响应）"),
                        delay=0
                    )

    # ================================================================
    # 粘贴时的实体生成与属性恢复
    # ================================================================

    def spawn_and_restore_entity(
        self,
        dimension,
        entity_info: "EntityInfo",
        target_min_pos: tuple,
        dim_name: str,
        on_done: Optional[Callable[[bool, Optional[str]], None]] = None
    ):
        """
        在目标位置生成一个实体，并恢复其所有属性。

        流程：
        1. 用 Endstone API 生成实体
        2. 用 Endstone API 设置基础属性（name_tag、health 等）
        3. 延迟 1 tick 后，通过 SAPI 设置扩展属性（装备/药水/颜色/属性）

        Args:
            dimension: Endstone Dimension 对象
            entity_info: 要恢复的实体数据
            target_min_pos: 粘贴目标区域的最小坐标（用于计算绝对位置）
            dim_name: 维度名称字符串
            on_done: 可选回调，签名为 on_done(ok: bool, error: str | None)
        """
        from endstone.level import Location

        abs_x = entity_info.rel_x + target_min_pos[0]
        abs_y = entity_info.rel_y + target_min_pos[1]
        abs_z = entity_info.rel_z + target_min_pos[2]

        try:
            loc = Location(
                dimension,
                float(abs_x),
                float(abs_y),
                float(abs_z),
                float(entity_info.pitch),
                float(entity_info.yaw),
            )
            actor = dimension.spawn_actor(loc, entity_info.type_id)
        except Exception as e:
            if on_done:
                on_done(False, f"生成实体失败 ({entity_info.type_id}): {e}")
            return

        # 延迟 1 tick 后设置属性（确保实体已完全初始化）
        def apply_endstone_props():
            try:
                # 设置命名牌
                if entity_info.name_tag:
                    actor.name_tag = entity_info.name_tag
                if entity_info.score_tag:
                    actor.score_tag = entity_info.score_tag
                actor.is_name_tag_visible = entity_info.is_name_tag_visible
                actor.is_name_tag_always_visible = entity_info.is_name_tag_always_visible

                # 设置 scoreboard 标签
                for tag in entity_info.scoreboard_tags:
                    try:
                        actor.add_scoreboard_tag(tag)
                    except Exception:
                        pass

                # 设置血量（Mob 类才有）
                if entity_info.health is not None:
                    try:
                        if entity_info.max_health is not None:
                            actor.max_health = entity_info.max_health
                        actor.health = entity_info.health
                    except AttributeError:
                        pass

                # 设置朝向
                try:
                    actor.set_rotation(
                        float(entity_info.yaw),
                        float(entity_info.pitch)
                    )
                except Exception:
                    pass

            except Exception as e:
                self._plugin.logger.warning(
                    f"[EntityManager] 设置实体基础属性失败: {e}"
                )

            # 如果有 SAPI 扩展数据，再延迟 1 tick 通过 SAPI 应用
            if entity_info.sapi_data:
                def apply_sapi_props():
                    # 获取新生成实体的当前坐标（用于 SAPI 匹配）
                    try:
                        new_loc = actor.location
                        new_abs_x = new_loc.x
                        new_abs_y = new_loc.y
                        new_abs_z = new_loc.z
                    except Exception as e:
                        if on_done:
                            on_done(False, f"获取实体坐标失败: {e}")
                        return

                    self.request_apply_sapi_data(
                        dim_name=dim_name,
                        type_id=entity_info.type_id,
                        abs_x=new_abs_x,
                        abs_y=new_abs_y,
                        abs_z=new_abs_z,
                        sapi_data=entity_info.sapi_data,
                        callback=on_done,
                    )

                self._plugin.server.scheduler.run_task(
                    self._plugin, apply_sapi_props, delay=1
                )
            else:
                if on_done:
                    on_done(True, None)

        self._plugin.server.scheduler.run_task(
            self._plugin, apply_endstone_props, delay=1
        )
