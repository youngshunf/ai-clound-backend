-- 为语音内容寻址包补充对象版本证据，支持锁外完整哈希、锁内快速版本复核。
-- 存量行保持 NULL，必须重新暂存并完成真实 SHA-256 复核后才能进入新 release。

ALTER TABLE "public"."hasn_speech_package"
  ADD COLUMN IF NOT EXISTS "object_etag" varchar(256);

COMMENT ON COLUMN "public"."hasn_speech_package"."object_etag"
  IS '完整 SHA-256 复核时对应的对象存储不可变版本标识';
