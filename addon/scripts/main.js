/*
 * MXCR Bridge - SAPI 端脚本
 * 版本: 1.0.1
 *
 * 功能:
 *   通过 /scriptevent 与 Endstone 插件双向通信，
 *   实现对箱子、漏斗、熔炉等容器方块的物品数据读写，
 *   以及对生物实体（装备/药水/颜色/属性）的数据读写。
 *
 * v1.0.0 新增:
 *   - 实体数据读取桥接（mxcr:read_entities）
 *   - 实体属性写入桥接（mxcr:apply_entity_data）
 *   - 实体数据同样支持分片传输协议
 *
 * 通信协议:
 *   Endstone → SAPI:
 *     /scriptevent mxcr:read_container       {"x":0,"y":64,"z":0,"dim":"minecraft:overworld","req_id":"abcd1234"}
 *     /scriptevent mxcr:write_container      {"x":0,"y":64,"z":0,"dim":"minecraft:overworld","req_id":"abcd1234","slots":[...]}
 *     /scriptevent mxcr:write_container_chunk {"req_id":"xxx","part":0,"total":3,"data":"..."}
 *     /scriptevent mxcr:read_entities        {"dim":"minecraft:overworld","req_id":"xxx","min_x":0,"min_y":0,"min_z":0,"max_x":10,"max_y":10,"max_z":10}
 *     /scriptevent mxcr:apply_entity_data    {"dim":"...","req_id":"xxx","runtime_id":12345,"data":{...}}
 *     /scriptevent mxcr:apply_entity_data_chunk {"req_id":"xxx","part":0,"total":3,"data":"..."}
 *
 *   SAPI → Endstone (通过 /scriptevent 回传):
 *     /scriptevent mxcr:container_data       {"req_id":"abcd1234","ok":true,"slots":[...]}
 *     /scriptevent mxcr:container_chunk      {"req_id":"xxx","part":0,"total":3,"data":"..."}
 *     /scriptevent mxcr:write_result         {"req_id":"abcd1234","ok":true,"count":27}
 *     /scriptevent mxcr:entity_data          {"req_id":"xxx","ok":true,"entities":[...]}
 *     /scriptevent mxcr:entity_chunk         {"req_id":"xxx","part":0,"total":3,"data":"..."}
 *     /scriptevent mxcr:apply_entity_result  {"req_id":"xxx","ok":true}
 *
 * entity 数据格式（每个元素）:
 *   {
 *     "runtime_id": 12345,             // 实体运行时 ID（用于 apply_entity_data 匹配）
 *     "equipment": {                   // 装备（null 表示空槽）
 *       "head":     <item_obj|null>,
 *       "chest":    <item_obj|null>,
 *       "legs":     <item_obj|null>,
 *       "feet":     <item_obj|null>,
 *       "mainhand": <item_obj|null>,
 *       "offhand":  <item_obj|null>
 *     },
 *     "effects": [                     // 药水效果
 *       {"type_id":"minecraft:speed","duration":200,"amplifier":1,"show_particles":true}
 *     ],
 *     "color": 0,                      // 颜色索引（-1 表示无此组件）
 *     "color2": -1,                    // 第二颜色索引（-1 表示无此组件）
 *     "properties": {}                 // 实体属性键值对
 *   }
 */

import { world, system, ItemStack, EquipmentSlot, EnchantmentType } from "@minecraft/server";

// ============================================================
// 常量
// ============================================================

/**
 * scriptevent 消息最大安全长度
 * scriptevent 限制 2048 字符，命令前缀约 40 字符，信封开销约 60 字符
 * 安全 data 片段长度取 1800
 */
const MAX_CHUNK_DATA_LEN = 1800;

// 不保存的实体类型（玩家、抛射物、掉落物等）
const EXCLUDED_ENTITY_TYPES = new Set([
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
]);

// ============================================================
// 分片传输工具
// ============================================================

/**
 * 分片缓冲区：收集来自 Endstone 的分片消息
 * key: req_id, value: { parts: {partIndex: string}, total: number }
 * @type {Map<string, {parts: Object<number, string>, total: number}>}
 */
const chunkBuffer = new Map();

