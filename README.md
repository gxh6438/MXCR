# MXCR - Minecraft X Copy Region

基岩版建筑复制/粘贴插件（Endstone Python 插件）。v1.1.0 起底层对接 [StructBridge](https://github.com/gxh6438/structbridge)，保存/加载/撤销全部走**引擎原生 `.mcstructure` 序列化**，方块、方块状态、容器物品、实体完整保真，无需任何行为包。

## 功能特性

- **选区系统**：木斧两点选区 + 木棍微调，粒子边框实时预览
- **建筑保存**：StructBridge 引擎原生导出，方块数据、方块状态（楼梯方向、门朝向等）、**箱子等容器的物品**、**建筑内的实体**（含 NBT、附魔、药水效果）完整保真
- **引擎原生存储**：建筑以 `.mcstructure` 文件存放于 StructBridge 结构库；超出引擎单模板上限（64×384×64）的大区域自动按 64×64 网格分片为 bundle 结构束
- **智能加载**：预览面板 + 粒子边框，确认后才真正放置；支持当前位置 / 自定义坐标 / 脚下 / 面前放置
- **智能区块处理**：由 StructBridge 自动创建 tickingarea 加载目标区域，非主世界操作会临时传送操作者并结束后送回
- **撤销/重做**：快照式历史记录——粘贴/清除前先对目标区域做引擎原生快照，undo/redo 对称"贴回快照"，容器与实体一并还原
- **公开建筑**：玩家可将建筑分享至公共库，供全服加载（需权限）
- **三级权限**：服务器 OP / 游戏管理员 / 授权玩家 / 普通玩家，普通玩家支持 11 项细粒度权限键
- **搬迁模式**：管理员可远程搬迁他人建筑，原址自动清理并记录撤销

## 系统要求

| 组件 | 要求 |
|------|------|
| Endstone | 0.11 及以上 |
| Bedrock Dedicated Server | 1.26 及以上 |
| [StructBridge](https://github.com/gxh6438/structbridge) | v0.0.3 及以上（Linux/Windows 均需含 Python API） |
| Python | 3.10 及以上（Endstone 自带） |

## 安装方法

插件依赖 **StructBridge** 提供底层序列化，**两者都要安装**。

### 1. 安装 StructBridge 插件

从 [StructBridge Releases](https://github.com/gxh6438/structbridge/releases) 下载对应平台（Linux `.so` / Windows `.dll`）的最新插件，放入服务器 `plugins/` 目录。要求 v0.0.3 及以上（含进程内 Python API，Windows 自 v0.0.3 起支持）。

### 2. 安装 MXCR 插件（.whl）

从 [Releases](https://github.com/gxh6438/MXCR/releases) 下载最新的 `endstone_mxcr-x.x.x-py3-none-any.whl`，放入服务器 `plugins/` 目录。

重启服务器，看到以下日志说明安装成功：

```
[INFO] [Mxcr] MXCR 建筑复制工具 v x.x.x 正在启用...
[INFO] [Mxcr] StructBridge 后端已就绪 (v0.0.3)
[INFO] [Mxcr] MXCR 建筑复制工具 v x.x.x 已启用!
```

若看到 `StructBridge 后端不可用` 错误，说明 StructBridge 未安装或版本过低，保存/加载/撤销功能将无法使用。

### 3. 验证安装

玩家在游戏内执行 `/mxcr bridge`，应看到：

```
[MXCR 后端] StructBridge 连接正常!
```

> v1.1.0 不再需要安装任何行为包（.mcpack）；从 v1.0.x 升级时应移除旧版 `MXCR-Bridge` 行为包。

## 快速上手

1. **授权**（OP 执行）：`/mxcr op <玩家名>` 授权玩家使用，或 `/mxcr admin <玩家名>` 授予管理员（可管理全服建筑）
2. **选区**：`/mxcr start` 或主菜单"开始选区"，手持**木斧**右键两个对角方块，木斧粒子标记选区边界
3. **保存**：`/mxcr ok` 或主菜单"保存建筑"，输入建筑名称，引擎原生导出（容器物品与实体一并保存）
4. **加载**：`/mxcr` 打开主菜单 → "我的建筑" → 选择建筑 → 配置加载参数 → 预览确认 → 完成
5. **撤销**：`/mxcr undo`（需 `allow_undo_redo` 权限，默认仅授权玩家以上可用）

## 命令参考

| 命令 | 权限 | 说明 |
|------|------|------|
| `/mxcr` | 复制权限+ | 打开主菜单 |
| `/mxcr start` | 复制权限+ | 开始选区模式 |
| `/mxcr ok` | 复制权限+ | 确认并保存当前选区 |
| `/mxcr cancel` | 复制权限+ | 取消当前选区（保留选择模式） |
| `/mxcr stop` | 复制权限+ | 停止选区模式（下线粒子显示） |
| `/mxcr list` | 复制权限+ | 打开"我的建筑"列表 |
| `/mxcr preview` | 预览中 | 打开/关闭预览控制面板 |
| `/mxcr undo` | 见权限章节 | 撤销上一次操作 |
| `/mxcr redo` | 见权限章节 | 恢复被撤销的操作 |
| `/mxcr bridge` | 复制权限+ | 查看 StructBridge 后端连接状态 |
| `/mxcr op <玩家>` | OP | 授权玩家使用插件 |
| `/mxcr deop <玩家>` | OP | 取消玩家授权 |
| `/mxcr admin <玩家>` | OP | 设为游戏管理员 |
| `/mxcr deadmin <玩家>` | OP | 移除游戏管理员 |
| `/mxcr oplist` | OP | 查看授权玩家与管理员列表 |
| `/mxcr reload` | OP | 重载配置与权限文件 |

**选区操作**（选区模式下）：

| 物品 | 操作 | 效果 |
|------|------|------|
| 木斧（默认，可配置） | 右键方块 | 设置第一个点 → 第二个点 → 重新选点 |
| 木棍（默认，可配置） | 右键方块 | 选区完成后，单独修改第二个点 |

## 主菜单功能

| 按钮 | 说明 |
|------|------|
| 开始选区 | 进入选区模式 |
| 保存建筑 | 为当前选区命名并保存 |
| 我的建筑 | 浏览/加载/删除自己的建筑 |
| 撤销操作 / 恢复操作 | 按权限键 `allow_undo_redo` 显示；粘贴/清除前的区域快照直接贴回，容器与实体一并还原 |
| 脚下放置方块 | 按权限键 `allow_foot_block` 显示；在脚下垫一块方块（可配置方块类型） |
| 停止选区 | 退出选区模式 |
| 公开建筑 | 按权限键 `allow_public_view` 显示；浏览全服分享的建筑 |
| 刷新区块 | 按权限键 `allow_refresh_chunks` 显示；手动触发区块加载 |
| 管理员面板 | 管理员可见；浏览任意玩家数据、管理公开建筑 |
| 插件设置 | 仅 OP；在线修改 config.json 配置 |
| 权限管理 | 仅 OP；管理授权玩家与游戏管理员 |
| 权限管理(普通玩家) | 仅 OP；需开启 `normal_player_permissions_enabled` 后显示 |

**加载建筑流程**：选择建筑后可配置粘贴方式（当前所在位置 / 自定义坐标）；确认后进入预览（粒子边框显示边界与高度），预览面板可调整位置，确认粘贴后由引擎整体放置并自动生成撤销记录（粘贴前区域快照）。超大建筑由 StructBridge 自动分片放置。

## 权限系统

权限从高到低：

| 等级 | 获得方式 | 能力 |
|------|----------|------|
| 服务器 OP | 服务器权限文件 | 全部功能 + 插件设置 + 权限管理 |
| 游戏管理员 | `/mxcr admin <玩家>` | 全部建筑功能 + 管理**任意玩家**的建筑数据 + 公开库管理 |
| 授权玩家 | `/mxcr op <玩家>` | 全部建筑功能（自己的建筑 + 公开库） |
| 普通玩家 | 默认 | 仅 `allow_copy` 等开启的细粒度权限 |

**普通玩家细粒度权限键**（默认值在 `permissions.json` 的 `default_normal_permissions`，也可按玩家单独设置）：

| 权限键 | 默认值 | 说明 |
|--------|--------|------|
| `allow_copy` | `true` | 选区与保存建筑 |
| `allow_paste` | `false` | 加载自己的建筑 |
| `allow_delete_own` | `false` | 删除自己的建筑 |
| `allow_public_view` | `true` | 浏览公开建筑 |
| `allow_public_load` | `false` | 加载公开建筑 |
| `allow_public_share` | `false` | 分享建筑到公开库 |
| `allow_undo_redo` | `false` | 撤销/重做操作 |
| `allow_refresh_chunks` | `false` | 手动刷新区块 |
| `allow_foot_block` | `false` | 脚下放置方块 |
| `allow_move_mode` | `false` | 搬迁模式 |
| `max_structures` | `10` | 最大保存建筑数 |
| `max_blocks_per_copy` | `10000` | 单次复制最大方块数 |

> 该体系默认关闭（配置 `normal_player_permissions_enabled: false`），此时普通玩家无权使用插件，需 OP 授权。开启后，权限键生效且限制值按玩家单独或默认设置计算。

## 配置文件

`plugins/mxcr/config.json`（可用 `/mxcr` → 插件设置 在线修改）：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `plugin_enabled` | `true` | 插件总开关（OP 可绕过） |
| `max_block_count` | `100000` | 单次操作最大方块数 |
| `particle_id` | `minecraft:endrod` | 选区粒子效果 ID |
| `authorized_save_limit` | `20` | 授权玩家建筑保存上限 |
| `menu_item_enabled` | `false` | 启用菜单物品（手持右键开菜单） |
| `menu_item_id` | `minecraft:compass` | 菜单物品 ID |
| `foot_block_id` | `minecraft:oak_planks` | 脚下放置方块的类型 |
| `selection_item_id` | `minecraft:wooden_axe` | 选区工具 |
| `modify_item_id` | `minecraft:stick` | 第二点修改工具 |
| `normal_player_permissions_enabled` | `false` | 启用普通玩家细粒度权限体系 |

> v1.0.x 的 `preserve_air` / `excluded_blocks` / `batch_size` / `chunk_wait_timeout_seconds` 已随引擎原生化移除，加载配置时会自动清除这些遗留键。

## 数据存储

```
plugins/
├── mxcr/
│   ├── config.json                          # 插件配置
│   ├── permissions.json                     # 授权玩家/管理员/普通玩家默认权限
│   ├── player_permissions/                  # 玩家单独权限（按 UUID）
│   └── data/
│       └── structures.db                    # 元数据库（structures + undo_entries 两张表）
└── structbridge/
    └── library/
        └── mystructure/
            ├── mxcr_{建筑ID}.mcstructure     # 常规建筑（引擎原生格式）
            ├── mxcr_{建筑ID}.bundle/         # 大区域结构束（超出 64×384×64 时）
            │   ├── manifest.json
            │   └── tile_r{行}c{列}.mcstructure
            └── mxcrsnap_{快照ID}.mcstructure # 撤销快照（无引用后自动清理）
```

- **元数据**（名称、归属、公开状态、撤销栈）集中存于 `structures.db`；方块/实体数据本体由 StructBridge 以 `.mcstructure` 文件管理
- **撤销快照**（`mxcrsnap_*`）在撤销栈条目全部失效后被垃圾回收，不占长期空间
- v1.0.x 的每建筑独立 `.db` 与 `undo_cache/` 目录不再使用（v1.1.0 不兼容旧数据）

## 常见问题

**Q: 启动报 "StructBridge 后端不可用"？**
按顺序排查：① StructBridge 插件（Linux `.so` / Windows `.dll`）是否已放入 `plugins/`；② 版本是否为 v0.0.3 及以上（含 Python API）；③ 游戏内执行 `/mxcr bridge` 查看详细提示。

**Q: 保存/加载时提示 Y 轴超限？**
引擎单结构模板高度上限为 384 格，超出会拒绝保存。请调整选区高度范围后重试。

**Q: 粘贴时提示高度超限？**
各维度高度不同：主世界 -64~319、下界 0~127、末地 0~255。请把粘贴原点选在使建筑完整落在范围内的位置。

**Q: 大型建筑操作卡顿？**
保存/加载由引擎原生序列化与放置完成，超大区域自动分片（bundle）。分片放置在服务器线程进行，瞬时负载略高属正常现象。

**Q: 旋转/翻转建筑去哪了？**
v1.1.0 底层改为引擎原生放置，StructBridge 以固定朝向（0 度、不镜像）写入结构，暂不支持旋转与翻转。

**Q: 撤销数据占用磁盘吗？**
撤销记录即撤销快照（`.mcstructure` 文件），每玩家默认保留最近 10 条；栈条目失效后快照文件自动垃圾回收。

## 从源码构建

```bash
# 构建 .whl
pip install build
python -m build --wheel
```

## 许可

MIT License
