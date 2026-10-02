"""
进度条管理模块 - 使用 Boss Bar 显示操作进度
"""
from endstone.boss import BarColor, BarStyle


class ProgressManager:
    """进度条管理器"""

    def __init__(self, server):
        """
        Args:
            server: Endstone Server 实例
        """
        self.server = server
        self._active_bars = {}  # {player_name: BossBar}

    def show_progress(self, player, title: str, progress: float = 0.0, 
                     color: BarColor = BarColor.GREEN):
        """
        显示进度条给玩家
        
        Args:
            player: 玩家对象
            title: 进度条标题
            progress: 进度值 (0.0 - 1.0)
            color: 进度条颜色
        """
        player_name = player.name
        
        # 如果已有进度条，先移除
        if player_name in self._active_bars:
            self.hide_progress(player)
        
        # 创建新的进度条
        clamped = max(0.0, min(1.0, progress))
        bar = self.server.create_boss_bar(
            title=title,
            color=color,
            style=BarStyle.SOLID
        )
        bar.progress = clamped  # 先设置进度值
        bar.add_player(player)
        bar.progress = clamped  # add_player 后再设一次，确保客户端同步正确值
        
        self._active_bars[player_name] = bar

    def update_progress(self, player, progress: float = None, title: str = None):
        """
        更新玩家的进度条
        
        Args:
            player: 玩家对象
            progress: 新的进度值 (可选)
            title: 新的标题 (可选)
        """
        player_name = player.name
        
        if player_name not in self._active_bars:
            return
        
        bar = self._active_bars[player_name]
        
        if progress is not None:
            bar.progress = max(0.0, min(1.0, progress))
        
        if title is not None:
            bar.title = title

    def hide_progress(self, player):
        """
        隐藏玩家的进度条（带离线安全检查）
        
        Args:
            player: 玩家对象
        """
        player_name = player.name
        
        if player_name in self._active_bars:
            bar = self._active_bars[player_name]
            try:
                bar.remove_player(player)
            except Exception:
                # 玩家可能已离线，remove_player 可能失败，忽略异常
                pass
            del self._active_bars[player_name]

    def hide_progress_by_name(self, player_name: str):
        """
        通过玩家名隐藏进度条（用于玩家已离线的情况）
        
        Args:
            player_name: 玩家名称
        """
        if player_name in self._active_bars:
            bar = self._active_bars[player_name]
            try:
                bar.remove_all()
            except Exception:
                pass
            del self._active_bars[player_name]

    def has_progress(self, player) -> bool:
        """
        检查玩家是否有活动的进度条
        
        Args:
            player: 玩家对象
            
        Returns:
            bool: 是否有活动的进度条
        """
        return player.name in self._active_bars

    def has_progress_by_name(self, player_name: str) -> bool:
        """
        通过玩家名检查是否有活动的进度条
        
        Args:
            player_name: 玩家名称
            
        Returns:
            bool: 是否有活动的进度条
        """
        return player_name in self._active_bars
