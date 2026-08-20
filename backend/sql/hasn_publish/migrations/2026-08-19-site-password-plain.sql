-- Publish 站点口令明文回读列（主人查看口令 + 复制带口令分享链接）。
--
-- 产品裁决（2026-08-19）：分享口令是「防访客不防主人」的访问口令（类比网盘提取码），
-- 主人必须能在任一设备回读口令并一键复制带口令链接。bcrypt hash 仍承担访客解锁校验
-- （verify_unlock 不变），本列仅供 owner/agent 通道序列化回读；open/meta/hosting 面
-- 不经过 site_to_dict，明文绝不外露给访客。

ALTER TABLE "hasn_publish"."site"
  ADD COLUMN IF NOT EXISTS "password_plain" text;

COMMENT ON COLUMN "hasn_publish"."site"."password_plain"
  IS 'visibility=password 时的口令明文（仅 owner/agent 通道可回读，用于主人查看与复制带口令链接；访客面绝不返回，可空）';

COMMENT ON COLUMN "hasn_publish"."site"."password_hash"
  IS 'visibility=password 时的 bcrypt hash（访客解锁校验用；主人回读走 password_plain，可空）';