/**
 * 通过 scriptevent 发送消息，超长时自动分片
 * @param {import("@minecraft/server").Dimension} dimension - 用于执行命令的维度
 * @param {string} eventId - 完整消息的事件 ID（如 "mxcr:container_data"）
 * @param {string} chunkEventId - 分片消息的事件 ID（如 "mxcr:container_chunk"）
 * @param {string} resultStr - 完整的 JSON 字符串
 */
function sendWithChunking(dimension, eventId, chunkEventId, resultStr) {
    const fullCmd = `scriptevent ${eventId} ${resultStr}`;
    if (fullCmd.length <= 2000) {
        // 短消息，直接发送
        safeRunCommand(dimension,fullCmd);
        return;
    }

    // 需要分片
    const parts = [];
    for (let i = 0; i < resultStr.length; i += MAX_CHUNK_DATA_LEN) {
        parts.push(resultStr.substring(i, i + MAX_CHUNK_DATA_LEN));
    }
    const total = parts.length;

    // 从 resultStr 中提取 req_id（避免重复解析完整 JSON）
    let reqId = "";
    try {
        const match = resultStr.match(/"req_id"\s*:\s*"([^"]+)"/);
        if (match) {
            reqId = match[1];
        }
    } catch (e) {
        try {
            reqId = JSON.parse(resultStr).req_id || "";
        } catch (e2) { /* ignore */ }
    }

    for (let i = 0; i < total; i++) {
        const envelope = JSON.stringify({
            req_id: reqId,
            part: i,
            total: total,
            data: parts[i]
        });
        safeRunCommand(dimension,`scriptevent ${chunkEventId} ${envelope}`);
    }
}

/**
 * 接收分片消息，收齐后返回重组的完整 JSON 对象
 * @param {object} envelope - 分片信封 {req_id, part, total, data}
 * @returns {object|null} 完整 JSON 对象，或 null（还需要更多片段）
 */
function receiveChunk(envelope) {
    const { req_id, part, total, data } = envelope;
    if (!req_id) return null;

    if (!chunkBuffer.has(req_id)) {
        chunkBuffer.set(req_id, { parts: {}, total: total });
    }

    const buf = chunkBuffer.get(req_id);
    buf.parts[part] = data;

    // 检查是否所有片段已收齐
    if (Object.keys(buf.parts).length >= buf.total) {
        let fullStr = "";
        for (let i = 0; i < buf.total; i++) {
            fullStr += buf.parts[i] || "";
        }
        chunkBuffer.delete(req_id);

        try {
            return JSON.parse(fullStr);
        } catch (e) {
            world.sendMessage(`§c[MXCR Bridge] 分片重组后 JSON 解析失败: ${e}`);
            return null;
        }
    }

    return null;
}

// ============================================================
// 工具函数
// ============================================================

/**
 * 根据维度名称获取 Dimension 对象
 * 大小写不敏感，兼容 Endstone 传来的所有形式：
 *   "Overworld" / "overworld" / "minecraft:overworld"
 *   "Nether" / "nether" / "minecraft:nether"
 *   "The End" / "the_end" / "minecraft:the_end"
 * @param {string} dimName
 * @returns {import("@minecraft/server").Dimension | undefined}
 */
function getDimension(dimName) {
    if (typeof dimName !== "string" || !dimName) return undefined;
    let key = dimName.trim().toLowerCase().replace(/\s+/g, "_");
    if (key.startsWith("minecraft:")) key = key.slice("minecraft:".length);
    const mapping = {
        "overworld": "minecraft:overworld",
        "nether":    "minecraft:nether",
        "the_end":   "minecraft:the_end",
        "end":       "minecraft:the_end",
    };
    const normalized = mapping[key] ?? ("minecraft:" + key);
    try {
        return world.getDimension(normalized);
    } catch (e) {
        return undefined;
    }
}

/**
 * 安全执行命令（v1.0.0 新增）
 * @minecraft/server 2.x 中 Dimension.runCommand 返回 Promise，
 * 命令失败时会产生 unhandled rejection 并在内容日志刷警告。
 * 本包装器同时兼容 1.x（同步抛错）与 2.x（Promise reject）。
 * @param {import("@minecraft/server").Dimension} dimension
 * @param {string} cmd
 */
