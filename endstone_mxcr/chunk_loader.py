"""
区块加载管理模块 - 智能分块渐进式保存/恢复
v1.0.0: 智能区块加载（优先处理已加载区块）、进度条修复、玩家离线容错
"""
import gc
import json
from endstone.boss import BarColor
from endstone.level import Location
from .undo_manager import BlockRecord
from .structure_data import BlockInfo
from .container_manager import ContainerManager
from .api_compat import block_type_str, norm_type


class ChunkLoader:
    """区块加载管理器"""

    def __init__(self, plugin):
        """
        Args:
            plugin: MXCRPlugin 实例
        """
        self.plugin = plugin
        self.server = plugin.server

    # ================================================================
    #  工具方法
    # ================================================================

    def _is_player_online(self, player_name: str) -> bool:
        """
        安全地检查玩家是否仍然在线

        Args:
            player_name: 玩家名称

        Returns:
            bool: 玩家是否在线
        """
        try:
            return self.server.get_player(player_name) is not None
        except Exception:
            return False

    def _safe_send_message(self, player_name: str, message: str):
        """
        安全地向玩家发送消息（玩家可能已离线）

        Args:
            player_name: 玩家名称
            message: 消息内容
        """
        try:
            p = self.server.get_player(player_name)
            if p is not None:
                p.send_message(message)
        except Exception:
            pass

    def _safe_teleport(self, player_name: str, location) -> bool:
        """
        安全地传送玩家（玩家可能已离线）

        Args:
            player_name: 玩家名称
            location: 目标位置

        Returns:
            bool: 是否成功传送
        """
        try:
            p = self.server.get_player(player_name)
            if p is not None:
                p.teleport(location)
                return True
        except Exception:
            pass
        return False

    def _safe_hide_progress(self, player_name: str):
        """
        安全地隐藏进度条（玩家可能已离线）

        Args:
            player_name: 玩家名称
        """
        try:
            progress_mgr = self.plugin.progress_manager
            if progress_mgr.has_progress_by_name(player_name):
                p = self.server.get_player(player_name)
                if p is not None:
                    progress_mgr.hide_progress(p)
                else:
                    progress_mgr.hide_progress_by_name(player_name)
        except Exception:
            pass

    def _safe_show_progress(self, player_name: str, title: str,
                            progress: float = 0.0, color=BarColor.YELLOW):
        """
        安全地显示进度条（玩家可能已离线）

        Args:
            player_name: 玩家名称
            title: 进度条标题
            progress: 进度值
            color: 进度条颜色
        """
        try:
            p = self.server.get_player(player_name)
            if p is not None:
                self.plugin.progress_manager.show_progress(p, title, progress, color)
        except Exception:
            pass

    def _safe_update_progress(self, player_name: str, progress: float = None,
                              title: str = None):
        """
        安全地更新进度条（玩家可能已离线）

        Args:
            player_name: 玩家名称
            progress: 新的进度值
            title: 新的标题
        """
        try:
            p = self.server.get_player(player_name)
            if p is not None:
                self.plugin.progress_manager.update_progress(p, progress, title)
        except Exception:
            pass

    # ================================================================
    #  区块计算方法
    # ================================================================

    def get_chunks_in_bounds(self, min_pos: tuple, max_pos: tuple) -> list:
        """
        计算选区覆盖的所有区块

        Args:
            min_pos: 最小坐标 (x, y, z)
            max_pos: 最大坐标 (x, y, z)

        Returns:
            区块坐标列表 [(chunk_x, chunk_z), ...]
        """
        min_chunk_x = min_pos[0] // 16
        max_chunk_x = max_pos[0] // 16
        min_chunk_z = min_pos[2] // 16
        max_chunk_z = max_pos[2] // 16

        chunks = []
        for cx in range(min_chunk_x, max_chunk_x + 1):
            for cz in range(min_chunk_z, max_chunk_z + 1):
                chunks.append((cx, cz))
        return chunks

    def get_chunk_center(self, chunk_x: int, chunk_z: int, y: int) -> tuple:
        """
        获取区块中心坐标

        Args:
            chunk_x: 区块X坐标
            chunk_z: 区块Z坐标
            y: Y坐标（高度）

        Returns:
            中心坐标 (x, y, z)
        """
        center_x = chunk_x * 16 + 8
        center_z = chunk_z * 16 + 8
        return (center_x, y, center_z)

    def get_blocks_in_chunk(self, chunk_x: int, chunk_z: int,
                           min_pos: tuple, max_pos: tuple) -> list:
        """
        获取指定区块内且在选区范围内的方块坐标

        Args:
            chunk_x: 区块X坐标
            chunk_z: 区块Z坐标
            min_pos: 选区最小坐标
            max_pos: 选区最大坐标

        Returns:
            方块坐标列表 [(x, y, z), ...]
        """
        chunk_min_x = chunk_x * 16
        chunk_max_x = chunk_x * 16 + 15
        chunk_min_z = chunk_z * 16
        chunk_max_z = chunk_z * 16 + 15

        scan_min_x = max(chunk_min_x, min_pos[0])
        scan_max_x = min(chunk_max_x, max_pos[0])
        scan_min_z = max(chunk_min_z, min_pos[2])
        scan_max_z = min(chunk_max_z, max_pos[2])

        blocks = []
        for x in range(scan_min_x, scan_max_x + 1):
            for y in range(min_pos[1], max_pos[1] + 1):
                for z in range(scan_min_z, scan_max_z + 1):
                    blocks.append((x, y, z))
        return blocks

    def _get_loaded_chunk_set(self, dimension) -> set:
        """
        获取维度中当前已加载的区块坐标集合

        Args:
            dimension: Dimension 实例

        Returns:
            set: 已加载的区块坐标集合 {(chunk_x, chunk_z), ...}
        """
        try:
            loaded = dimension.loaded_chunks
            return {(chunk.x, chunk.z) for chunk in loaded}
        except Exception:
            return set()

    def _classify_chunks(self, chunks: list, dimension) -> tuple:
        """
        将区块列表分为已加载和未加载两组

        Args:
            chunks: 区块坐标列表 [(chunk_x, chunk_z), ...]
            dimension: Dimension 实例

        Returns:
            (loaded_chunks, unloaded_chunks): 两个列表
        """
        loaded_set = self._get_loaded_chunk_set(dimension)
        loaded_chunks = []
        unloaded_chunks = []
        for chunk in chunks:
            if chunk in loaded_set:
                loaded_chunks.append(chunk)
            else:
                unloaded_chunks.append(chunk)
        return loaded_chunks, unloaded_chunks

    # ================================================================
    #  智能保存：先处理已加载区块，再传送处理未加载区块
    # ================================================================

    def save_with_chunk_loading(self, player, name: str, preserve_air: bool,
                                excluded_blocks: list, selection, relocate: bool = False,
                                reliable_mode: bool = False):
        """
        智能分块保存建筑
        1. 先扫描已加载的区块（无需传送，立即处理）
        2. 再逐个传送到未加载的区块进行处理
        3. 全程带进度条和容错处理

        Args:
            player: 玩家对象
            name: 建筑名称
            preserve_air: 是否保留空气
            excluded_blocks: 排除的方块列表
            selection: 选区对象
            relocate: 是否搬迁模式
            reliable_mode: 传统可靠模式（跳过已加载区块快速路径，全部走 TP 流程，等待 40 ticks）
        """
        player_uuid_str = str(player.unique_id)
        player_name = player.name
        dimension = player.dimension
        from .api_compat import dim_name as _dim_name_fn
        dim_name = _dim_name_fn(dimension)
        min_pos, max_pos = selection.get_bounds()

        # 计算所有区块
        all_chunks = self.get_chunks_in_bounds(min_pos, max_pos)
        total_chunks = len(all_chunks)

        # 智能分类：已加载 vs 未加载
        # 传统可靠模式下，跳过已加载区块快速路径，全部走 TP 流程
        if reliable_mode:
            loaded_chunks = []
            unloaded_chunks = all_chunks[:]
        else:
            loaded_chunks, unloaded_chunks = self._classify_chunks(all_chunks, dimension)

        mode_label = "§c传统可靠" if reliable_mode else "§a快速实验"
        player.send_message(
            f"§e[MXCR] 建筑跨越 §b{total_chunks} §e个区块"
            f"（§a{len(loaded_chunks)} §e已加载, §c{len(unloaded_chunks)} §e需传送加载）"
            f" [{mode_label}§e模式]"
        )

        # 保存玩家原始位置
        original_loc = player.location

        # v1.0.0: 流式写入器 —— 边扫描边写盘，方块数据不再全量驻留内存
        from .structure_data import StructureMetadata as _SM
        import time as _time
        _writer_metadata = _SM(
            structure_id=None, name=name,
            min_x=min_pos[0], min_y=min_pos[1], min_z=min_pos[2],
            max_x=max_pos[0], max_y=max_pos[1], max_z=max_pos[2],
            timestamp=int(_time.time()),
            player_uuid=player_uuid_str, player_name=player_name,
            dimension=dim_name,
        )
        try:
            structure_writer = self.plugin.db_manager.open_structure_writer(
                player_uuid_str, player_name, _writer_metadata
            )
        except Exception as e:
            player.send_message(f"§c[MXCR] 无法创建存档文件: {e}")
            return

        # 容器方块坐标列表（只存坐标，不存方块对象）
        container_coords = []
        processed_count = [0]  # 使用列表以便在闭包中修改
        scanned_block_count = [0]

        # 加载时发现已失效的已加载区块列表（需要重新加入 TP 队列）
        fallback_chunks = []

        # ----------------------------------------------------------
        # 第一阶段：逐区块异步处理已加载的区块（避免单 tick 扫描大量区块导致卡顿）
        # ----------------------------------------------------------
        def _scan_one_chunk_to_writer(cx, cz, dim_obj):
            """扫描一个区块并直接写入磁盘，仅临时占用单区块内存"""
            chunk_result = []
            self._scan_chunk_blocks(
                cx, cz, min_pos, max_pos, dim_obj,
                preserve_air, excluded_blocks, chunk_result
            )
            for b in chunk_result:
                if b.nbt_data == "__CONTAINER__":
                    container_coords.append((b.x, b.y, b.z))
                    b.nbt_data = None
            structure_writer.add_blocks(chunk_result)
            scanned_block_count[0] += len(chunk_result)
            chunk_result.clear()

        def _cleanup_save():
            """玩家离线时立即清理：删除半成品存档文件，释放内存"""
            container_coords.clear()
            fallback_chunks.clear()
            try:
                structure_writer.abort()
            except Exception:
                pass
            gc.collect()

        def phase1_process_loaded(loaded_index=0):
            if not self._is_player_online(player_name):
                self.plugin.logger.info(f"Player {player_name} offline, save cancelled (phase1)")
                _cleanup_save()
                self._safe_hide_progress(player_name)
                return

            # 所有已加载区块处理完成，进入第二阶段
            if loaded_index >= len(loaded_chunks):
                # 将失效的已加载区块加入 TP 队列头部
                all_unloaded = fallback_chunks + unloaded_chunks
                if all_unloaded:
                    self.server.scheduler.run_task(
                        self.plugin,
                        lambda: phase2_process_unloaded(0, all_unloaded),
                        delay=2
                    )
                else:
                    self.server.scheduler.run_task(
                        self.plugin,
                        finish_save,
                        delay=2
                    )
                return

            chunk_x, chunk_z = loaded_chunks[loaded_index]
            progress = (loaded_index / total_chunks) if total_chunks > 0 else 0

            if loaded_index == 0:
                if len(loaded_chunks) > 0:
                    self._safe_update_progress(
                        player_name, 0.05,
                        f"§e正在扫描已加载区块 (0/{len(loaded_chunks)})…"
                    )
                else:
                    self._safe_update_progress(
                        player_name, 0.05,
                        "§e正在准备传送加载区块..."
                    )

            # 扫描前重新验证该区块是否仍然已加载
            try:
                dim = self.server.level.get_dimension(dim_name)
                current_loaded_set = self._get_loaded_chunk_set(dim)
                chunk_still_loaded = (chunk_x, chunk_z) in current_loaded_set
            except Exception:
                chunk_still_loaded = False

            if not chunk_still_loaded:
                # 该区块已被卸载，加入 fallback 列表等待 TP 处理
                self.plugin.logger.info(
                    f"Chunk ({chunk_x},{chunk_z}) was unloaded during phase1, adding to TP queue"
                )
                fallback_chunks.append((chunk_x, chunk_z))
            else:
                # 区块仍然已加载，扫描方块
                self._safe_update_progress(
                    player_name, progress,
                    f"§e正在扫描已加载区块 ({loaded_index + 1}/{len(loaded_chunks)})..."
                )
                try:
                    _scan_one_chunk_to_writer(chunk_x, chunk_z, dim)
                    processed_count[0] += 1
                except Exception as e:
                    self.plugin.logger.error(
                        f"Failed to scan loaded chunk ({chunk_x},{chunk_z}): {e}"
                    )
                    fallback_chunks.append((chunk_x, chunk_z))

            # 异步处理下一个已加载区块（delay=1 避免单 tick 内处理过多区块）
            self.server.scheduler.run_task(
                self.plugin,
                lambda: phase1_process_loaded(loaded_index + 1),
                delay=1
            )

        # ----------------------------------------------------------
        # 第二阶段：传送处理未加载的区块（包括 phase1 中失效的 fallback 区块）
        # ----------------------------------------------------------
        def phase2_process_unloaded(unloaded_index, chunks_to_process=None):
            # chunks_to_process 由第一阶段完成后传入（fallback + unloaded 的合并）
            if chunks_to_process is None:
                chunks_to_process = unloaded_chunks

            if not self._is_player_online(player_name):
                self.plugin.logger.info(f"Player {player_name} offline, save cancelled (phase2)")
                _cleanup_save()
                self._safe_hide_progress(player_name)
                return

            if unloaded_index >= len(chunks_to_process):
                # 所有区块处理完毕
                self.server.scheduler.run_task(
                    self.plugin,
                    finish_save,
                    delay=2
                )
                return

            chunk_x, chunk_z = chunks_to_process[unloaded_index]
            total_tp = len(chunks_to_process)
            overall_index = len(loaded_chunks) + unloaded_index
            progress = (overall_index / (total_chunks + len(fallback_chunks))) if (total_chunks + len(fallback_chunks)) > 0 else 0
            progress = min(progress, 0.95)

            self._safe_update_progress(
                player_name, progress,
                f"§e正在传送加载区块 ({unloaded_index + 1}/{total_tp})..."
            )

            # 传送到区块中心
            center_y = (min_pos[1] + max_pos[1]) // 2
            center = self.get_chunk_center(chunk_x, chunk_z, center_y)

            try:
                dim = self.server.level.get_dimension(dim_name)
                loc = Location(dim, float(center[0]), float(center[1]), float(center[2]))
                self._safe_teleport(player_name, loc)
            except Exception as e:
                self.plugin.logger.warning(f"TP to chunk ({chunk_x}, {chunk_z}) failed: {e}")

            # 传送后延迟等待区块加载，然后再显示/更新进度条
            def after_tp_scan():
                if not self._is_player_online(player_name):
                    self.plugin.logger.info(f"Player {player_name} offline after TP")
                    _cleanup_save()
                    self._safe_hide_progress(player_name)
                    return

                # 传送完成后重新显示进度条（传送可能导致进度条被刷掉）
                self._safe_show_progress(
                    player_name,
                    f"§e正在扫描区块 ({unloaded_index + 1}/{total_tp})...",
                    progress, BarColor.YELLOW
                )

                try:
                    dim = self.server.level.get_dimension(dim_name)
                    _scan_one_chunk_to_writer(chunk_x, chunk_z, dim)
                    processed_count[0] += 1
                except Exception as e:
                    self.plugin.logger.error(f"Failed to scan chunk ({chunk_x}, {chunk_z}): {e}")

                # 处理下一个区块
                self.server.scheduler.run_task(
                    self.plugin,
                    lambda: phase2_process_unloaded(unloaded_index + 1, chunks_to_process),
                    delay=2
                )

            # 自动检测区块加载状态，动态决定等待时间
            self._wait_for_chunk(
                chunk_x, chunk_z, dim_name, after_tp_scan,
                player_name, reliable_mode,
                cleanup_fn=_cleanup_save
            )

        # ----------------------------------------------------------
        # 完成保存
        # ----------------------------------------------------------
        def finish_save():
            if not self._is_player_online(player_name):
                self.plugin.logger.info(f"Player {player_name} offline, save finalizing skipped")
                self._safe_hide_progress(player_name)
                return

            try:
                self._safe_update_progress(player_name, 0.9, "§e正在保存数据...")

                # TP 回原位置
                self._safe_teleport(player_name, original_loc)

                # v1.0.0: 方块数据已在扫描过程中写盘，这里只需处理容器物品补写
                # container_coords: 容器方块坐标列表 [(x, y, z), ...]
                container_updates = []  # [(nbt_json_or_None, x, y, z), ...]

                def do_save_to_db():
                    """完成保存：补写容器 NBT，更新元数据计数"""
                    try:
                        if container_updates:
                            structure_writer.update_container_nbt(container_updates)
                            container_updates.clear()
                        saved_block_count = structure_writer.block_count
                        structure_id = structure_writer.finalize()
                    except Exception as e:
                        try:
                            structure_writer.abort()
                        except Exception:
                            pass
                        self._safe_hide_progress(player_name)
                        self._safe_send_message(player_name, f"§c[MXCR] 保存失败: {e}")
                        self.plugin.logger.error(f"Failed to finalize save: {e}")
                        return
                    container_coords.clear()
                    gc.collect()

                    def show_final():
                        if not self._is_player_online(player_name):
                            self._safe_hide_progress(player_name)
                            return

                        self._safe_show_progress(player_name, "§a保存完成!", 1.0, BarColor.GREEN)
                        self._safe_send_message(player_name, f"§a[MXCR] §l建筑 §e{name} §a已保存!")
                        self._safe_send_message(
                            player_name,
                            f"§f共保存了 §e{saved_block_count} §f个方块 (ID: §b{structure_id}§f)"
                        )
                        self._safe_send_message(
                            player_name,
                            f"§f智能分块: §a{len(loaded_chunks)} §f已加载 + "
                            f"§e{len(unloaded_chunks)} §f需传送"
                        )

                        # 搬迁模式
                        if relocate:
                            handle_relocate()
                        else:
                            try:
                                self.plugin.selection_manager.clear_selection(player_name)
                            except Exception:
                                pass
                            self.server.scheduler.run_task(
                                self.plugin,
                                lambda: self._safe_hide_progress(player_name),
                                delay=40
                            )

                    self.server.scheduler.run_task(self.plugin, show_final, delay=20)

                if container_coords:
                    # 有容器方块，先异步读取物品数据再完成保存
                    self._safe_update_progress(
                        player_name, 0.9, f"§e正在读取 {len(container_coords)} 个容器物品..."
                    )
                    self._safe_send_message(
                        player_name,
                        f"§e[MXCR] 正在读取 {len(container_coords)} 个容器的物品数据..."
                    )
                    container_mgr = self.plugin.container_manager

                    # v1.0.0: 分批读取（每批 16 个），避免 scriptevent 洪泛丢包
                    coords_list = list(container_coords)
                    batch_size = 16

                    def read_batch(start_idx):
                        if start_idx >= len(coords_list):
                            do_save_to_db()
                            return
                        batch = coords_list[start_idx:start_idx + batch_size]
                        pending = [len(batch)]

                        def make_read_cb(cx_, cy_, cz_):
                            def cb(slots, error):
                                if error:
                                    self.plugin.logger.warning(
                                        f"[MXCR] 容器读取失败 ({cx_},{cy_},{cz_}): {error}"
                                    )
                                else:
                                    try:
                                        nbt_str = json.dumps(slots, ensure_ascii=False)
                                        container_updates.append((nbt_str, cx_, cy_, cz_))
                                    except Exception:
                                        pass
                                pending[0] -= 1
                                if pending[0] <= 0:
                                    self.server.scheduler.run_task(
                                        self.plugin,
                                        lambda: read_batch(start_idx + batch_size),
                                        delay=1
                                    )
                            return cb

                        for (cx_, cy_, cz_) in batch:
                            container_mgr.request_read(
                                x=cx_, y=cy_, z=cz_,
                                dim_name=dim_name,
                                callback=make_read_cb(cx_, cy_, cz_)
                            )

                    read_batch(0)
                else:
                    # 无容器方块，直接完成保存
                    do_save_to_db()

            except Exception as e:
                self._safe_hide_progress(player_name)
                self._safe_send_message(player_name, f"§c[MXCR] 保存失败: {str(e)}")
                self.plugin.logger.error(f"Failed to save structure: {e}")

        # ----------------------------------------------------------
        # 搬迁模式处理
        # ----------------------------------------------------------
        def handle_relocate():
            if not self._is_player_online(player_name):
                self._safe_hide_progress(player_name)
                return

            try:
                self._safe_update_progress(player_name, 0.95, "§e正在清除原方块...")
                self._safe_send_message(player_name, "§e[MXCR] 搬迁模式: 正在删除原方块...")

                dim = self.server.level.get_dimension(dim_name)

                # v1.0.0: 删除记录直接流式写入磁盘 undo 文件，不再全量驻留内存
                undo_mgr = self.plugin.undo_manager
                undo_writer = undo_mgr.open_action_writer(
                    player_name, "save_relocate", dim_name, f"搬迁建筑 {name}"
                )
                try:
                    for x in range(min_pos[0], max_pos[0] + 1):
                        for y in range(min_pos[1], max_pos[1] + 1):
                            for z in range(min_pos[2], max_pos[2] + 1):
                                try:
                                    block = dim.get_block_at(x, y, z)
                                    btype = block_type_str(block)
                                    if btype != "minecraft:air":
                                        bd = block.data
                                        states = dict(bd.block_states) if bd.block_states else {}
                                        undo_writer.add_block(BlockRecord(
                                            x=x, y=y, z=z,
                                            block_type=btype,
                                            block_data=0,
                                            block_states=states
                                        ))
                                except Exception:
                                    pass
                except Exception:
                    undo_writer.abort()
                    raise

                struct_op = self.plugin.structure_operator
                cleared = struct_op.clear_area(dim, min_pos, max_pos)
                self._safe_send_message(player_name, f"§a[MXCR] 已清除 §e{cleared} §a个方块")

                # 记录到撤销历史（commit 时入栈；无方块则自动丢弃）
                undo_writer.commit()
                gc.collect()

                # 清除选区
                try:
                    self.plugin.selection_manager.clear_selection(player_name)
                except Exception:
                    pass

                # 延迟隐藏进度条
                self.server.scheduler.run_task(
                    self.plugin,
                    lambda: self._safe_hide_progress(player_name),
                    delay=40
                )

            except Exception as e:
                self._safe_hide_progress(player_name)
                self._safe_send_message(player_name, f"§c[MXCR] 搬迁失败: {str(e)}")
                self.plugin.logger.error(f"Failed to relocate: {e}")

        # ----------------------------------------------------------
        # 启动第一阶段
        # ----------------------------------------------------------
        self.server.scheduler.run_task(
            self.plugin,
            phase1_process_loaded,
            delay=2
        )

    # ================================================================
    #  智能恢复：先处理已加载区块，再传送处理未加载区块
    # ================================================================

    def restore_blocks_with_chunk_loading(self, player, blocks: list,
                                          dimension_name: str, description: str,
                                          reliable_mode: bool = False,
                                          entities_to_restore: list = None,
                                          undo_action=None):
        """
        智能分块恢复方块（用于撤销/恢复操作）
        1. 先恢复已加载区块中的方块（无需传送）
        2. 再逐个传送到未加载区块进行恢复
        3. 全程带进度条和容错处理

        Args:
            player: 玩家对象
            blocks: 方块记录列表 [BlockRecord, ...]
            dimension_name: 维度名称
            description: 操作描述
            reliable_mode: 传统可靠模式（跳过已加载区块快速路径，全部走 TP 流程，等待 40 ticks）
            entities_to_restore: 需要恢复的实体列表
            undo_action: v1.0.0 新增 —— 若传入 UndoAction，则流式从磁盘按区块读取
                         方块数据（推荐，内存占用极低），blocks 参数可传 None
        """
        use_action = undo_action is not None
        if not use_action and not blocks:
            player.send_message("§c[MXCR] 没有可恢复的方块")
            return
        if use_action and undo_action.block_count <= 0:
            player.send_message("§c[MXCR] 没有可恢复的方块")
            return

        player_name = player.name
        dimension = player.dimension
        from .api_compat import dim_name as _dim_name_fn
        if _dim_name_fn(dimension) != dimension_name:
            player.send_message(f"§c[MXCR] 请切换到 {dimension_name} 维度")
            return

        # 计算方块覆盖的范围
        if use_action:
            min_pos, max_pos = undo_action.get_bounds()
            min_x, min_y, min_z = min_pos
            max_x, max_y, max_z = max_pos
        else:
            min_x = min(b.x for b in blocks)
            max_x = max(b.x for b in blocks)
            min_y = min(b.y for b in blocks)
            max_y = max(b.y for b in blocks)
            min_z = min(b.z for b in blocks)
            max_z = max(b.z for b in blocks)

            min_pos = (min_x, min_y, min_z)
            max_pos = (max_x, max_y, max_z)

        # 计算所有区块
        if use_action:
            # 只处理实际有方块记录的区块（磁盘索引查询）
            all_chunks = undo_action.get_chunk_list()
        else:
            all_chunks = self.get_chunks_in_bounds(min_pos, max_pos)
        total_chunks = len(all_chunks)

        # 智能分类：传统可靠模式下全部走 TP 流程
        if reliable_mode:
            loaded_chunks = []
            unloaded_chunks = all_chunks[:]
        else:
            loaded_chunks, unloaded_chunks = self._classify_chunks(all_chunks, dimension)

        mode_label = "§c传统可靠" if reliable_mode else "§a快速实验"
        player.send_message(
            f"§e[MXCR] {description} 跨越 §b{total_chunks} §e个区块"
            f"（§a{len(loaded_chunks)} §e已加载, §c{len(unloaded_chunks)} §e需传送加载）"
            f" [{mode_label}§e模式]"
        )

        # 保存玩家原始位置
        original_loc = player.location

        # 按区块分组方块（仅旧接口 list 模式；action 模式下按需从磁盘查询）
        chunk_blocks = {}
        if not use_action:
            for block in blocks:
                chunk_x = block.x // 16
                chunk_z = block.z // 16
                key = (chunk_x, chunk_z)
                if key not in chunk_blocks:
                    chunk_blocks[key] = []
                chunk_blocks[key].append(block)

        def _get_chunk_records(chunk_key):
            """获取一个区块的方块记录（action 模式下现查现用，用完即弃）"""
            if use_action:
                return undo_action.get_chunk_blocks(chunk_key[0], chunk_key[1])
            return chunk_blocks.pop(chunk_key, None) or []

        restored_count = [0]
        # 用于收集容器写入任务：[(x, y, z, nbt_data), ...]
        container_writes = []

        # ----------------------------------------------------------
        # 第一阶段：逐区块异步恢复已加载区块中的方块
        # v1.0.0 修复：原版在单个 tick 内同步循环恢复所有已加载区块，
        # 大型建筑会造成主线程长时间卡顿甚至卡服；现改为每 tick 一个区块
        # ----------------------------------------------------------
        def _cleanup_restore():
            """玩家离线时立即清空大型数据结构，释放内存"""
            chunk_blocks.clear()
            container_writes.clear()
            gc.collect()

        def phase1_restore_loaded(loaded_index=0):
            if not self._is_player_online(player_name):
                self.plugin.logger.info(f"Player {player_name} offline, restore cancelled (phase1)")
                _cleanup_restore()
                self._safe_hide_progress(player_name)
                return

            # 所有已加载区块处理完成，进入第二阶段
            if loaded_index >= len(loaded_chunks):
                if unloaded_chunks:
                    self.server.scheduler.run_task(
                        self.plugin,
                        lambda: phase2_restore_unloaded(0),
                        delay=2
                    )
                else:
                    self.server.scheduler.run_task(
                        self.plugin,
                        finish_restore,
                        delay=2
                    )
                return

            if loaded_index == 0:
                if len(loaded_chunks) > 0:
                    self._safe_update_progress(
                        player_name, 0.05,
                        f"§a正在恢复已加载区块 (0/{len(loaded_chunks)})…"
                    )
                else:
                    self._safe_update_progress(
                        player_name, 0.05,
                        f"§a正在准备传送加载区块..."
                    )

            chunk_x, chunk_z = loaded_chunks[loaded_index]
            progress = (loaded_index / total_chunks) if total_chunks > 0 else 0
            self._safe_update_progress(
                player_name, progress,
                f"§a正在恢复已加载区块 ({loaded_index + 1}/{len(loaded_chunks)})..."
            )

            try:
                records = _get_chunk_records((chunk_x, chunk_z))
                if records:
                    self._restore_chunk_blocks(
                        records, dimension, restored_count,
                        container_writes=container_writes
                    )
                    # 处理完后立即释放该区块的内存
                    records.clear()
            except Exception as e:
                self.plugin.logger.error(
                    f"Failed to restore chunk ({chunk_x},{chunk_z}): {e}"
                )

            # 异步处理下一个区块（delay=1 避免单 tick 内处理过多区块导致卡顿）
            self.server.scheduler.run_task(
                self.plugin,
                lambda: phase1_restore_loaded(loaded_index + 1),
                delay=1
            )

        # ----------------------------------------------------------
        # 第二阶段：传送恢复未加载区块中的方块
        # ----------------------------------------------------------
        def phase2_restore_unloaded(unloaded_index):
            if not self._is_player_online(player_name):
                _cleanup_restore()
                self._safe_hide_progress(player_name)
                return

            if unloaded_index >= len(unloaded_chunks):
                self.server.scheduler.run_task(
                    self.plugin,
                    finish_restore,
                    delay=2
                )
                return

            chunk_x, chunk_z = unloaded_chunks[unloaded_index]
            overall_index = len(loaded_chunks) + unloaded_index
            progress = (overall_index / total_chunks) if total_chunks > 0 else 0

            self._safe_update_progress(
                player_name, progress,
                f"§a正在传送恢复区块 ({unloaded_index + 1}/{len(unloaded_chunks)})..."
            )

            # 传送到区块中心
            center_y = (min_y + max_y) // 2
            center = self.get_chunk_center(chunk_x, chunk_z, center_y)

            try:
                dim = self.server.level.get_dimension(dimension_name)
                loc = Location(dim, float(center[0]), float(center[1]), float(center[2]))
                self._safe_teleport(player_name, loc)
            except Exception as e:
                self.plugin.logger.warning(f"TP to chunk ({chunk_x}, {chunk_z}) failed: {e}")

            # 传送后延迟恢复
            def after_tp_restore():
                if not self._is_player_online(player_name):
                    _cleanup_restore()
                    self._safe_hide_progress(player_name)
                    return

                # 传送后重新显示进度条
                self._safe_show_progress(
                    player_name,
                    f"§a正在恢复未加载区块 ({unloaded_index + 1}/{len(unloaded_chunks)})...",
                    progress, BarColor.GREEN
                )

                try:
                    dim = self.server.level.get_dimension(dimension_name)
                    records = _get_chunk_records((chunk_x, chunk_z))
                    if records:
                        self._restore_chunk_blocks(
                            records, dim, restored_count,
                            container_writes=container_writes
                        )
                        # 处理完后立即释放该区块的内存
                        records.clear()
                except Exception as e:
                    self.plugin.logger.error(f"Failed to restore chunk ({chunk_x}, {chunk_z}): {e}")

                self.server.scheduler.run_task(
                    self.plugin,
                    lambda: phase2_restore_unloaded(unloaded_index + 1),
                    delay=2
                )

            # 自动检测区块加载状态，动态决定等待时间
            self._wait_for_chunk(
                chunk_x, chunk_z, dimension_name, after_tp_restore,
                player_name, reliable_mode,
                cleanup_fn=_cleanup_restore
            )

        # ----------------------------------------------------------
        # 完成恢复
        # ----------------------------------------------------------
        def finish_restore():
            if not self._is_player_online(player_name):
                _cleanup_restore()
                self._safe_hide_progress(player_name)
                return

            try:
                final_restored = restored_count[0]
                chunk_blocks.clear()
                gc.collect()

                # TP 回原位置
                self._safe_teleport(player_name, original_loc)

                def do_finish_display():
                    if not self._is_player_online(player_name):
                        self._safe_hide_progress(player_name)
                        return

                    # 恢复实体（如果有）
                    entity_count = 0
                    if entities_to_restore:
                        try:
                            dim_obj = self.server.level.get_dimension(dimension_name)
                            entity_mgr = self.plugin.entity_manager
                            for ent_info in entities_to_restore:
                                try:
                                    entity_mgr.spawn_and_restore_entity(
                                        dim_obj, ent_info, min_pos, dimension_name
                                    )
                                    entity_count += 1
                                except Exception as e:
                                    self.plugin.logger.warning(
                                        f"[MXCR] 撤销恢复实体失败: {e}"
                                    )
                        except Exception as e:
                            self.plugin.logger.warning(
                                f"[MXCR] 撤销恢复实体失败: {e}"
                            )

                    self._safe_show_progress(player_name, "§a恢复完成!", 1.0, BarColor.GREEN)
                    self._safe_send_message(player_name, f"§a[MXCR] {description}完成!")
                    self._safe_send_message(
                        player_name,
                        f"§f共恢复了 §e{final_restored} §f个方块"
                        + (f"、§e{entity_count}§f 个实体" if entity_count else "")
                    )
                    self._safe_send_message(
                        player_name,
                        f"§f智能分块: §a{len(loaded_chunks)} §f已加载 + "
                        f"§e{len(unloaded_chunks)} §f需传送"
                    )
                    self.server.scheduler.run_task(
                        self.plugin,
                        lambda: self._safe_hide_progress(player_name),
                        delay=40
                    )
                    # v1.0.0: 恢复完成，释放已弹出 action 的磁盘文件
                    if use_action:
                        try:
                            undo_action.dispose()
                        except Exception:
                            pass
                    gc.collect()

                def show_final():
                    if not self._is_player_online(player_name):
                        self._safe_hide_progress(player_name)
                        return

                    if container_writes:
                        # 异步写入容器物品（v1.0.0: 分批节流，避免 scriptevent 洪泛）
                        self._safe_update_progress(
                            player_name, 0.95, "§a正在还原容器物品..."
                        )
                        self._safe_send_message(
                            player_name,
                            f"§e[MXCR] 正在还原 {len(container_writes)} 个容器的物品..."
                        )
                        self.batched_container_writes(
                            container_writes, dimension_name, do_finish_display,
                            fail_log_prefix="恢复容器失败"
                        )
                    else:
                        do_finish_display()

                self.server.scheduler.run_task(self.plugin, show_final, delay=20)

            except Exception as e:
                self._safe_hide_progress(player_name)
                self._safe_send_message(player_name, f"§c[MXCR] {description}失败: {str(e)}")
                self.plugin.logger.error(f"Failed to finish restore: {e}")

        # ----------------------------------------------------------
        # 启动第一阶段
        # ----------------------------------------------------------
        self.server.scheduler.run_task(
            self.plugin,
            lambda: phase1_restore_loaded(0),
            delay=2
        )

    # ================================================================
    #  内部辅助方法
    # ================================================================

    def batched_container_writes(self, writes: list, dim_name: str,
                                 on_all_done, batch_size: int = 16,
                                 fail_log_prefix: str = "容器写入失败"):
        """
        分批发送容器写入请求 (v1.0.0 新增)。

        每批发送 batch_size 个请求，全部回调后再发下一批，
        避免一次性向 SAPI 发送数百个 scriptevent 导致丢包/卡顿。

        Args:
            writes: [(x, y, z, nbt_data), ...]
            dim_name: 维度名称
            on_all_done: 全部完成后的回调（无参数）
            batch_size: 每批并发请求数
            fail_log_prefix: 失败日志前缀
        """
        container_mgr = self.plugin.container_manager
        writes_list = list(writes)
        # 释放调用方列表（内容已拷贝）
        try:
            writes.clear()
        except Exception:
            pass

        def send_batch(start_idx):
            if start_idx >= len(writes_list):
                on_all_done()
                return
            batch = writes_list[start_idx:start_idx + batch_size]
            pending = [len(batch)]

            def one_done():
                pending[0] -= 1
                if pending[0] <= 0:
                    self.server.scheduler.run_task(
                        self.plugin,
                        lambda: send_batch(start_idx + batch_size),
                        delay=1
                    )

            def make_write_cb(wx, wy, wz):
                def cb(ok, count, error):
                    if not ok:
                        self.plugin.logger.warning(
                            f"[MXCR] {fail_log_prefix} ({wx},{wy},{wz}): {error}"
                        )
                    one_done()
                return cb

            for (wx, wy, wz, nbt_data) in batch:
                try:
                    slots = json.loads(nbt_data)
                except Exception:
                    one_done()
                    continue
                container_mgr.request_write(
                    x=wx, y=wy, z=wz,
                    dim_name=dim_name,
                    slots=slots,
                    callback=make_write_cb(wx, wy, wz)
                )

        send_batch(0)

    def _scan_chunk_blocks(self, chunk_x: int, chunk_z: int,
                           min_pos: tuple, max_pos: tuple,
                           dimension, preserve_air: bool,
                           excluded_blocks: list, result_list: list):
        """
        扫描指定区块内的方块并追加到结果列表

        Args:
            chunk_x: 区块X坐标
            chunk_z: 区块Z坐标
            min_pos: 选区最小坐标
            max_pos: 选区最大坐标
            dimension: Dimension 实例
            preserve_air: 是否保留空气
            excluded_blocks: 排除的方块列表
            result_list: 结果列表（会被修改）
        """
        block_positions = self.get_blocks_in_chunk(chunk_x, chunk_z, min_pos, max_pos)

        for pos in block_positions:
            x, y, z = pos
            try:
                block = dimension.get_block_at(x, y, z)
                block_type = block_type_str(block)

                if not preserve_air and block_type == "minecraft:air":
                    continue
                if block_type in excluded_blocks:
                    continue

                block_data = block.data
                states = dict(block_data.block_states) if block_data.block_states else {}

                # 标记容器方块，保存时需要异步读取物品数据
                from .container_manager import ContainerManager
                nbt_data = "__CONTAINER__" if ContainerManager.is_container_block(block_type) else None

                result_list.append(BlockInfo(
                    x=x, y=y, z=z,
                    block_type=block_type,
                    block_states=states,
                    nbt_data=nbt_data
                ))
            except Exception:
                pass

    def _restore_chunk_blocks(self, block_records: list, dimension,
                              restored_count: list,
                              container_writes: list = None):
        """
        恢复一组方块记录

        Args:
            block_records: 方块记录列表 [BlockRecord, ...]
            dimension: Dimension 实例
            restored_count: 恢复计数器 [int]（会被修改）
            container_writes: 若传入列表，则收集容器写入任务 [(x, y, z, nbt_data), ...]
        """
        for block_rec in block_records:
            try:
                block = dimension.get_block_at(block_rec.x, block_rec.y, block_rec.z)

                # 如果当前位置是容器方块，先替换为屏障（不会掉落物品）再放置目标方块
                # 这样可以避免直接覆盖容器时物品掉落
                try:
                    current_type = block.type
                    if ContainerManager.is_container_block(current_type):
                        block.set_type("minecraft:barrier", False)
                except Exception:
                    pass

                if block_rec.block_states and self.server:
                    try:
                        block_data = self.server.create_block_data(
                            block_rec.block_type, block_rec.block_states
                        )
                        block.set_data(block_data, False)
                    except Exception:
                        block.set_type(block_rec.block_type, False)
                else:
                    block.set_type(block_rec.block_type, False)
                restored_count[0] += 1
                # 如果该方块有容器物品数据，加入写入队列
                if container_writes is not None and block_rec.nbt_data:
                    container_writes.append((block_rec.x, block_rec.y, block_rec.z, block_rec.nbt_data))
            except Exception:
                pass

    def _wait_for_chunk(self, chunk_x: int, chunk_z: int, dim_name: str,
                         callback, player_name: str,
                         reliable_mode: bool = False,
                         waited_ticks: int = 0,
                         cleanup_fn=None):
        """
        轮询等待指定区块加载完成，然后执行回调。

        策略：
        - 最短等待：可靠模式 5 ticks，快速模式 3 ticks
        - 轮询间隔：可靠模式 3 ticks，快速模式 2 ticks
        - 最大等待：可靠模式 = 配置的超时秒数×20 ticks，快速模式 = 超时秒数×10 ticks
        - 超时后强制执行回调（不会卡死）
        - 玩家离线时调用 cleanup_fn 立即释放内存

        Args:
            chunk_x: 目标区块 X 坐标
            chunk_z: 目标区块 Z 坐标
            dim_name: 维度名称
            callback: 区块加载完成后执行的回调函数（无参数）
            player_name: 玩家名（用于离线检测）
            reliable_mode: 是否传统可靠模式
            waited_ticks: 已等待的 ticks 数（内部递归使用）
            cleanup_fn: 玩家离线时调用的清理函数（用于释放大型数据结构）
        """
        min_wait = 5 if reliable_mode else 3
        poll_interval = 3 if reliable_mode else 2
        # 从配置读取超时秒数，转换为 ticks（20 ticks/秒）
        try:
            timeout_secs = self.plugin.config_manager.chunk_wait_timeout_seconds
        except Exception:
            timeout_secs = 60
        max_wait = timeout_secs * 20 if reliable_mode else timeout_secs * 10

        def do_check():
            if not self._is_player_online(player_name):
                # 玩家离线，调用清理函数释放内存
                if cleanup_fn is not None:
                    try:
                        cleanup_fn()
                    except Exception:
                        pass
                self._safe_hide_progress(player_name)
                return

            # 已达到最短等待时间后，开始检测
            if waited_ticks >= min_wait:
                try:
                    dim = self.server.level.get_dimension(dim_name)
                    loaded_set = self._get_loaded_chunk_set(dim)
                    if (chunk_x, chunk_z) in loaded_set:
                        # 区块已加载，立即执行回调
                        callback()
                        return
                except Exception:
                    pass

                # 超时则强制执行
                if waited_ticks >= max_wait:
                    self.plugin.logger.warning(
                        f"Chunk ({chunk_x},{chunk_z}) load timeout after {waited_ticks} ticks, forcing callback"
                    )
                    callback()
                    return

            # 继续等待
            self.server.scheduler.run_task(
                self.plugin,
                lambda: self._wait_for_chunk(
                    chunk_x, chunk_z, dim_name, callback,
                    player_name, reliable_mode, waited_ticks + poll_interval,
                    cleanup_fn
                ),
                delay=poll_interval
            )

        # 初始延迟（最短等待的第一步）
        self.server.scheduler.run_task(self.plugin, do_check, delay=poll_interval)

    # ================================================================
    #  智能加载：先处理已加载区块，再传送处理未加载区块
    #  与 save_with_chunk_loading 对称，解决大型建筑只 TP 一次的问题
    # ================================================================

    def load_with_chunk_loading(self, player, structure_id: str,
                                target_pos: tuple,
                                source_type: str = "player",
                                source_dir: str = "",
                                reliable_mode: bool = False):
        """
        智能分块加载建筑
        1. 先从数据库读取方块数据，按区块分组后立即释放原始列表（节省内存）
        2. 先处理已加载区块（无需传送）
        3. 再逐个传送到未加载区块放置方块
        4. 每个区块处理完后立即释放该区块数据（避免内存堆积）
        5. 全程带进度条和容错处理

        Args:
            player: 玩家对象
            structure_id: 建筑 ID
            target_pos: 目标位置 (x, y, z)
            source_type: "player" / "public" / "admin"
            source_dir: 管理员模式下的源文件夹名
            reliable_mode: 传统可靠模式（跳过已加载区块快速路径，全部走 TP 流程，等待 40 ticks）
        """
        player_name = player.name
        player_uuid_str = str(player.unique_id)
        dim_name = player.dimension.name

        # ----------------------------------------------------------
        # 第0步：读取元数据和方块数据，按区块分组
        # ----------------------------------------------------------
        def do_prepare():
            p = self.server.get_player(player_name)
            if p is None:
                self._safe_hide_progress(player_name)
                return

            try:
                self._safe_update_progress(player_name, 0.1, "§a正在读取建筑数据...")

                # v1.0.0: 不再一次性读取全部方块，改为按区块流式查询（内存占用极低）
                db_mgr = self.plugin.db_manager
                if source_type == "public":
                    metadata = db_mgr.get_public_structure_metadata(structure_id)
                    entities_raw = db_mgr.get_public_structure_entities(structure_id)
                elif source_type == "admin" and source_dir:
                    metadata = db_mgr.get_metadata_from_dir(source_dir, structure_id)
                    entities_raw = db_mgr.get_entities_from_dir(source_dir, structure_id)
                else:
                    metadata = db_mgr.get_structure_metadata(
                        player_uuid_str, structure_id, player_name
                    )
                    entities_raw = db_mgr.get_structure_entities(
                        player_uuid_str, structure_id, player_name
                    )

                db_path = db_mgr.resolve_db_path(
                    structure_id, source_type=source_type,
                    player_uuid=player_uuid_str, player_name=player_name,
                    source_dir=source_dir
                )

                if metadata is None or db_path is None:
                    self._safe_hide_progress(player_name)
                    self._safe_send_message(player_name, "§c[MXCR] 建筑数据不存在!")
                    return

                # 确保旧版数据库也有坐标索引（按区块查询提速）
                db_mgr.ensure_xz_index(db_path)

                # 计算偏移量
                offset_x = target_pos[0] - metadata.min_x
                offset_y = target_pos[1] - metadata.min_y
                offset_z = target_pos[2] - metadata.min_z

                # 计算目标区域范围
                target_min = target_pos
                target_max = (
                    metadata.max_x + offset_x,
                    metadata.max_y + offset_y,
                    metadata.max_z + offset_z
                )

                # 计算所有区块
                all_chunks = self.get_chunks_in_bounds(target_min, target_max)
                total_chunks = len(all_chunks)

                self._safe_update_progress(player_name, 0.15, "§a正在分析区块...")

                # v1.0.0: 按区块现查现用 —— 不再全量物化 chunk_blocks 字典
                # 每个目标区块处理时才从磁盘查询对应源坐标范围的方块，
                # 用完立即释放，内存峰值 = 单个区块的方块数据
                def fetch_chunk_tuples(cx, cz):
                    """查询目标区块 (cx, cz) 对应的方块，返回偏移后的坐标元组列表"""
                    # 目标区块的世界坐标范围
                    tx1, tx2 = cx << 4, (cx << 4) + 15
                    tz1, tz2 = cz << 4, (cz << 4) + 15
                    # 反推源坐标范围
                    sx1, sx2 = tx1 - offset_x, tx2 - offset_x
                    sz1, sz2 = tz1 - offset_z, tz2 - offset_z
                    try:
                        src_blocks = db_mgr.get_blocks_in_range(db_path, sx1, sx2, sz1, sz2)
                    except Exception as fe:
                        self.plugin.logger.error(f"[MXCR] 区块数据查询失败 ({cx},{cz}): {fe}")
                        return []
                    result = [
                        (b.x + offset_x, b.y + offset_y, b.z + offset_z,
                         b.block_type, b.block_states, b.nbt_data)
                        for b in src_blocks
                    ]
                    src_blocks.clear()
                    return result

                # 智能分类：传统可靠模式下全部走 TP 流程
                if reliable_mode:
                    loaded_chunks = []
                    unloaded_chunks = all_chunks[:]
                else:
                    try:
                        dim = self.server.level.get_dimension(dim_name)
                        loaded_chunks, unloaded_chunks = self._classify_chunks(all_chunks, dim)
                    except Exception:
                        loaded_chunks = []
                        unloaded_chunks = all_chunks[:]

                mode_label = "§c传统可靠" if reliable_mode else "§a快速实验"
                self._safe_send_message(
                    player_name,
                    f"§e[MXCR] 建筑跨越 §b{total_chunks} §e个区块"
                    f"（§a{len(loaded_chunks)} §e已加载, §c{len(unloaded_chunks)} §e需传送加载）"
                    f" [{mode_label}§e模式]"
                )

                # 保存玩家原始位置
                try:
                    p2 = self.server.get_player(player_name)
                    original_loc = p2.location if p2 else None
                except Exception:
                    original_loc = None

                placed_count = [0]
                struct_name = metadata.name if hasattr(metadata, 'name') else structure_id
                # v1.0.0: 被替换的原始方块直接流式写入磁盘 undo 文件
                # 内存中只保留需要异步读取容器物品的少量记录
                undo_writer = self.plugin.undo_manager.open_action_writer(
                    player_name, "load", dim_name, f"加载建筑 {struct_name}"
                )
                # 需要异步读取原容器物品的记录（数量少，留内存，读完后再写入 undo）
                pending_container_records = []
                # 加载时发现已失效的已加载区块列表（需要重新加入 TP 队列）
                load_fallback_chunks = []
                # 用于收集需要写入容器物品的方块：[(nx, ny, nz, nbt_data), ...]
                container_writes = []

                def _sink_original_record(rec):
                    """原始方块记录落盘；容器记录暂留内存等待异步读取物品"""
                    if rec.nbt_data == "__NEED_READ__":
                        pending_container_records.append(rec)
                    else:
                        undo_writer.add_block(rec)

                # ----------------------------------------------------------
                # 第一阶段：逐区块异步放置已加载区块中的方块
                # ----------------------------------------------------------
                def _cleanup_load():
                    """玩家离线时立即清空大型数据结构，释放内存"""
                    load_fallback_chunks.clear()
                    container_writes.clear()
                    pending_container_records.clear()
                    try:
                        # 玩家离线也要保留已放置部分的撤销记录，避免无法回退
                        undo_writer.commit()
                    except Exception:
                        pass
                    gc.collect()

                def phase1_place_loaded(loaded_index=0):
                    if not self._is_player_online(player_name):
                        _cleanup_load()
                        self._safe_hide_progress(player_name)
                        return

                    # 所有已加载区块处理完成，进入第二阶段
                    if loaded_index >= len(loaded_chunks):
                        all_unloaded = load_fallback_chunks + unloaded_chunks
                        if all_unloaded:
                            self.server.scheduler.run_task(
                                self.plugin,
                                lambda: phase2_place_unloaded(0, all_unloaded),
                                delay=2
                            )
                        else:
                            self.server.scheduler.run_task(
                                self.plugin,
                                finish_load,
                                delay=2
                            )
                        return

                    chunk_x, chunk_z = loaded_chunks[loaded_index]
                    progress = 0.2 + (loaded_index / total_chunks) * 0.7 if total_chunks > 0 else 0.2

                    if loaded_index == 0:
                        if len(loaded_chunks) > 0:
                            self._safe_update_progress(
                                player_name, 0.2,
                                f"§a正在放置已加载区块 (0/{len(loaded_chunks)})..."
                            )
                        else:
                            self._safe_update_progress(
                                player_name, 0.2,
                                "§a正在准备传送加载区块..."
                            )

                    # 放置前重新验证该区块是否仍然已加载
                    try:
                        dim2 = self.server.level.get_dimension(dim_name)
                        current_loaded_set = self._get_loaded_chunk_set(dim2)
                        chunk_still_loaded = (chunk_x, chunk_z) in current_loaded_set
                    except Exception:
                        chunk_still_loaded = False

                    chunk_key = (chunk_x, chunk_z)
                    if not chunk_still_loaded:
                        # 区块已被卸载，加入 fallback 列表等待 TP 处理
                        self.plugin.logger.info(
                            f"Chunk ({chunk_x},{chunk_z}) was unloaded during phase1 load, adding to TP queue"
                        )
                        load_fallback_chunks.append((chunk_x, chunk_z))
                    else:
                        # 区块仍然已加载，放置方块
                        self._safe_update_progress(
                            player_name, progress,
                            f"§a正在放置已加载区块 ({loaded_index + 1}/{len(loaded_chunks)})..."
                        )
                        try:
                            tuples = fetch_chunk_tuples(chunk_x, chunk_z)
                            if tuples:
                                self._place_chunk_blocks(
                                    tuples, dim2, placed_count,
                                    original_sink=_sink_original_record,
                                    container_writes=container_writes
                                )
                                # 放置完后立即释放该区块数据，节省内存
                                tuples.clear()
                        except Exception as e:
                            self.plugin.logger.error(
                                f"Failed to place chunk ({chunk_x},{chunk_z}): {e}"
                            )
                            load_fallback_chunks.append((chunk_x, chunk_z))

                    # 异步处理下一个已加载区块
                    self.server.scheduler.run_task(
                        self.plugin,
                        lambda: phase1_place_loaded(loaded_index + 1),
                        delay=1
                    )

                # ----------------------------------------------------------
                # 第二阶段：传送放置未加载区块中的方块（包括 phase1 中失效的 fallback 区块）
                # ----------------------------------------------------------
                def phase2_place_unloaded(unloaded_index, chunks_to_process=None):
                    if chunks_to_process is None:
                        chunks_to_process = unloaded_chunks

                    if not self._is_player_online(player_name):
                        _cleanup_load()
                        self._safe_hide_progress(player_name)
                        return

                    if unloaded_index >= len(chunks_to_process):
                        self.server.scheduler.run_task(
                            self.plugin,
                            finish_load,
                            delay=2
                        )
                        return

                    chunk_x, chunk_z = chunks_to_process[unloaded_index]
                    total_tp = len(chunks_to_process)
                    overall_index = len(loaded_chunks) + unloaded_index
                    total_all = total_chunks + len(load_fallback_chunks)
                    progress = 0.2 + (overall_index / total_all) * 0.7 if total_all > 0 else 0.5
                    progress = min(progress, 0.95)

                    self._safe_update_progress(
                        player_name, progress,
                        f"§a正在传送加载区块 ({unloaded_index + 1}/{total_tp})..."
                    )

                    # 传送到区块中心
                    center_y = (target_min[1] + target_max[1]) // 2
                    center = self.get_chunk_center(chunk_x, chunk_z, center_y)

                    try:
                        dim3 = self.server.level.get_dimension(dim_name)
                        loc = Location(dim3, float(center[0]), float(center[1]), float(center[2]))
                        self._safe_teleport(player_name, loc)
                    except Exception as e:
                        self.plugin.logger.warning(
                            f"TP to chunk ({chunk_x}, {chunk_z}) failed: {e}"
                        )

                    # 传送后等待区块加载
                    def after_tp_place():
                        if not self._is_player_online(player_name):
                            _cleanup_load()
                            self._safe_hide_progress(player_name)
                            return

                        # 传送后重新显示进度条
                        self._safe_show_progress(
                            player_name,
                            f"§a正在放置区块 ({unloaded_index + 1}/{total_tp})...",
                            progress, BarColor.GREEN
                        )

                        try:
                            tuples = fetch_chunk_tuples(chunk_x, chunk_z)
                            if tuples:
                                dim4 = self.server.level.get_dimension(dim_name)
                                self._place_chunk_blocks(
                                    tuples, dim4, placed_count,
                                    original_sink=_sink_original_record,
                                    container_writes=container_writes
                                )
                                # 放置完后立即释放该区块数据
                                tuples.clear()
                        except Exception as e:
                            self.plugin.logger.error(
                                f"Failed to place chunk ({chunk_x},{chunk_z}): {e}"
                            )

                        self.server.scheduler.run_task(
                            self.plugin,
                            lambda: phase2_place_unloaded(unloaded_index + 1, chunks_to_process),
                            delay=2
                        )

                    # 自动检测区块加载状态，动态决定等待时间
                    self._wait_for_chunk(
                        chunk_x, chunk_z, dim_name, after_tp_place,
                        player_name, reliable_mode,
                        cleanup_fn=_cleanup_load
                    )

                # ----------------------------------------------------------
                # 完成加载
                # ----------------------------------------------------------
                def finish_load():
                    if not self._is_player_online(player_name):
                        _cleanup_load()
                        self._safe_hide_progress(player_name)
                        return

                    # 传送回原位置
                    if original_loc is not None:
                        self._safe_teleport(player_name, original_loc)

                    # 先定义 show_final，再定义调用它的内部函数，避免向前引用问题
                    def show_final():
                        if not self._is_player_online(player_name):
                            self._safe_hide_progress(player_name)
                            return

                        has_containers = bool(container_writes)

                        def on_finish_display():
                            # 如果有实体需要生成，先生成实体再显示最终消息
                            def do_final_msg(entity_count: int):
                                self._safe_show_progress(player_name, "§a加载完成!", 1.0, BarColor.GREEN)
                                if has_containers:
                                    self._safe_send_message(
                                        player_name, "§a[MXCR] §l建筑已加载（包含容器物品）!"
                                    )
                                else:
                                    self._safe_send_message(player_name, "§a[MXCR] §l建筑已加载!")
                                self._safe_send_message(
                                    player_name,
                                    f"§f共放置了 §e{placed_count[0]} §f个方块"
                                )
                                if entity_count > 0:
                                    self._safe_send_message(
                                        player_name,
                                        f"§f共生成了 §e{entity_count} §f个实体"
                                    )
                                self._safe_send_message(
                                    player_name,
                                    f"§f智能分块: §a{len(loaded_chunks)} §f已加载 + "
                                    f"§e{len(unloaded_chunks)} §f需传送"
                                )
                                self.server.scheduler.run_task(
                                    self.plugin,
                                    lambda: self._safe_hide_progress(player_name),
                                    delay=40
                                )

                            # 尝试生成实体
                            entity_mgr = getattr(self.plugin, 'entity_manager', None)
                            if entity_mgr is not None and entities_raw:
                                try:
                                    dim_obj = self.server.level.get_dimension(dim_name)
                                    total_e = len(entities_raw)
                                    pending_e = [total_e]
                                    spawned_e = [0]

                                    def make_e_cb():
                                        def on_done(ok, error):
                                            if ok:
                                                spawned_e[0] += 1
                                            elif error:
                                                self.plugin.logger.warning(
                                                    f"[MXCR] 实体生成失败: {error}"
                                                )
                                            pending_e[0] -= 1
                                            if pending_e[0] <= 0:
                                                do_final_msg(spawned_e[0])
                                        return on_done

                                    for ei in entities_raw:
                                        entity_mgr.spawn_and_restore_entity(
                                            dimension=dim_obj,
                                            entity_info=ei,
                                            target_min_pos=target_pos,
                                            dim_name=dim_name,
                                            on_done=make_e_cb(),
                                        )
                                except Exception as e_err:
                                    self.plugin.logger.warning(
                                        f"[MXCR] 实体生成异常: {e_err}"
                                    )
                                    do_final_msg(0)
                            else:
                                do_final_msg(0)

                        if container_writes:
                            # 异步写入容器物品（v1.0.0: 分批节流，避免 scriptevent 洪泛）
                            self._safe_update_progress(
                                player_name, 0.95, "§a正在还原容器物品..."
                            )
                            self._safe_send_message(
                                player_name,
                                f"§e[MXCR] 正在还原 {len(container_writes)} 个容器的物品..."
                            )
                            self.batched_container_writes(
                                container_writes, dim_name, on_finish_display,
                                fail_log_prefix="容器写入失败"
                            )
                        else:
                            on_finish_display()

                    # 异步读取原始容器物品，然后提交撤销记录并处理容器写入
                    def _do_record_undo_and_write_containers():
                        """异步读取原始容器物品完毕后，提交撤销记录并写入容器"""
                        try:
                            # 将读取完物品的容器记录补写入 undo，然后提交入栈
                            for rec in pending_container_records:
                                undo_writer.add_block(rec)
                            pending_container_records.clear()
                            undo_writer.commit()
                        except Exception as e:
                            self.plugin.logger.warning(
                                f"[MXCR] 撤销记录失败（多区块加载）: {e}"
                            )
                        gc.collect()
                        # 进入容器写入阶段
                        self.server.scheduler.run_task(
                            self.plugin, show_final, delay=20
                        )

                    # 检查是否有容器需要异步读取物品（v1.0.0: 分批节流）
                    if pending_container_records:
                        container_mgr_r = self.plugin.container_manager
                        records_list = list(pending_container_records)
                        read_batch_size = 16

                        def read_orig_batch(start_idx):
                            if start_idx >= len(records_list):
                                _do_record_undo_and_write_containers()
                                return
                            batch = records_list[start_idx:start_idx + read_batch_size]
                            pending_r = [len(batch)]

                            def make_orig_read_cb(rec):
                                def cb(slots, error):
                                    if not error and slots:
                                        rec.nbt_data = json.dumps(slots, ensure_ascii=False)
                                    else:
                                        rec.nbt_data = None
                                    pending_r[0] -= 1
                                    if pending_r[0] <= 0:
                                        self.server.scheduler.run_task(
                                            self.plugin,
                                            lambda: read_orig_batch(start_idx + read_batch_size),
                                            delay=1
                                        )
                                return cb

                            for rec in batch:
                                container_mgr_r.request_read(
                                    x=rec.x, y=rec.y, z=rec.z,
                                    dim_name=dim_name,
                                    callback=make_orig_read_cb(rec)
                                )

                        read_orig_batch(0)
                    else:
                        _do_record_undo_and_write_containers()

                # 启动第一阶段
                self.server.scheduler.run_task(
                    self.plugin,
                    phase1_place_loaded,
                    delay=2
                )

            except Exception as e:
                self._safe_hide_progress(player_name)
                self._safe_send_message(player_name, f"§c[MXCR] 加载失败: {str(e)}")
                self.plugin.logger.error(f"load_with_chunk_loading failed: {e}")

        self.server.scheduler.run_task(self.plugin, do_prepare, delay=2)

    def _place_chunk_blocks(self, block_tuples: list, dimension,
                            placed_count: list,
                            original_records: list = None,
                            container_writes: list = None,
                            original_sink=None):
        """
        放置一组方块（使用偏移后的坐标元组）

        Args:
            block_tuples: [(nx, ny, nz, block_type, block_states, nbt_data), ...]
            dimension: Dimension 实例
            placed_count: 放置计数器 [int]（会被修改）
            original_records: 若传入列表，则在放置前记录原始方块（用于撤销，旧接口）
            container_writes: 若传入列表，则收集容器写入任务 [(nx, ny, nz, nbt_data), ...]
            original_sink: v1.0.0 新增 —— 若传入可调用对象，则逐条回调原始方块记录
                           （推荐，配合磁盘 undo writer 流式落盘，不占内存）
        """
        for item in block_tuples:
            # 兼容 5 元素（旧格式）和 6 元素（含 nbt_data）的元组
            if len(item) == 6:
                nx, ny, nz, block_type, block_states, nbt_data = item
            else:
                nx, ny, nz, block_type, block_states = item
                nbt_data = None
            try:
                block = dimension.get_block_at(nx, ny, nz)
                # 记录原始方块（用于撤销）
                if original_records is not None or original_sink is not None:
                    try:
                        bd = block.data
                        states = dict(bd.block_states) if bd.block_states else {}
                        cur_type = block_type_str(block)
                        # 如果原始方块是容器，标记需要异步读取物品数据
                        orig_nbt = "__NEED_READ__" if ContainerManager.is_container_block(cur_type) else None
                        rec = BlockRecord(
                            x=nx, y=ny, z=nz,
                            block_type=cur_type,
                            block_data=0,
                            block_states=states,
                            nbt_data=orig_nbt,
                        )
                        if original_sink is not None:
                            original_sink(rec)
                        else:
                            original_records.append(rec)
                    except Exception:
                        pass
                # 如果当前位置是容器方块，先替换为屏障（不会掉落物品）再放置目标方块
                try:
                    current_type = block_type_str(block)
                    if ContainerManager.is_container_block(current_type):
                        block.set_type("minecraft:barrier", False)
                except Exception:
                    pass

                if block_states and self.server:
                    try:
                        block_data = self.server.create_block_data(block_type, block_states)
                        block.set_data(block_data, False)
                    except Exception:
                        block.set_type(block_type, False)
                else:
                    block.set_type(block_type, False)
                placed_count[0] += 1
                # 收集容器写入任务
                if container_writes is not None and nbt_data and nbt_data != "__CONTAINER__":
                    container_writes.append((nx, ny, nz, nbt_data))
            except Exception:
                pass
