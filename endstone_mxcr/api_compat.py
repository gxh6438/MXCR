"""
Endstone API 兼容层 (v1.0.0 新增)

面向 Endstone 0.11.x 编写，同时对 0.12 (Unreleased) 的重大变更做前向兼容:
- Block.type / BlockData.type 将从 str 变为 BlockType 对象 (str() 可得 "namespace:key")
- Dimension.name 将被 Dimension.id (Identifier) 替代
- Location.dimension 将变为弱引用，可能为 None

所有涉及字符串比较、字典键、JSON/sqlite 序列化的地方
都应通过本模块归一化为 str，确保跨版本行为一致。
"""


def norm_type(t) -> str:
    """将方块/实体类型归一化为 "namespace:key" 字符串"""
    if t is None:
        return ""
    if isinstance(t, str):
        return t
    try:
        return str(t)
    except Exception:
        return ""


def block_type_str(block) -> str:
    """获取方块类型的字符串形式（兼容 0.11 str / 0.12 BlockType）"""
    try:
        return norm_type(block.type)
    except Exception:
        return ""


def dim_name(dimension) -> str:
    """获取维度名称字符串（兼容 0.11 Dimension.name / 0.12 Dimension.id）"""
    if dimension is None:
        return ""
    try:
        name = dimension.name
        if name:
            return str(name)
    except Exception:
        pass
    try:
        return str(dimension.id)
    except Exception:
        return ""


# 各维度的 Y 轴高度范围（包含上下边界）
# v1.0.0: 统一的高度限制表（此前 load_forms.py 硬编码 -64~319，下界/末地粘贴会误报）
DIM_Y_LIMITS = {
    "minecraft:overworld": (-64, 319),
    "overworld":           (-64, 319),
    "minecraft:nether":    (0,   127),
    "nether":              (0,   127),
    "minecraft:the_end":   (0,   255),
    "the_end":             (0,   255),
}

DIM_DISPLAY_NAMES = {
    "minecraft:overworld": "主世界",
    "overworld":           "主世界",
    "minecraft:nether":    "下界",
    "nether":              "下界",
    "minecraft:the_end":   "末地",
    "the_end":             "末地",
}


def get_dim_y_limits(dim_name_str: str) -> tuple:
    """获取维度的高度范围 (y_min, y_max)，未知维度默认按主世界处理"""
    return DIM_Y_LIMITS.get(str(dim_name_str).lower(), (-64, 319))


def get_dim_display_name(dim_name_str: str) -> str:
    """获取维度的中文显示名"""
    key = str(dim_name_str).lower()
    return DIM_DISPLAY_NAMES.get(key, key)