function safeRunCommand(dimension, cmd) {
    if (!dimension) return;
    try {
        const result = dimension.runCommand(cmd);
        if (result && typeof result.catch === "function") {
            result.catch(() => { /* 命令失败静默处理，避免 unhandled rejection */ });
        }
    } catch (e) { /* 1.x 同步抛错时静默 */ }
}

/**
 * 序列化单个 ItemStack 为 JSON 可存储的对象
 * @param {import("@minecraft/server").ItemStack} item
 * @param {number} slot
 * @returns {object}
 */
function serializeItem(item, slot) {
    const obj = {
        slot: slot,
        type_id: item.typeId,
        amount: item.amount,
        keep_on_death: item.keepOnDeath,
        name_tag: item.nameTag ?? null,
        lore: item.getLore() ?? [],
        enchantments: [],
        durability: null,
        can_destroy: item.getCanDestroy() ?? [],
        can_place_on: item.getCanPlaceOn() ?? [],
    };

    // 附魔
    // v1.0.0: 兼容 1.x ({typeName, level}) 与 2.x ({type: EnchantmentType, level})
    try {
        const enchComp = item.getComponent("minecraft:enchantable");
        if (enchComp) {
            const enchs = enchComp.getEnchantments();
            for (const ench of enchs) {
                let typeId = "";
                if (ench.type && typeof ench.type === "object" && ench.type.id) {
                    typeId = ench.type.id;          // 2.x: EnchantmentType 对象
                } else if (typeof ench.type === "string") {
                    typeId = ench.type;             // 部分版本: 直接字符串
                } else if (ench.typeName) {
                    typeId = ench.typeName;         // 1.x: typeName 字段
                }
                if (typeId) {
                    obj.enchantments.push({
                        type: typeId,
                        level: ench.level,
                    });
                }
            }
        }
    } catch (e) { /* 无附魔组件则跳过 */ }

    // 耐久
    try {
        const durComp = item.getComponent("minecraft:durability");
        if (durComp) {
            obj.durability = durComp.damage; // 已损耗耐久值
        }
    } catch (e) { /* 无耐久组件则跳过 */ }

    return obj;
}

/**
 * 从序列化对象同步构造 ItemStack
 * （SAPI 事件回调中不能使用 async/await，必须同步）
 * @param {object} slotData
 * @returns {import("@minecraft/server").ItemStack | null}
 */
function buildItemStack(slotData) {
    try {
        const item = new ItemStack(slotData.type_id, slotData.amount ?? 1);

        // 自定义名称
        if (slotData.name_tag != null) {
            item.nameTag = slotData.name_tag;
        }

        // Lore
        if (Array.isArray(slotData.lore) && slotData.lore.length > 0) {
            item.setLore(slotData.lore);
        }

        // 死亡保留
        if (slotData.keep_on_death === true) {
            item.keepOnDeath = true;
        }

        // 可破坏方块
        if (Array.isArray(slotData.can_destroy) && slotData.can_destroy.length > 0) {
            item.setCanDestroy(slotData.can_destroy);
        }

        // 可放置方块
        if (Array.isArray(slotData.can_place_on) && slotData.can_place_on.length > 0) {
            item.setCanPlaceOn(slotData.can_place_on);
        }

        // 附魔
        // v1.0.0: 2.x 的 addEnchantment 要求 type 为 EnchantmentType 实例，
        // 传入裸对象 {id: ...} 会在原生校验时抛错并被静默吞掉，导致附魔静默丢失。
        // 改为通过 EnchantmentType.get(id) 获取实例后再添加。
        if (Array.isArray(slotData.enchantments) && slotData.enchantments.length > 0) {
            try {
                const enchComp = item.getComponent("minecraft:enchantable");
                if (enchComp) {
                    for (const ench of slotData.enchantments) {
                        try {
                            let enchType = null;
                            if (typeof EnchantmentType !== "undefined" && EnchantmentType.get) {
                                enchType = EnchantmentType.get(ench.type);
                            }
                            if (enchType) {
                                enchComp.addEnchantment({ type: enchType, level: ench.level });
                            } else {
                                // 回退：旧版本接受裸对象/字符串
                                enchComp.addEnchantment({ type: { id: ench.type }, level: ench.level });
                            }
                        } catch (e) { /* 跳过不兼容的附魔 */ }
                    }
                }
            } catch (e) { /* 无附魔组件 */ }
        }

        // 耐久
        if (slotData.durability != null) {
            try {
                const durComp = item.getComponent("minecraft:durability");
                if (durComp) {
                    durComp.damage = slotData.durability;
                }
            } catch (e) { /* 无耐久组件 */ }
        }

        return item;
    } catch (e) {
        return null;
    }
}

