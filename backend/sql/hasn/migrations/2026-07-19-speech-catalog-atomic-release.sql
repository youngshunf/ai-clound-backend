-- 将语音 catalog 单行权威升级为不可变 release head（SPEECH-FULL-C1）。
-- 幂等：字段、索引和外键均可重复执行；既有 legacy catalog 保留原文，但在首个 v2 原子发布前没有 release head。

ALTER TABLE "public"."hasn_speech_catalog"
  ADD COLUMN IF NOT EXISTS "current_release_id" bigint,
  ADD COLUMN IF NOT EXISTS "release_sequence" numeric(20, 0),
  ADD COLUMN IF NOT EXISTS "key_id" varchar(64);

CREATE INDEX IF NOT EXISTS "idx_speech_catalog_current_release"
  ON "public"."hasn_speech_catalog" ("current_release_id")
  WHERE "current_release_id" IS NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'fk_speech_catalog_current_release'
      AND conrelid = 'public.hasn_speech_catalog'::regclass
  ) THEN
    ALTER TABLE "public"."hasn_speech_catalog"
      ADD CONSTRAINT "fk_speech_catalog_current_release"
      FOREIGN KEY ("current_release_id")
      REFERENCES "public"."hasn_speech_catalog_release" ("id")
      ON DELETE RESTRICT;
  END IF;
END
$$;

COMMENT ON COLUMN "public"."hasn_speech_catalog"."current_release_id" IS '当前权威不可变 release ID';
COMMENT ON COLUMN "public"."hasn_speech_catalog"."release_sequence" IS '当前权威全目录单调 u64 发布序列';
COMMENT ON COLUMN "public"."hasn_speech_catalog"."key_id" IS '当前 release 的签名公钥稳定标识';
