-- =====================================================
-- 通用 LLM 裁判判定记录表（doc07 §5.3）
-- 三层漏斗 L2 裁判端点每次判定成功后落一行：教师标签 + 可观测。
-- 全 kind 共表（judge_kind 区分 termination/disclosure/后续扩展位）。
-- 追加即写、不改行；updated_time 仅为对齐 codegen 基模型保留（恒为空）。
-- =====================================================
CREATE TABLE "public"."hasn_judge_verdict" (
  "id"               bigserial PRIMARY KEY,
  "judge_kind"       varchar(32) NOT NULL,
  "owner_hasn_id"    varchar(40) NOT NULL,
  "agent_hasn_id"    varchar(40) NOT NULL,
  "peer_hasn_id"     varchar(40) NOT NULL,
  "conversation_ref" varchar(64) NOT NULL,
  "input_json"       jsonb NOT NULL DEFAULT '{}',
  "verdict_json"     jsonb NOT NULL DEFAULT '{}',
  "model"            varchar(100),
  "latency_ms"       integer,
  "created_time"     timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"     timestamptz(6)
);

CREATE INDEX "idx_judge_verdict_kind" ON "public"."hasn_judge_verdict" ("judge_kind", "created_time");
CREATE INDEX "idx_judge_verdict_owner" ON "public"."hasn_judge_verdict" ("owner_hasn_id", "created_time");

COMMENT ON TABLE "public"."hasn_judge_verdict" IS '通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）';
COMMENT ON COLUMN "public"."hasn_judge_verdict"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_judge_verdict"."judge_kind" IS '裁判类型 (termination:会话终止:blue/disclosure:隐私披露:green)';
COMMENT ON COLUMN "public"."hasn_judge_verdict"."owner_hasn_id" IS '发起方分身所属主人 hasn_id（凭据/计费归属）';
COMMENT ON COLUMN "public"."hasn_judge_verdict"."agent_hasn_id" IS '发起方分身 hasn_id';
COMMENT ON COLUMN "public"."hasn_judge_verdict"."peer_hasn_id" IS '对端 hasn_id（人或分身）';
COMMENT ON COLUMN "public"."hasn_judge_verdict"."conversation_ref" IS 'daemon 本地会话 id，仅溯源元数据，不作资源解析（URI 铁律豁免范围）';
COMMENT ON COLUMN "public"."hasn_judge_verdict"."input_json" IS '脱敏后裁判输入（transcript/正文+上下文；L1 命中片段以 PartialMask 形态入库，不存附件/原文）';
COMMENT ON COLUMN "public"."hasn_judge_verdict"."verdict_json" IS '裁判出参 JSON（kind 专属：termination={should_end,reason}；disclosure={allow,categories,reason}）';
COMMENT ON COLUMN "public"."hasn_judge_verdict"."model" IS '实际命中的裁判模型名';
COMMENT ON COLUMN "public"."hasn_judge_verdict"."latency_ms" IS 'LLM 调用耗时（毫秒）';
COMMENT ON COLUMN "public"."hasn_judge_verdict"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_judge_verdict"."updated_time" IS '更新时间（append-only，恒空）';