// ============================================================
// 读取容器
// ============================================================

/**
 * 读取指定坐标容器的所有物品，通过 scriptevent 回传给 Endstone
 * 如果结果超长，自动使用分片传输
 * @param {object} payload - {x, y, z, dim, req_id}
 */
function handleReadContainer(payload) {
    const { x, y, z, dim, req_id } = payload;

    const dimension = getDimension(dim);
    if (!dimension) {
        const result = JSON.stringify({ req_id, ok: false, error: `维度 ${dim} 不存在` });
        safeRunCommand(world.getDimension("minecraft:overworld"),`scriptevent mxcr:container_data ${result}`);
        return;
    }

    try {
        const block = dimension.getBlock({ x, y, z });
        if (!block) {
            const result = JSON.stringify({ req_id, ok: false, error: "方块不存在或区块未加载" });
            sendWithChunking(dimension, "mxcr:container_data", "mxcr:container_chunk", result);
            return;
        }

        const invComp = block.getComponent("minecraft:inventory");
        if (!invComp || !invComp.container) {
            // 该方块没有容器组件（不是容器方块）
            const result = JSON.stringify({ req_id, ok: false, error: "该方块不是容器", not_container: true });
            sendWithChunking(dimension, "mxcr:container_data", "mxcr:container_chunk", result);
            return;
        }

        const container = invComp.container;
        const slots = [];

        for (let i = 0; i < container.size; i++) {
            const item = container.getItem(i);
            if (item) {
                slots.push(serializeItem(item, i));
            }
        }

        const result = JSON.stringify({ req_id, ok: true, slots, size: container.size });

        // 使用分片传输发送结果（自动判断是否需要分片）
        sendWithChunking(dimension, "mxcr:container_data", "mxcr:container_chunk", result);

    } catch (e) {
        const result = JSON.stringify({ req_id, ok: false, error: String(e) });
        try {
            sendWithChunking(dimension, "mxcr:container_data", "mxcr:container_chunk", result);
        } catch (e2) {
            safeRunCommand(world.getDimension("minecraft:overworld"),`scriptevent mxcr:container_data ${result}`);
        }
    }
}

// ============================================================
// 写入容器
// ============================================================

/**
 * 将物品数据写入指定坐标的容器
 * @param {object} payload - {x, y, z, dim, req_id, slots}
 */
function handleWriteContainer(payload) {
    const { x, y, z, dim, req_id, slots } = payload;

    const dimension = getDimension(dim);
    if (!dimension) {
        const result = JSON.stringify({ req_id, ok: false, error: `维度 ${dim} 不存在` });
        safeRunCommand(world.getDimension("minecraft:overworld"),`scriptevent mxcr:write_result ${result}`);
        return;
    }

    try {
        const block = dimension.getBlock({ x, y, z });
        if (!block) {
            const result = JSON.stringify({ req_id, ok: false, error: "方块不存在或区块未加载" });
            safeRunCommand(dimension,`scriptevent mxcr:write_result ${result}`);
            return;
        }

        const invComp = block.getComponent("minecraft:inventory");
        if (!invComp || !invComp.container) {
            const result = JSON.stringify({ req_id, ok: false, error: "该方块不是容器" });
            safeRunCommand(dimension,`scriptevent mxcr:write_result ${result}`);
            return;
        }

        const container = invComp.container;
        let count = 0;

        // 先清空容器
        container.clearAll();

        // 写入物品
        for (const slotData of slots) {
            if (slotData.slot < 0 || slotData.slot >= container.size) continue;
            const item = buildItemStack(slotData);
            if (item) {
                try {
                    container.setItem(slotData.slot, item);
                    count++;
                } catch (e) { /* 跳过单个物品的错误 */ }
            }
        }

        const result = JSON.stringify({ req_id, ok: true, count });
        safeRunCommand(dimension,`scriptevent mxcr:write_result ${result}`);

    } catch (e) {
        const result = JSON.stringify({ req_id, ok: false, error: String(e) });
        try {
            safeRunCommand(dimension,`scriptevent mxcr:write_result ${result}`);
        } catch (e2) {
            safeRunCommand(world.getDimension("minecraft:overworld"),`scriptevent mxcr:write_result ${result}`);
        }
    }
}

