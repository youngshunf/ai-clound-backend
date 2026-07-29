-- 生成：
-- DATABASE_PORT=15432 uv run fba codegen generate \
--   --sql-file backend/sql/hasn/hasn_im_history_snapshots.sql \
--   --app hasn --execute

CREATE TABLE "public"."hasn_im_history_snapshots" (
    "id" uuid NOT NULL DEFAULT gen_random_uuid(),
    "owner_id" varchar(40) NOT NULL,
    "identity_ids" jsonb NOT NULL,
    "head_revision" bigint NOT NULL,
    "message_upper_bound" bigint NOT NULL DEFAULT 0,
    "conversation_count" integer NOT NULL DEFAULT 0,
    "message_count" integer NOT NULL DEFAULT 0,
    "history_complete" boolean NOT NULL DEFAULT false,
    "expires_time" timestamptz NOT NULL,
    "created_time" timestamptz NOT NULL DEFAULT now(),
    "updated_time" timestamptz,
    CONSTRAINT "pk_hasn_im_history_snapshots" PRIMARY KEY ("id"),
    CONSTRAINT "ck_hasn_im_history_snapshots_head_revision"
        CHECK ("head_revision" >= 0),
    CONSTRAINT "ck_hasn_im_history_snapshots_message_upper_bound"
        CHECK ("message_upper_bound" >= 0),
    CONSTRAINT "ck_hasn_im_history_snapshots_counts"
        CHECK ("conversation_count" >= 0 AND "message_count" >= 0)
);

COMMENT ON TABLE "public"."hasn_im_history_snapshots" IS
    '跨设备会话与消息历史物化快照';
COMMENT ON COLUMN "public"."hasn_im_history_snapshots"."owner_id" IS
    '快照所属主人 HASN ID';
COMMENT ON COLUMN "public"."hasn_im_history_snapshots"."identity_ids" IS
    '建立快照时主人本人及名下分身 HASN ID 集合';
COMMENT ON COLUMN "public"."hasn_im_history_snapshots"."head_revision" IS
    '建立快照前读取的主人增量同步流头';
COMMENT ON COLUMN "public"."hasn_im_history_snapshots"."message_upper_bound" IS
    '物化消息中的最大权威消息 ID';
COMMENT ON COLUMN "public"."hasn_im_history_snapshots"."conversation_count" IS
    '物化会话数量';
COMMENT ON COLUMN "public"."hasn_im_history_snapshots"."message_count" IS
    '物化消息数量';
COMMENT ON COLUMN "public"."hasn_im_history_snapshots"."history_complete" IS
    '所有会话均已证明历史完整';
COMMENT ON COLUMN "public"."hasn_im_history_snapshots"."expires_time" IS
    '快照服务端失效时间';

CREATE INDEX "idx_hasn_im_history_snapshots_owner_expiry"
    ON "public"."hasn_im_history_snapshots" ("owner_id", "expires_time");

CREATE TABLE "public"."hasn_im_history_snapshot_conversations" (
    "id" bigserial NOT NULL,
    "snapshot_id" uuid NOT NULL,
    "item_index" integer NOT NULL,
    "conversation_id" uuid NOT NULL,
    "payload" jsonb NOT NULL,
    "created_time" timestamptz NOT NULL DEFAULT now(),
    "updated_time" timestamptz,
    CONSTRAINT "pk_hasn_im_history_snapshot_conversations" PRIMARY KEY ("id"),
    CONSTRAINT "fk_hasn_im_history_snapshot_conversations_snapshot"
        FOREIGN KEY ("snapshot_id")
        REFERENCES "public"."hasn_im_history_snapshots" ("id")
        ON DELETE CASCADE,
    CONSTRAINT "uq_hasn_im_history_snapshot_conversations_index"
        UNIQUE ("snapshot_id", "item_index"),
    CONSTRAINT "uq_hasn_im_history_snapshot_conversations_source"
        UNIQUE ("snapshot_id", "conversation_id"),
    CONSTRAINT "ck_hasn_im_history_snapshot_conversations_index"
        CHECK ("item_index" > 0)
);

COMMENT ON TABLE "public"."hasn_im_history_snapshot_conversations" IS
    '跨设备历史快照的不可变会话投影';
COMMENT ON COLUMN "public"."hasn_im_history_snapshot_conversations"."snapshot_id" IS
    '所属物化快照 ID';
COMMENT ON COLUMN "public"."hasn_im_history_snapshot_conversations"."item_index" IS
    '快照内稳定分页序号';
COMMENT ON COLUMN "public"."hasn_im_history_snapshot_conversations"."conversation_id" IS
    '权威会话 UUID';
COMMENT ON COLUMN "public"."hasn_im_history_snapshot_conversations"."payload" IS
    'daemon 可直接消费的会话投影 JSON';

CREATE INDEX "idx_hasn_im_history_snapshot_conversations_page"
    ON "public"."hasn_im_history_snapshot_conversations"
    ("snapshot_id", "item_index");

CREATE TABLE "public"."hasn_im_history_snapshot_messages" (
    "id" bigserial NOT NULL,
    "snapshot_id" uuid NOT NULL,
    "item_index" integer NOT NULL,
    "message_id" bigint NOT NULL,
    "payload" jsonb NOT NULL,
    "created_time" timestamptz NOT NULL DEFAULT now(),
    "updated_time" timestamptz,
    CONSTRAINT "pk_hasn_im_history_snapshot_messages" PRIMARY KEY ("id"),
    CONSTRAINT "fk_hasn_im_history_snapshot_messages_snapshot"
        FOREIGN KEY ("snapshot_id")
        REFERENCES "public"."hasn_im_history_snapshots" ("id")
        ON DELETE CASCADE,
    CONSTRAINT "uq_hasn_im_history_snapshot_messages_index"
        UNIQUE ("snapshot_id", "item_index"),
    CONSTRAINT "uq_hasn_im_history_snapshot_messages_source"
        UNIQUE ("snapshot_id", "message_id"),
    CONSTRAINT "ck_hasn_im_history_snapshot_messages_index"
        CHECK ("item_index" > 0),
    CONSTRAINT "ck_hasn_im_history_snapshot_messages_message_id"
        CHECK ("message_id" > 0)
);

COMMENT ON TABLE "public"."hasn_im_history_snapshot_messages" IS
    '跨设备历史快照的不可变消息投影';
COMMENT ON COLUMN "public"."hasn_im_history_snapshot_messages"."snapshot_id" IS
    '所属物化快照 ID';
COMMENT ON COLUMN "public"."hasn_im_history_snapshot_messages"."item_index" IS
    '快照内稳定分页序号';
COMMENT ON COLUMN "public"."hasn_im_history_snapshot_messages"."message_id" IS
    '权威消息 ID';
COMMENT ON COLUMN "public"."hasn_im_history_snapshot_messages"."payload" IS
    'daemon 可直接消费的消息投影 JSON';

CREATE INDEX "idx_hasn_im_history_snapshot_messages_page"
    ON "public"."hasn_im_history_snapshot_messages"
    ("snapshot_id", "item_index");
