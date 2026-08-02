-- =====================================================
-- 模型注册表（云端权威的「模型是什么」）
--
-- 事实源分工：new-api `/api/pricing` 说「有什么」（清单 + 相对成本 + 分组 + 供应商），
-- 本表说「是什么」（能力类别 / 输入要求 / 方言 / 质量档 / 场景 / 可见性 / 排序），
-- PDC 说「用哪个」。三者不重叠——PDC 里再抄一份清单必然漂移，2026-08-02 线上视频
-- 全线失败（配的 `agnes-2.0-video` 网关上真名是 `agnes-video-v2.0`）即此根因。
--
-- 同步语义（绝不删行）：按 `model_name` upsert；新模型 `capability='unclassified'` +
-- `agent_visible=false`（能力猜不出来，猜错会让分身把文生图请求发给 TTS 模型）；既有模型
-- 只覆盖 new-api 那几列，人工标注列一律不动；本轮没出现的标 `upstream_status='missing'`
-- 但保留行——渠道临时下线是常态，删了人工标注就得重标。
--
-- 设计事实源：docs/hasn-node设计文档/运行时配置下发/02-模型注册表与语义标注下发设计.md §4
-- =====================================================
CREATE TABLE IF NOT EXISTS "public"."hasn_model_registry" (
  "id"                 bigserial      PRIMARY KEY,
  "model_name"         varchar(128)   NOT NULL,
  "capability"         varchar(32)    NOT NULL DEFAULT 'unclassified',
  "inputs"             jsonb          NOT NULL DEFAULT '{}'::jsonb,
  "dialect"            varchar(32),
  "quality"            varchar(16),
  "scenario"           text,
  "agent_visible"      boolean        NOT NULL DEFAULT false,
  "sort_order"         integer        NOT NULL DEFAULT 0,
  "vendor_name"        varchar(64),
  "relative_cost"      numeric(12, 4),
  "cost_extra"         jsonb          NOT NULL DEFAULT '{}'::jsonb,
  "cost_tier_override" varchar(16),
  "enable_groups"      jsonb          NOT NULL DEFAULT '[]'::jsonb,
  "upstream_status"    varchar(16)    NOT NULL DEFAULT 'active',
  "last_synced_time"   timestamptz(6),
  "created_time"       timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"       timestamptz(6)
);

-- 模型名是同步键：一个网关模型名只允许一行（upsert 依赖它）
CREATE UNIQUE INDEX IF NOT EXISTS "uq_hasn_model_registry_model_name"
  ON "public"."hasn_model_registry" ("model_name");
-- 下发按能力类别分组查询
CREATE INDEX IF NOT EXISTS "idx_hasn_model_registry_capability"
  ON "public"."hasn_model_registry" ("capability");
-- 下发只取网关上还在的（missing 的不参与下发，仅在 Admin 高亮）
CREATE INDEX IF NOT EXISTS "idx_hasn_model_registry_upstream_status"
  ON "public"."hasn_model_registry" ("upstream_status");

COMMENT ON TABLE "public"."hasn_model_registry" IS '模型注册表（new-api 供事实、云端补语义、一处维护全平台下发）';
COMMENT ON COLUMN "public"."hasn_model_registry"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_model_registry"."model_name" IS '网关上的模型名（同步键，全局唯一）';
COMMENT ON COLUMN "public"."hasn_model_registry"."capability" IS '能力类别 (chat:对话:blue/vision:视觉理解:blue/image_generate:文生图:green/image_edit:图像编辑:green/tts:语音合成:purple/stt:语音识别:purple/video:视频生成:orange/embedding:向量化:gray/rerank:重排:gray/unclassified:待标注:red)';
COMMENT ON COLUMN "public"."hasn_model_registry"."inputs" IS '输入要求表，每种输入取 required/optional/unsupported，省略即 unsupported；text 恒为必需不写';
COMMENT ON COLUMN "public"."hasn_model_registry"."dialect" IS '入参方言 (openai:OpenAI 兼容:blue/ali:阿里通义万相:orange)';
COMMENT ON COLUMN "public"."hasn_model_registry"."quality" IS '质量档 (draft:草稿:gray/standard:标准:blue/high:高质量:green)';
COMMENT ON COLUMN "public"."hasn_model_registry"."scenario" IS '适用场景一句话（给分身选型看）';
COMMENT ON COLUMN "public"."hasn_model_registry"."agent_visible" IS '是否对分身暴露（新同步进来的默认关闭，标注后再放开）';
COMMENT ON COLUMN "public"."hasn_model_registry"."sort_order" IS '同能力内的推荐顺序（failover 优先级，小的在前）';
COMMENT ON COLUMN "public"."hasn_model_registry"."vendor_name" IS '供应商显示名（来自 new-api，如阿里巴巴/DeepSeek）';
COMMENT ON COLUMN "public"."hasn_model_registry"."relative_cost" IS 'new-api model_ratio 快照，仅内部/Admin 可见，用于算档位与运维核对，绝不下发';
COMMENT ON COLUMN "public"."hasn_model_registry"."cost_extra" IS 'new-api 其它计费参数原样留档（image_ratio/completion_ratio/quota_type 等），不下发';
COMMENT ON COLUMN "public"."hasn_model_registry"."cost_tier_override" IS '人工覆盖价格档位 (economy:经济:green/standard:标准:blue/premium:高价:orange)，留空即用算出来的';
COMMENT ON COLUMN "public"."hasn_model_registry"."enable_groups" IS '可用分组（来自 new-api enable_groups）';
COMMENT ON COLUMN "public"."hasn_model_registry"."upstream_status" IS '网关状态 (active:网关上可用:green/missing:网关上已消失:red)';
COMMENT ON COLUMN "public"."hasn_model_registry"."last_synced_time" IS '最近一次在网关上被看到的时间';
COMMENT ON COLUMN "public"."hasn_model_registry"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_model_registry"."updated_time" IS '更新时间';
