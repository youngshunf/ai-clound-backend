-- 2026-07-31 · H8 云端 Runtime 与云端分身下线：runtime_location 存量取值归一到 local
--
-- 背景：「分身跑在云端沙箱」（runtime_location='cloud'）这一形态已整体退役——云端 Runtime
-- 派发代理面 /api/v1/hasn/agent/runtime/* 及其派发/供给服务已删除。分身一律在 hasn-node 上
-- 运行；需要「关机后仍在线」的主人改用云端托管的无头 hasn-node（每订阅一容器 = 第 N 台设备），
-- 那些分身在数据上同样是 local——它们跑在一台节点上，只是这台节点住在云端。
--
-- 为什么保留列而不 DROP：
--   1. hasn_agents.runtime_location —— 读模型（AgentSnapshot / AgentProfileResponse）仍下发该
--      字段供 daemon read-through，存量行必须能读；
--   2. hasn_agent_channel_mirrors.runtime_location —— 渠道镜像的历史快照列，保留读语义，
--      不重写历史。
-- 写入侧已在应用层收窄：创建入参不再接受 runtime_location，服务端恒写 'local'。
--
-- 本迁移只做取值归一：先 SELECT 计数（部署时肉眼可见影响面），再就地 UPDATE。
-- 幂等：重复执行时 UPDATE 命中 0 行。

-- ── 1. 影响面计数（执行前先看这两行输出）────────────────────────────────
SELECT
    'hasn_agents' AS table_name,
    count(*) FILTER (WHERE "runtime_location" = 'cloud')     AS cloud_rows,
    count(*) FILTER (WHERE "runtime_location" = 'local')     AS local_rows,
    count(*)                                                 AS total_rows
FROM "public"."hasn_agents";

SELECT
    'hasn_agent_channel_mirrors' AS table_name,
    count(*) FILTER (WHERE "runtime_location" = 'cloud')     AS cloud_rows,
    count(*) FILTER (WHERE "runtime_location" = 'local')     AS local_rows,
    count(*)                                                 AS total_rows
FROM "public"."hasn_agent_channel_mirrors";

-- ── 2. 就地归一 ────────────────────────────────────────────────────────
UPDATE "public"."hasn_agents"
SET "runtime_location" = 'local',
    "updated_time"     = now()
WHERE "runtime_location" = 'cloud';

UPDATE "public"."hasn_agent_channel_mirrors"
SET "runtime_location" = 'local',
    "updated_time"     = now()
WHERE "runtime_location" = 'cloud';

-- ── 3. 列注释同步到退役后的语义 ────────────────────────────────────────
COMMENT ON COLUMN "public"."hasn_agents"."runtime_location" IS
    '运行位置 (local:本地:blue)；云端沙箱形态已于 H8 退役，取值恒为 local。列保留供存量行读取';
COMMENT ON COLUMN "public"."hasn_agent_channel_mirrors"."runtime_location" IS
    '运行位置快照 (local:本地桌面端:blue/remote:远端:green)；cloud 取值已于 H8 随云端沙箱形态退役。列保留供存量行读取';

-- ── 4. 归一后复核（cloud_rows 应为 0）──────────────────────────────────
SELECT
    'hasn_agents' AS table_name,
    count(*) FILTER (WHERE "runtime_location" = 'cloud') AS cloud_rows_after
FROM "public"."hasn_agents";

SELECT
    'hasn_agent_channel_mirrors' AS table_name,
    count(*) FILTER (WHERE "runtime_location" = 'cloud') AS cloud_rows_after
FROM "public"."hasn_agent_channel_mirrors";
