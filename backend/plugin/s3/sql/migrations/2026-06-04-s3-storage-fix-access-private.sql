-- =====================================================
-- 修正：既有七牛空间（bucket=hasn）实为「私有桶」，
-- 2026-06-02 迁移误将其标为 public，导致 dm_attachment（私信附件，私有桶）
-- 上传时 _pick_storage('private') 找不到存储行 → 云端 500「未配置 access=private」。
--
-- 公开类资源（头像/帖图）经 sys/upload/image 走 storages[0]（与 access 无关，
-- 由 file_ops 直传 + CDN 直读），故本行改 private 不影响公开类上传；
-- 而私信附件改为正确命中此私有行 + s3_presign 签名。
-- 关联 docs/hasn-node设计文档/80-执行与审计/07,09。
-- =====================================================
BEGIN;
UPDATE "public"."s3_storage" SET "access" = 'private' WHERE "bucket" = 'hasn';
COMMIT;