// ============================================================
// 实体数据读取
// ============================================================

/**
 * 序列化单个实体的 SAPI 侧数据（装备/药水/颜色/属性）
 * @param {import("@minecraft/server").Entity} entity
 * @returns {object}
 */
function serializeEntitySapiData(entity) {
    const loc = entity.location;
    const data = {
        sapi_id: entity.id,
        loc_x: loc.x,
        loc_y: loc.y,
        loc_z: loc.z,
        equipment: {
            head: null,
            chest: null,
            legs: null,
            feet: null,
            mainhand: null,
            offhand: null,
        },
        effects: [],
        color: -1,
        color2: -1,
        properties: {},
    };

    // ---- 装备 ----
    try {
        const equipComp = entity.getComponent("minecraft:equippable");
        if (equipComp) {
            const slotMap = {
                head:     EquipmentSlot.Head,
                chest:    EquipmentSlot.Chest,
                legs:     EquipmentSlot.Legs,
                feet:     EquipmentSlot.Feet,
                mainhand: EquipmentSlot.Mainhand,
                offhand:  EquipmentSlot.Offhand,
            };
            for (const [key, slot] of Object.entries(slotMap)) {
                try {
                    const item = equipComp.getEquipment(slot);
                    if (item) {
                        // 复用 serializeItem，slot 字段此处用 key 字符串标识
                        const itemObj = serializeItem(item, 0);
                        delete itemObj.slot; // 装备不需要 slot 字段
                        data.equipment[key] = itemObj;
                    }
                } catch (e) { /* 该槽位不支持则跳过 */ }
            }
        }
    } catch (e) { /* 无装备组件 */ }

    // ---- 药水效果 ----
    // v1.0.0: @minecraft/server 2.0 移除了 Entity.getEffects()，
    // 改用 getComponent("minecraft:effects") 的 EntityEffectComponent.getEffects()
    try {
        let effects = null;
        const effectsComp = entity.getComponent("minecraft:effects");
        if (effectsComp && typeof effectsComp.getEffects === "function") {
            effects = effectsComp.getEffects();      // 2.x
        } else if (typeof entity.getEffects === "function") {
            effects = entity.getEffects();           // 1.x 回退
        }
        if (effects && effects.length > 0) {
            for (const effect of effects) {
                data.effects.push({
                    type_id: effect.typeId,
                    duration: effect.duration,
                    amplifier: effect.amplifier,
                    show_particles: effect.displayOnScreenTextureAnimation,
                });
            }
        }
    } catch (e) { /* 无效果 */ }

    // ---- 颜色 ----
    try {
        const colorComp = entity.getComponent("minecraft:color");
        if (colorComp) {
            data.color = colorComp.value;
        }
    } catch (e) { /* 无颜色组件 */ }

    try {
        const color2Comp = entity.getComponent("minecraft:color2");
        if (color2Comp) {
            data.color2 = color2Comp.value;
        }
    } catch (e) { /* 无第二颜色组件 */ }

    // ---- 幼年状态 ----
    // EntityIsBabyComponent 没有可写属性，仅通过组件是否存在判断
    try {
        const babyComp = entity.getComponent("minecraft:is_baby");
        data.is_baby = (babyComp !== undefined && babyComp !== null);
    } catch (e) {
        data.is_baby = false;
    }

    // ---- 实体属性（Entity Properties）----
    // 通过 getProperties() 获取自定义行为包属性
    // 注意：过滤掉 minecraft:is_baby，因为已单独用 is_baby 字段处理，避免重复写入导致充突
    try {
        const props = entity.getProperties();
        if (props && typeof props === "object") {
            for (const [key, value] of Object.entries(props)) {
                if (key === "minecraft:is_baby") continue;  // 已由 is_baby 字段单独处理
                data.properties[key] = value;
            }
        }
    } catch (e) { /* 无属性 */ }

    return data;
}

