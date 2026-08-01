-- =====================================================
-- 无头 hasn-node 托管 · 托管事件流水（H4）
-- 契约：docs/hasn-node设计文档/云端节点托管/实施/01-切片实施契约(H1-H8).md §2.3
-- 只追加不修改；hosting-agent 与云端各自的关键动作都在此留痕，便于事后归因。
-- =====================================================
CREATE TABLE "public"."hasn_cloud_node_events" (
  "id"            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "cloud_node_id" uuid NOT NULL,
  "node_id"       varchar(40) NOT NULL,
  "event_type"    varchar(32) NOT NULL,
  "detail"        jsonb NOT NULL DEFAULT '{}',
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6)
);

CREATE INDEX "idx_hasn_cloud_node_events_node" ON "public"."hasn_cloud_node_events" ("node_id", "created_time" DESC);
CREATE INDEX "idx_hasn_cloud_node_events_cloud_node" ON "public"."hasn_cloud_node_events" ("cloud_node_id");

COMMENT ON TABLE "public"."hasn_cloud_node_events" IS '云端托管节点事件流水';
COMMENT ON COLUMN "public"."hasn_cloud_node_events"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_cloud_node_events"."cloud_node_id" IS '关联 hasn_cloud_nodes.id';
COMMENT ON COLUMN "public"."hasn_cloud_node_events"."node_id" IS '节点 node_id（冗余，便于按设备直查）';
COMMENT ON COLUMN "public"."hasn_cloud_node_events"."event_type" IS '事件类型 (created:已创建:blue/started:已启动:green/stopped:已停止:gray/updated:已更新:cyan/update_failed:更新失败:red/rolled_back:已回滚:orange/reauthorized:已重新授权:purple/deleted:已删除:gray/backup:已备份:green/failed:失败:red)';
COMMENT ON COLUMN "public"."hasn_cloud_node_events"."detail" IS '事件明细 JSON';
COMMENT ON COLUMN "public"."hasn_cloud_node_events"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_cloud_node_events"."updated_time" IS '更新时间';
