-- =====================================================
-- 分身运行时配置：hasn_agents 补 runtime_config_json 列
-- per-agent hermes runtime 原生配置（云端权威，桌面端编辑 → 落库 bump revision →
-- daemon 下发写入该分身 runtime 的 config.yaml/.env）。形状（全部可空=跟随默认）：
--   {
--     "models": {"main": null, "fast": null, "vision": null, "delegation": null},
--     "working_directory": null,   -- TERMINAL_CWD（空=默认隔离工作区）
--     "max_turns": null,           -- agent.max_turns（空=50）
--     "gateway_timeout": null,     -- agent.gateway_timeout（空=600；区别于权限页审批超时）
--     "memory_enabled": null,      -- memory.memory_enabled（空=true）
--     "user_profile_enabled": null,-- memory.user_profile_enabled（空=true）
--     "timezone": null             -- HERMES_TIMEZONE（空=Asia/Shanghai）
--   }
-- 主模型(models.main)由 daemon 路由到既有 LLM 通道写 model.default（带凭据）；其余槽位
-- 由 runtime /runtime-config 端点写 auxiliary.*/delegation.*/agent.*/memory.*/.env。
-- 存量行为 NULL，runtime 全部回退原硬编码默认，行为不变。
-- =====================================================

ALTER TABLE "public"."hasn_agents"
  ADD COLUMN IF NOT EXISTS "runtime_config_json" jsonb DEFAULT NULL;

COMMENT ON COLUMN "public"."hasn_agents"."runtime_config_json" IS 'Agent hermes runtime 原生配置（4 槽模型/工作目录/max_turns/网关超时/记忆开关/时区；云端权威，下发写入 runtime config.yaml/.env）';
