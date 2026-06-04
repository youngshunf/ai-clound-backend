-- =====================================================
-- s3_storage 新增 access「访问类型」+ sign_strategy「签名策略」两列
-- access：公私分桶（public 走 CDN 直读不签名 / private 走签名），见 07 D1/D3。
-- sign_strategy：provider 无关的签名策略抽象（07 D8 存储可移植性），
--   换 provider/CDN 时签名器按此列分发，业务/资产引用零改动。
-- 关联 docs/hasn-node设计文档/80-执行与审计/07-S3对象存储统一服务与公私分桶签名方案.md
-- =====================================================
BEGIN;
ALTER TABLE "public"."s3_storage" ADD COLUMN IF NOT EXISTS "access" varchar(16) NOT NULL DEFAULT 'private';
ALTER TABLE "public"."s3_storage" ADD COLUMN IF NOT EXISTS "sign_strategy" varchar(24) NOT NULL DEFAULT 's3_presign';
COMMENT ON COLUMN "public"."s3_storage"."access" IS '访问类型 (public:公开:green/private:私有:orange)';
COMMENT ON COLUMN "public"."s3_storage"."sign_strategy" IS '签名策略 (cdn_timestamp:CDN时间戳防盗链:blue/s3_presign:S3预签名:green/nginx_secure_link:Nginx防盗链:orange)';

-- 注：既有七牛空间（bucket=hasn）实为「私有桶」，保持列默认 'private'，不改 public。
-- （初版曾误 UPDATE 为 public，已由 2026-06-04-s3-storage-fix-access-private.sql 修正回 private。）
-- 公开类资源（头像/帖图）经 sys/upload/image 走 storages[0]（与 access 无关 + CDN 直读），
-- 不受 access 影响；私信附件（dm_attachment）才按 access=private 命中并 s3_presign 签名。
COMMIT;
