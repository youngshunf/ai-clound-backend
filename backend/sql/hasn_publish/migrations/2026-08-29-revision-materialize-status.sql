-- Publish revision 物化状态列（bundle-zip 发布异步化）。
--
-- 背景（2026-08-29 生产事故定案）：POST /api/v1/publish/*/sites 在请求内同步做 bundle-zip
-- 物化（读 zip + 逐对象串行 PUT 对象存储），21MB 包实测 38-39s，超过 daemon reqwest 写死的
-- 30s 总超时 → 客户端 499 放弃、服务端 200 落库、发布结果丢失；非幂等重试一晚重复发布 4 次。
-- 本次把 bundle-zip 物化挪进 Celery：请求内只落 site + pending revision 立即返回，worker
-- 完成 fan-out 后回写 manifest.files、置 ready 并翻转 site.current_revision_id。
--
-- 存量行全部是「请求内已同步物化完成」的历史行，DEFAULT 'ready' 回填即为真相（PG11+
-- ADD COLUMN 常量默认值仅改元数据，不重写表）。

ALTER TABLE "hasn_publish"."revision"
  ADD COLUMN IF NOT EXISTS "materialize_status" varchar(16) NOT NULL DEFAULT 'ready';

ALTER TABLE "hasn_publish"."revision"
  ADD COLUMN IF NOT EXISTS "materialize_error" text;

COMMENT ON COLUMN "hasn_publish"."revision"."materialize_status"
  IS '物化状态（ready:已物化/pending:bundle-zip 物化在途/Celery/failed:物化失败；仅 bundle-zip 会出现非 ready）';
COMMENT ON COLUMN "hasn_publish"."revision"."materialize_error"
  IS '物化失败的主人可读原因（materialize_status=failed 时非空，可空）';

-- 滞留兜底 sweep 的查询面：pending + created_time 早于宽限期
CREATE INDEX IF NOT EXISTS "idx_revision_materialize_pending"
  ON "hasn_publish"."revision" ("created_time")
  WHERE "materialize_status" = 'pending' AND "deleted_time" IS NULL;
