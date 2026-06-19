-- 设计系统协作分身绑定：hasn_designsystem.design_system 增加 bound_agent_id 列（AppCollab AC-P3）。
-- 需求（doc21 / 实施21 AC-P3）：与 deck DECKBIND 同模型——designsystem 属「主会话派发阵营」
-- （owner 选分身写简报 → 与该分身建主会话派发生成，协作可见于主聊天，非隔离 work_session）。
-- 绑定语义：生成它的分身即协作分身（save() bind-only-if-unbound 自动绑），owner 可二次确认改绑/解绑
-- （set_bound_agent）。云端权威 + 本地 SQLite 镜像（随 _ds_dict 双通道下行，端云经 server_id 映射）。
--
-- 幂等：可重复执行。

ALTER TABLE "hasn_designsystem"."design_system"
  ADD COLUMN IF NOT EXISTS "bound_agent_id" varchar(40);

COMMENT ON COLUMN "hasn_designsystem"."design_system"."bound_agent_id"
  IS '协作分身 HASN ID（owner 名下 a_* 分身，null=未绑定；生成它的分身，负责后续精修，改绑需二次确认）';
