-- 迁移：清理 public schema 的孤儿表（过时设计残留）+ 截断 sys_opera_log 历史
-- 背景：public schema 累积了多次设计演进遗留的旧表，与代码 ORM 模型对账后，
--       发现 16 张表「库里有、运行时 metadata 与代码全无引用、也无任何活 SQL」→ 确认废弃。
-- 对账方法：导入全量 model + 整棵 router 树得到 MappedBase.metadata（运行时真正认识的表），
--           与库 public 实际表做差集，再对每张差集表全仓 grep 确认无裸 SQL 引用。
-- 安全前置（均已核验）：
--   1) 无任何【保留表】外键反向指向这 16 张表 → DROP 不会被依赖阻塞、不会级联误伤。
--   2) 这 16 张表 0 行或仅为备份/测试快照；platform_scopes(11)/system_config(1) 为旧设计 seed，已被取代。
-- 幂等：DROP ... IF EXISTS；opera_log 段为按时间窗 DELETE，可重复执行（亦可挂定时任务）。
-- 破坏性 DDL：生产执行须在停机/低峰窗口，先全库备份（PG16 用 /www/server/pgsql/bin/pg_dump）。
-- 执行：psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 本文件
--
-- ⚠️ 刻意【不删】的表（别在本文件加）：
--   · 记忆相关旧表（episodic_turns / memory_events / memory_extraction_jobs / semantic_facts）
--     —— 福仔 2026-06-15「记忆相关的表先不删」，留待记忆子系统收尾时统一处置。
--   · memory_namespace_revisions(31 行) —— 现行记忆 revision 表，hasn_sync_service.py 裸 SQL 活用，保留。
--   · hasn_agent_scopes(16 行) —— 能力三态真相表，agent_jwt.py 裸 SQL 活用（v3 只删了 scopes 列），保留。
--   · sys_topic / project_topics / projects / model_credit_rate —— 由 new-api 解耦会话(NEWAPI-P5)处置。
--   · hasn_workspace_app / hasn_user_active_workspace —— 由应用平台 v3 拆 workspace 会话(V3-P3)处置。

BEGIN;

-- ── A 桶：备份 / 测试 / 从未落地（零引用零有效数据）──────────────────────────
DROP TABLE IF EXISTS public._bak_hasn_conversations;   -- 旧迁移留的会话备份快照
DROP TABLE IF EXISTS public._bak_hasn_humans;          -- 旧迁移留的人身份备份快照
DROP TABLE IF EXISTS public._bak_hasn_messages;        -- 旧迁移留的消息备份快照
DROP TABLE IF EXISTS public.codegen_test_task;         -- fba codegen 工具测试残留
DROP TABLE IF EXISTS public.lead_collection_job_smoke; -- 获客采集早期冒烟测试表
DROP TABLE IF EXISTS public.lead_source_config_smoke;  -- 获客采集早期冒烟测试表
DROP TABLE IF EXISTS public.checkins;                  -- 零引用零数据，功能从未落地
DROP TABLE IF EXISTS public.custom_oauth_providers;    -- 旧 OAuth 设计（现用 sys_user_social）
DROP TABLE IF EXISTS public.user_oauth_bindings;       -- 旧 OAuth 设计（现用 sys_user_social）

-- ── B 桶（runtime 部分）：旧 Agent runtime 绑定设计 ─────────────────────────
-- 被 hermes_agent_runtime_state + hasn_agent_runtime_reports + hasn_node_bindings 取代
DROP TABLE IF EXISTS public.hasn_agent_runtime_bindings;
DROP TABLE IF EXISTS public.hasn_agent_runtime_events;
DROP TABLE IF EXISTS public.hasn_agent_runtime_status;
DROP TABLE IF EXISTS public.hasn_binding_events;

-- ── C 桶：schema 迁移遗留的空壳副本 ───────────────────────────────────────
-- pay_app 活表已在 hasn_billing.pay_app（2026-06-14 billing 迁移）；public 这张是
-- 迁移幂等守卫「目标已存在则跳过 SET SCHEMA」留下的 0 行空壳。
DROP TABLE IF EXISTS public.pay_app;

-- ── D 桶：旧设计入库表（无活 SQL，已被代码定义取代）──────────────────────────
-- platform_scopes：最早的 AI-Native App 能力 scope 目录入库设计；现 scope 由代码
--   scopes.py 聚合 + manifest 做权威（mcp_authz_unify_v3），表已无人读写。
DROP TABLE IF EXISTS public.platform_scopes;
-- system_config：早期自建全局配置表（唯一 1 行 credit_conversion，已被 billing/new-api 取代）；
--   fba 框架用的是 sys_config（另一张，保留）。
DROP TABLE IF EXISTS public.system_config;

-- ── sys_opera_log：保留近 30 天，截断更早（可重复执行 / 可挂定时）───────────────
-- 操作审计日志，框架表（保留表本身），仅清理 30 天前的历史行以释放空间。
DELETE FROM public.sys_opera_log
WHERE created_time < (now() - INTERVAL '30 days');

COMMIT;
