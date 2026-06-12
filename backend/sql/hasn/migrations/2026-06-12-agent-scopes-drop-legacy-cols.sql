-- 2026-06-12 · hasn_agent_scopes 去除遗留列（设计事实源：16-工具授权统一与权限声明Manifest化 D-v3-2）
--
-- 背景：三态判定真相早已是 default_mode + capability_modes（消费时活取 D3）。
--   · scopes TEXT[]：不参与任何判定的审计残留列，DB 残留历史值反而误导成判定依据。
--   · post_needs_review：社区零引用的死字段（社区审核走 HasnPosts.status + 圈子 needs_review）。
-- 本迁移 drop 这两列，判定后只剩 default_mode + capability_modes，语义干净。
--
-- 兼容：JWT 的 scopes 审计 claim 改由固定常量 DEFAULT_AGENT_SCOPES 提供（不再 per-agent 入库）。
-- 回滚：ADD COLUMN 重建空列即可恢复结构（历史值本就不参与判定，无需精确还原）。
ALTER TABLE hasn_agent_scopes
  DROP COLUMN IF EXISTS scopes,
  DROP COLUMN IF EXISTS post_needs_review;
