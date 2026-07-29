-- 文集社会化指标：订阅数由订阅写事务同步维护；阅读数由服务端真实阅读入口维护。
ALTER TABLE "hasn_community"."hasn_doc_spaces"
  ADD COLUMN IF NOT EXISTS "subscribe_count" integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS "view_count" integer NOT NULL DEFAULT 0;

COMMENT ON COLUMN "hasn_community"."hasn_doc_spaces"."subscribe_count" IS '当前有效订阅者数';
COMMENT ON COLUMN "hasn_community"."hasn_doc_spaces"."view_count" IS '通过文集阅读入口累计的真实阅读次数';
