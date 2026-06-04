-- =====================================================
-- 新增签名策略 qiniu_private（七牛私有空间下载凭证 e+token）。
--
-- 背景：私有 bucket(hasn) 经 CDN(hasn-cdn.dcfuture.cn) 交付时，七牛侧「回源鉴权」与
-- 「时间戳防盗链」互斥，且官方建议私有 bucket 用「回源鉴权」。此时终端访问凭证是
-- Kodo 私有下载 token(e+token)，而非 cdn_timestamp 的 sign/t —— 故新增 qiniu_private。
--
-- 同时修正两行配置：
--   1. 私有桶 hasn        → sign_strategy=qiniu_private（原 s3_presign 会输出难看的 S3 端点 URL）
--   2. 公共桶 hasn-pub    → 误配的 cdn_timestamp + 防盗链 key 清回默认（public 走 CDN 直读不签名，key 无用）
-- 关联 docs/hasn-node设计文档/80-执行与审计/07(D6/D8),09。
-- =====================================================
BEGIN;

COMMENT ON COLUMN "public"."s3_storage"."sign_strategy" IS '签名策略 (s3_presign:S3预签名:green/cdn_timestamp:CDN时间戳防盗链:cyan/qiniu_private:七牛私有下载凭证:blue/nginx_secure_link:Nginx防盗链:orange)';

UPDATE "public"."s3_storage" SET "sign_strategy" = 'qiniu_private' WHERE "bucket" = 'hasn' AND "access" = 'private';

UPDATE "public"."s3_storage" SET "sign_strategy" = 's3_presign', "remark" = NULL WHERE "bucket" = 'hasn-pub' AND "access" = 'public';

COMMIT;