/**
 * 读取选区内所有实体的 SAPI 数据，通过 scriptevent 回传给 Endstone
 * @param {object} payload - {dim, req_id, min_x, min_y, min_z, max_x, max_y, max_z}
 */
function handleReadEntities(payload) {
    const { dim, req_id, min_x, min_y, min_z, max_x, max_y, max_z } = payload;

    const dimension = getDimension(dim);
    if (!dimension) {
        const result = JSON.stringify({ req_id, ok: false, error: `维度 ${dim} 不存在` });
        safeRunCommand(world.getDimension("minecraft:overworld"),`scriptevent mxcr:entity_data ${result}`);
        return;
    }

    try {
        // 使用 queryOptions 过滤选区内的实体
        const entities = dimension.getEntities({
            location: {
                x: (min_x + max_x) / 2,
                y: (min_y + max_y) / 2,
                z: (min_z + max_z) / 2,
            },
            maxDistance: Math.sqrt(
                Math.pow((max_x - min_x) / 2 + 1, 2) +
                Math.pow((max_y - min_y) / 2 + 1, 2) +
                Math.pow((max_z - min_z) / 2 + 1, 2)
            ) + 1,
        });

        const entityDataList = [];

        for (const entity of entities) {
            // 跳过排除的实体类型
            if (EXCLUDED_ENTITY_TYPES.has(entity.typeId)) continue;

            // 精确 AABB 过滤（getEntities 用球形范围，需要再过滤一次）
            const loc = entity.location;
            if (loc.x < min_x - 0.5 || loc.x > max_x + 0.5) continue;
            if (loc.y < min_y - 0.5 || loc.y > max_y + 1.5) continue;
            if (loc.z < min_z - 0.5 || loc.z > max_z + 0.5) continue;

            try {
                entityDataList.push(serializeEntitySapiData(entity));
            } catch (e) {
                // 单个实体序列化失败不影响其他实体
            }
        }

        const result = JSON.stringify({ req_id, ok: true, entities: entityDataList });
        sendWithChunking(dimension, "mxcr:entity_data", "mxcr:entity_chunk", result);

    } catch (e) {
        const result = JSON.stringify({ req_id, ok: false, error: String(e) });
        try {
            sendWithChunking(dimension, "mxcr:entity_data", "mxcr:entity_chunk", result);
        } catch (e2) {
            safeRunCommand(world.getDimension("minecraft:overworld"),`scriptevent mxcr:entity_data ${result}`);
        }
    }
}

// ============================================================
// 实体属性写入（粘贴后应用 SAPI 侧数据）
// ============================================================

/**
 * 向指定坐标附近的实体应用 SAPI 侧数据（装备/药水/颜色/属性）
 * 通过坐标+类型匹配实体，避免 Endstone runtime_id 与 SAPI entity.id 不一致的问题
 * @param {object} payload - {dim, req_id, type_id, loc_x, loc_y, loc_z, data}
 */
function handleApplyEntityData(payload) {
    const { dim, req_id, type_id, loc_x, loc_y, loc_z, data } = payload;

    const dimension = getDimension(dim);
    if (!dimension) {
        const result = JSON.stringify({ req_id, ok: false, error: `维度 ${dim} 不存在` });
        safeRunCommand(world.getDimension("minecraft:overworld"),`scriptevent mxcr:apply_entity_result ${result}`);
        return;
    }

    try {
        // 通过坐标+类型查找实体（在 1.5 格范围内搜索，取最近的同类型实体）
        const searchLoc = { x: loc_x, y: loc_y, z: loc_z };
        const candidates = dimension.getEntities({
            location: searchLoc,
            maxDistance: 2.0,
            type: type_id,
        });

        let targetEntity = null;
        let minDist = Infinity;
        for (const entity of candidates) {
            const el = entity.location;
            const dist = Math.sqrt(
                Math.pow(el.x - loc_x, 2) +
                Math.pow(el.y - loc_y, 2) +
                Math.pow(el.z - loc_z, 2)
            );
            if (dist < minDist) {
                minDist = dist;
                targetEntity = entity;
            }
        }

        if (!targetEntity) {
            const result = JSON.stringify({ req_id, ok: false, error: `未在 (${loc_x},${loc_y},${loc_z}) 附近找到类型为 ${type_id} 的实体` });
            safeRunCommand(dimension,`scriptevent mxcr:apply_entity_result ${result}`);
            return;
        }

        applyEntitySapiData(targetEntity, data);

        const result = JSON.stringify({ req_id, ok: true });
        safeRunCommand(dimension,`scriptevent mxcr:apply_entity_result ${result}`);

    } catch (e) {
        const result = JSON.stringify({ req_id, ok: false, error: String(e) });
        try {
            safeRunCommand(dimension,`scriptevent mxcr:apply_entity_result ${result}`);
        } catch (e2) {
            safeRunCommand(world.getDimension("minecraft:overworld"),`scriptevent mxcr:apply_entity_result ${result}`);
        }
    }
}

