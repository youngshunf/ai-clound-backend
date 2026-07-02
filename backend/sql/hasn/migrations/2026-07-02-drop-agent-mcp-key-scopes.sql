-- Agent JWT / MCP key scopes 全退役（实施102 S0-4）
-- 事实源：docs/hasn-node设计文档/MCP统一工具体系/实施/102-本地工具授权链路修复与AgentJWT-scopes全退役实施清单.md
--
-- 背景：scopes（Agent JWT claim + MCP key 的 JSONB scope 集）从来不是 per-agent 授权
-- 权威——它对所有 Agent 恒定（DEFAULT_AGENT_SCOPES 死快照），真正的授权只看
-- hasn_agent_scopes.{default_mode, capability_modes} 三态（消费时活取）。福仔：
-- 「把 agent jwt 的 scopes 完全退役，所有代码都清理干净，留着只会影响后面的判断」。
--
-- 本迁移退役 MCP key 表上的 scopes 列（JWT claim / DEFAULT_AGENT_SCOPES 已在代码层删除，
-- 无需迁移）。列内数据是死快照、无消费方，直接 DROP 即可。
--
-- 幂等：DROP COLUMN IF EXISTS 可反复执行。

ALTER TABLE "public"."hasn_agent_mcp_keys"
    DROP COLUMN IF EXISTS "scopes";
