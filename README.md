# MXCR - Minecraft X Copy Region

基岩版建筑复制/粘贴插件，由 **Endstone Python 插件** 与 **SAPI 桥接 Add-on** 两部分组成，支持方块、方块状态、容器物品、实体的完整保存与恢复。

## 功能特性

- **选区系统**：木斧两点选区 + 木棍微调，粒子边框实时预览
- **建筑保存**：方块数据、方块状态（楼梯方向、门朝向等）、**箱子等容器的物品**、**建筑内的实体**（含 NBT、附魔、药水效果）一并保存
- **流式存储**：每个建筑独立 SQLite 数据库，边扫描边写盘，大型建筑不爆内存
- **智能加载**：预览面板 + 粒子边框，确认后才真正放置；支持当前位置 / 自定义坐标 / 脚下 / 面前放置
- **智能区块处理**：优先处理已加载区块，未加载区块自动等待，带 Boss Bar 进度条
- **撤销/重做**：磁盘化历史记录，支持大方块量操作的逐区块恢复
- **公开建筑**：玩家可将建筑分享至公共库，供全服加载（需权限）
- **三级权限**：服务器 OP / 游戏管理员 / 授权玩家 / 普通玩家，普通玩家支持 11 项细粒度权限键
- **搬迁模式**：管理员可远程搬迁他人建筑，原址自动清理并记录撤销

## 系统要求

| 组件 | 要求 |
|------|------|
| Endstone | 0.11 及以上 |
| Bedrock Dedicated Server | 1.26 及以上 |
| `@minecraft/server` | 2.5.0（行为包内置依赖） |
| Python | 3.10 及以上（Endstone 自带） |

## 安装方法

插件由两个部分组成，**两者都要安装**，容器物品和实体功能才能工作。

### 1. 安装 Endstone 插件（.whl）

从 [Releases](https://github.com/gxh6438/MXCR/releases) 下载最新的 `endstone_mxcr-x.x.x-py3-none-any.whl`，放入服务器 `plugins/` 目录。

重启服务器，看到以下日志说明插件已启用：

```
[INFO] [Mxcr] MXCR 建筑复制工具 v x.x.x 正在启用...
[INFO] [Mxcr] MXCR 建筑复制工具 v x.x.x 已启用!
```

### 2. 安装 SAPI 桥接包（.mcpack）

从 [Releases](https://github.com/gxh6438/MXCR/releases) 下载最新的 `MXCR-Bridge-x.x.x.mcpack` 并安装。

### 3. 验证安装

重启服务器后，在世界聊天中应看到：

```
[MXCR Bridge] 容器+实体桥接脚本已加载 v x.x.x
```

玩家在游戏内执行 `/mxcr bridge` 可主动探测桥接状态。

## 快速上手

1. **授权**（OP 执行）：`/mxcr op <玩家名>` 授权玩家使用，或 `/mxcr admin <玩家名>` 授予管理员（可管理全服建筑）
2. **选区**：`/mxcr start` 或主菜单"开始选区"，手持**木斧**右键两个对角方块，木斧粒子标记选区边界
3. **保存**：`/mxcr ok` 或主菜单"保存建筑"，输入建筑名称，容器物品与实体会自动异步读取
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
| `/mxcr bridge` | 复制权限+ | 探测 SAPI 桥接连接状态 |
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
| 撤销操作 / 恢复操作 | 按权限键 `allow_undo_redo` 显示；可选择快速模式或传统可靠模式 |
| 脚下放置方块 | 按权限键 `allow_foot_block` 显示；在脚下垫一块方块（可配置方块类型） |
| 停止选区 | 退出选区模式 |
| 公开建筑 | 按权限键 `allow_public_view` 显示；浏览全服分享的建筑 |
| 刷新区块 | 按权限键 `allow_refresh_chunks` 显示；手动触发区块加载 |
| 管理员面板 | 管理员可见；浏览任意玩家数据、管理公开建筑 |
| 插件设置 | 仅 OP；在线修改 config.json 配置 |
| 权限管理 | 仅 OP；管理授权玩家与游戏管理员 |
| 权限管理(普通玩家) | 仅 OP；需开启 `normal_player_permissions_enabled` 后显示 |

**加载建筑流程**：选择建筑后可配置粘贴方式（当前所在位置 / 自定义坐标）、是否使用传统可靠模式（逐区块确认，更稳但更慢）；确认后进入预览（粒子边框显示边界与高度），预览面板可调整位置，确认粘贴后逐区块放置并自动生成撤销记录。

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
| `preserve_air` | `true` | 粘贴时是否保留空气位置的原有方块 |
| `excluded_blocks` | `[]` | 保存时排除的方块类型 |
| `authorized_save_limit` | `20` | 授权玩家建筑保存上限 |
| `menu_item_enabled` | `false` | 启用菜单物品（手持右键开菜单） |
| `menu_item_id` | `minecraft:compass` | 菜单物品 ID |
| `batch_size` | `1000` | 每批处理的方块数 |
| `foot_block_id` | `minecraft:oak_planks` | 脚下放置方块的类型 |
| `selection_item_id` | `minecraft:wooden_axe` | 选区工具 |
| `modify_item_id` | `minecraft:stick` | 第二点修改工具 |
| `normal_player_permissions_enabled` | `false` | 启用普通玩家细粒度权限体系 |
| `chunk_wait_timeout_seconds` | `60` | 等待区块加载的超时秒数 |

## 数据存储

```
plugins/mxcr/
├── config.json                    # 插件配置
├── permissions.json                # 授权玩家/管理员/普通玩家默认权限
├── player_permissions/             # 玩家单独权限（按 UUID）
├── undo_cache/                     # 撤销历史（磁盘化，重启自动清理）
└── data/
    ├── {UUID}_{玩家名}/
    │   ├── mxcr_{建筑ID}.db        # 每个建筑独立 SQLite
    │   └── ...
    └── __public__/                 # 公开建筑库
        └── mxcr_{建筑ID}.db
```

每个 `.db` 含三张表：`metadata`（元数据）、`blocks`（方块+状态+容器 NBT）、`entities`（实体 JSON）。

## 常见问题

**Q: 后台警告"[MXCR] 容器读取失败 / 桥接通信失败"？**
按顺序排查：① 行为包是否已安装并在世界中启用；② 服务器启动日志是否有 `[MXCR Bridge] 已加载`；③ 游戏内执行 `/mxcr bridge` 看探测结果。

**Q: 保存时容器物品没存上？**
SAPI 桥接未工作（见上条）。没有桥接时方块仍可正常复制粘贴，仅容器物品与实体缺失。

**Q: 粘贴时提示高度超限？**
各维度高度不同：主世界 -64~319、下界 0~127、末地 0~255。请把粘贴原点选在使建筑完整落在范围内的位置。

**Q: 大型建筑操作卡顿？**
插件已做逐区块分批处理 + Boss Bar 进度显示，但仍会带来短暂负载；`batch_size` 可调小以减轻单 tick 压力。

**Q: 撤销数据占用磁盘吗？**
撤销历史磁盘化存放于 `undo_cache/`，操作后自动管理，插件启停时清理残留缓存。

## 从源码构建

```bash
# 构建 .whl
pip install build
python -m build --wheel

# 构建 .mcpack
cd addon
zip -r ../MXCR-Bridge-x.x.x.mcpack manifest.json scripts
```

## 许可

MIT License