/**
 * 将序列化的 SAPI 数据应用到实体上
 * @param {import("@minecraft/server").Entity} entity
 * @param {object} data - 序列化的实体 SAPI 数据
 */
function applyEntitySapiData(entity, data) {
    // ---- 装备 ----
    if (data.equipment) {
        try {
            const equipComp = entity.getComponent("minecraft:equippable");
            if (equipComp) {
                const slotMap = {
                    head:     EquipmentSlot.Head,
                    chest:    EquipmentSlot.Chest,
                    legs:     EquipmentSlot.Legs,
                    feet:     EquipmentSlot.Feet,
                    mainhand: EquipmentSlot.Mainhand,
                    offhand:  EquipmentSlot.Offhand,
                };
                for (const [key, slot] of Object.entries(slotMap)) {
                    const itemData = data.equipment[key];
                    if (itemData != null) {
                        try {
                            const item = buildItemStack(itemData);
                            if (item) {
                                equipComp.setEquipment(slot, item);
                            }
                        } catch (e) { /* 跳过单个装备槽的错误 */ }
                    }
                }
            }
        } catch (e) { /* 无装备组件 */ }
    }

    // ---- 药水效果 ----
    if (Array.isArray(data.effects) && data.effects.length > 0) {
        for (const eff of data.effects) {
            try {
                entity.addEffect(eff.type_id, eff.duration, {
                    amplifier: eff.amplifier ?? 0,
                    showParticles: eff.show_particles !== false,
                });
            } catch (e) { /* 跳过单个效果的错误 */ }
        }
    }

    // ---- 颜色、幼年状态、实体属性 ----
    // 这些操作不能在 restricted-execution mode 中执行（scriptevent 回调属于受限模式）
    // 必须包裹在 system.run() 中，切换到主线程非受限上下文
    system.run(() => {
        // 颜色
        if (data.color !== undefined && data.color >= 0) {
            try {
                const colorComp = entity.getComponent("minecraft:color");
                if (colorComp) {
                    colorComp.value = data.color;
                }
            } catch (e) { /* 无颜色组件 */ }
        }

        if (data.color2 !== undefined && data.color2 >= 0) {
            try {
                const color2Comp = entity.getComponent("minecraft:color2");
                if (color2Comp) {
                    color2Comp.value = data.color2;
                }
            } catch (e) { /* 无第二颜色组件 */ }
        }

        // 幼年状态
        // EntityIsBabyComponent 没有可写属性，通过 setProperty 设置幼年状态
        // 注意：不能使用 triggerEvent("minecraft:entity_born")，该事件仅在繁殖出生时触发，会导致意外副作用
        if (data.is_baby === true) {
            try {
                entity.setProperty("minecraft:is_baby", true);
            } catch (e) { /* 该实体类型不支持幼年属性 */ }
        }

        // 自定义属性
        if (data.properties && typeof data.properties === "object") {
            for (const [key, value] of Object.entries(data.properties)) {
                try {
                    entity.setProperty(key, value);
                } catch (e) { /* 跳过不支持的属性 */ }
            }
        }
    });
}

// ============================================================
// 清空容器（撤销/恢复前调用，防止物品掉落）
// ============================================================

