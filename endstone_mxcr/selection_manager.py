"""
选区管理模块 - 包含粒子任务管理
"""
from .structure_data import Selection


class SelectionManager:
    """选区管理器 - 使用玩家名称作为键"""

    def __init__(self):
        """初始化选区管理器"""
        self._selections = {}            # player_name -> Selection
        self._selecting_players = set()   # 正在选区的玩家名称集合
        self._particle_tasks = {}         # player_name -> Task (粒子定时任务)

    def start_selection(self, player_name: str):
        """开始选区"""
        self._selections[player_name] = Selection()
        self._selecting_players.add(player_name)

    def stop_selection(self, player_name: str):
        """停止选区模式(保留选区数据)"""
        self._selecting_players.discard(player_name)

    def is_selecting(self, player_name: str) -> bool:
        """检查玩家是否正在选区"""
        return player_name in self._selecting_players

    def set_position(self, player_name: str, pos: tuple, dimension_name: str, index: int):
        """设置选区点位置

        Args:
            player_name: 玩家名称
            pos: 位置坐标 (x, y, z)
            dimension_name: 维度名称
            index: 点索引 (1 或 2)
        """
        if player_name not in self._selections:
            self._selections[player_name] = Selection()

        selection = self._selections[player_name]
        selection.dimension_name = dimension_name

        if index == 1:
            selection.pos1 = pos
        elif index == 2:
            selection.pos2 = pos

    def get_selection(self, player_name: str) -> Selection:
        """获取玩家的选区"""
        if player_name not in self._selections:
            self._selections[player_name] = Selection()
        return self._selections[player_name]

    def clear_selection(self, player_name: str):
        """清除玩家的选区"""
        self._selections.pop(player_name, None)
        self._selecting_players.discard(player_name)
        # 取消粒子任务
        self.cancel_particle_task(player_name)

    def has_complete_selection(self, player_name: str) -> bool:
        """检查玩家是否有完整的选区"""
        if player_name not in self._selections:
            return False
        return self._selections[player_name].is_complete()

    # ========== 粒子任务管理 ==========

    def set_particle_task(self, player_name: str, task):
        """设置粒子定时任务

        Args:
            player_name: 玩家名称
            task: endstone.scheduler.Task 对象
        """
        # 先取消旧任务
        self.cancel_particle_task(player_name)
        self._particle_tasks[player_name] = task

    def cancel_particle_task(self, player_name: str):
        """取消粒子定时任务"""
        if player_name in self._particle_tasks:
            try:
                task = self._particle_tasks[player_name]
                if not task.is_cancelled:
                    task.cancel()
            except Exception:
                pass
            del self._particle_tasks[player_name]

    def has_particle_task(self, player_name: str) -> bool:
        """检查玩家是否有粒子任务"""
        if player_name not in self._particle_tasks:
            return False
        try:
            return not self._particle_tasks[player_name].is_cancelled
        except Exception:
            return False
