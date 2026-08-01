CREATE TABLE "public"."hasn_agent_channel_mirrors" (
  "id"                     bigserial PRIMARY KEY,
  "mirror_id"              varchar(40)  NOT NULL,
  "owner_id"               varchar(40)  NOT NULL,
  "agent_hasn_id"          varchar(40)  NOT NULL,
  "channel"                varchar(30)  NOT NULL,
  "origin_node_id"         varchar(40)  NOT NULL,
  "runtime_location"       varchar(50)  NOT NULL DEFAULT 'local',
  "status"                 varchar(30)  NOT NULL DEFAULT 'unbound',
  "bound_account_display"  varchar(128),
  "metadata_json"          jsonb        NOT NULL DEFAULT '{}',
  "last_error"             varchar(500),
  "created_time"           timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"           timestamptz(6) NOT NULL DEFAULT now(),
  CONSTRAINT "uq_hasn_agent_channel_mirrors_mirror" UNIQUE ("mirror_id"),
  CONSTRAINT "uq_hasn_agent_channel_mirrors_scope"
    UNIQUE ("owner_id", "agent_hasn_id", "channel", "origin_node_id")
);
CREATE INDEX "idx_hasn_agent_channel_mirrors_owner"
  ON "public"."hasn_agent_channel_mirrors" ("owner_id", "updated_time" DESC);
CREATE INDEX "idx_hasn_agent_channel_mirrors_agent"
  ON "public"."hasn_agent_channel_mirrors" ("agent_hasn_id", "channel");
CREATE INDEX "idx_hasn_agent_channel_mirrors_node"
  ON "public"."hasn_agent_channel_mirrors" ("origin_node_id", "updated_time" DESC);

COMMENT ON TABLE  "public"."hasn_agent_channel_mirrors" IS 'HASN Agent 渠道摘要跨设备镜像表（best-effort 可见性镜像，非操作代理；本表不持外键到 hermes_agent）';
COMMENT ON COLUMN "public"."hasn_agent_channel_mirrors"."mirror_id"             IS '镜像行业务主键（ULID/UUID 文本），唯一';
COMMENT ON COLUMN "public"."hasn_agent_channel_mirrors"."owner_id"              IS 'Owner hasn_id（数据隔离主键，所有查询强制过滤）';
COMMENT ON COLUMN "public"."hasn_agent_channel_mirrors"."agent_hasn_id"         IS 'Agent hasn_id，varchar(40) 对齐 hasn_agents.hasn_id';
COMMENT ON COLUMN "public"."hasn_agent_channel_mirrors"."channel"               IS '渠道类型 (feishu:飞书:blue/weixin:微信:green/qq:QQ:purple/wecom:企业微信:orange/webhook:Webhook:gray)';
COMMENT ON COLUMN "public"."hasn_agent_channel_mirrors"."origin_node_id"        IS '上报来源 Node ID（哪台设备的 daemon 上报）';
COMMENT ON COLUMN "public"."hasn_agent_channel_mirrors"."runtime_location"      IS '运行位置快照 (local:本地桌面端:blue/remote:远端:green)；cloud 已随 H8 云端沙箱形态退役，列保留供存量行读取';
COMMENT ON COLUMN "public"."hasn_agent_channel_mirrors"."status"                IS '渠道状态快照 (unbound:未绑:gray/bound:已绑:green/expired:过期:orange/failed:失败:red/unknown:未知:gray)';
COMMENT ON COLUMN "public"."hasn_agent_channel_mirrors"."bound_account_display" IS '脱敏绑定账号展示：飞书=昵称[@domain]/微信=昵称或****后4位/QQ=昵称或****后4位；禁原始 open_id/user_id/user_openid';
COMMENT ON COLUMN "public"."hasn_agent_channel_mirrors"."metadata_json"         IS '脱敏元数据；禁 SECRET_KEYS/_secret/_token，写库前过 _safe_json';
COMMENT ON COLUMN "public"."hasn_agent_channel_mirrors"."last_error"            IS '最近错误摘要（可空）';
COMMENT ON COLUMN "public"."hasn_agent_channel_mirrors"."created_time"          IS '创建时间';
COMMENT ON COLUMN "public"."hasn_agent_channel_mirrors"."updated_time"          IS '更新时间（daemon 每次 upsert 设为 now()，同时即「最近上报时间」语义）';