/**
 * 清空指定坐标容器的所有物品，回传清空结果
 * 用于撤销/恢复操作前先清空容器，避免覆盖方块时物品掉落
 * @param {object} payload - {x, y, z, dim, req_id}
 */
function handleClearContainer(payload) {
    const { x, y, z, dim, req_id } = payload;

    const dimension = getDimension(dim);
    if (!dimension) {
        const result = JSON.stringify({ req_id, ok: false, error: `维度 ${dim} 不存在` });
        safeRunCommand(world.getDimension("minecraft:overworld"),`scriptevent mxcr:clear_result ${result}`);
        return;
    }

    try {
        const block = dimension.getBlock({ x, y, z });
        if (!block) {
            // 方块不存在也视为成功（可能已经是空气）
            const result = JSON.stringify({ req_id, ok: true });
            safeRunCommand(dimension,`scriptevent mxcr:clear_result ${result}`);
            return;
        }

        const invComp = block.getComponent("minecraft:inventory");
        if (!invComp || !invComp.container) {
            // 不是容器，直接返回成功
            const result = JSON.stringify({ req_id, ok: true });
            safeRunCommand(dimension,`scriptevent mxcr:clear_result ${result}`);
            return;
        }

        // 清空容器
        invComp.container.clearAll();

        const result = JSON.stringify({ req_id, ok: true });
        safeRunCommand(dimension,`scriptevent mxcr:clear_result ${result}`);

    } catch (e) {
        const result = JSON.stringify({ req_id, ok: false, error: String(e) });
        try {
            safeRunCommand(dimension,`scriptevent mxcr:clear_result ${result}`);
        } catch (e2) {
            safeRunCommand(world.getDimension("minecraft:overworld"),`scriptevent mxcr:clear_result ${result}`);
        }
    }
}

// ============================================================
// 注册 scriptevent 监听器
// ============================================================

system.afterEvents.scriptEventReceive.subscribe((event) => {
    const { id, message } = event;

    // 只处理 mxcr: 命名空间的事件
    if (!id.startsWith("mxcr:")) return;

    let payload;
    try {
        payload = JSON.parse(message);
    } catch (e) {
        world.sendMessage(`§c[MXCR Bridge] JSON 解析失败: ${id} - ${e}`);
        return;
    }

    switch (id) {
        // ---- 容器读写 ----
        case "mxcr:read_container":
            handleReadContainer(payload);
            break;

        case "mxcr:write_container":
            handleWriteContainer(payload);
            break;

        case "mxcr:write_container_chunk": {
            // 分片写入请求：收集片段，收齐后执行写入
            const fullPayload = receiveChunk(payload);
            if (fullPayload !== null) {
                handleWriteContainer(fullPayload);
            }
            break;
        }

        // ---- 容器清空（撤销/恢复前调用）----
        case "mxcr:clear_container":
            handleClearContainer(payload);
            break;

        // ---- 实体读写 ----
        case "mxcr:read_entities":
            handleReadEntities(payload);
            break;

        case "mxcr:apply_entity_data":
            handleApplyEntityData(payload);
            break;

        case "mxcr:apply_entity_data_chunk": {
            // 分片写入请求：收集片段，收齐后执行应用
            const fullPayload = receiveChunk(payload);
            if (fullPayload !== null) {
                handleApplyEntityData(fullPayload);
            }
            break;
        }

        // ---- 心跳检测 ----
        case "mxcr:ping": {
            const pong = JSON.stringify({ req_id: payload.req_id, ok: true });
            try {
                safeRunCommand(world.getDimension("minecraft:overworld"),
                    `scriptevent mxcr:container_data ${pong}`
                );
            } catch (e) {
                world.sendMessage(`§c[MXCR Bridge] ping 回传失败: ${e}`);
            }
            break;
        }

        default:
            // 未知事件，忽略
            break;
    }
}, {
    // 只接收来自服务端（命令/插件）的 scriptevent，不接收玩家发出的
    namespaces: ["mxcr"]
});

// world.sendMessage 不能在顶层直接调用，需延迟到第一个 tick
system.run(() => {
    world.sendMessage("§a[MXCR Bridge] 容器+实体桥接脚本已加载 v1.0.1");
});
