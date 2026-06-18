-- 演示文稿协作分身绑定：hasn_deck.deck 增加 bound_agent_id 列（DECKBIND）
-- 需求：首页创建即由分身生成并与该分身绑定；下次协作自动指定由该分身修改，改绑需二次确认。
-- 设计：绑定是 owner 概念（绑定的总是 owner 自己名下分身 a_*），云端权威 + 本地 SQLite 镜像，
--   随 deck create/update 双向同步（端云经 server_id 映射，同 deck 其它字段）。
--
-- 幂等：可重复执行。

ALTER TABLE "hasn_deck"."deck"
  ADD COLUMN IF NOT EXISTS "bound_agent_id" varchar(40);

COMMENT ON COLUMN "hasn_deck"."deck"."bound_agent_id"
  IS '协作分身 HASN ID（owner 名下 a_* 分身，null=未绑定；负责后续生成/精修，改绑需二次确认）';
