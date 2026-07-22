-- 通用语音 catalog 不可变 release 历史（SPEECH-FULL-C1）。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn/hasn_speech_catalog_release.sql --app hasn --execute
-- 每次回滚也写入更高 release_sequence 的新行；旧行与旧包永久保留，不重放旧 catalog。

CREATE TABLE "public"."hasn_speech_catalog_release" (
  "id"               bigserial       PRIMARY KEY,
  "revision"         varchar(16)     NOT NULL,
  "release_sequence" numeric(20, 0)  NOT NULL,
  "key_id"           varchar(64)     NOT NULL,
  "catalog_version"  varchar(64)     NOT NULL,
  "expires_at"       timestamptz(6)  NOT NULL,
  "catalog_json"     text            NOT NULL,
  "model_summary"    jsonb           NOT NULL DEFAULT '[]'::jsonb,
  "published_by"     varchar(64),
  "created_time"     timestamptz(6)  NOT NULL DEFAULT now(),
  "updated_time"     timestamptz(6),
  CONSTRAINT "uq_speech_catalog_release_revision" UNIQUE ("revision"),
  CONSTRAINT "uq_speech_catalog_release_sequence" UNIQUE ("release_sequence"),
  CONSTRAINT "ck_speech_catalog_release_sequence" CHECK (
    "release_sequence" > 0 AND "release_sequence" <= 18446744073709551615
  )
);

COMMENT ON TABLE "public"."hasn_speech_catalog_release" IS '语音签名 catalog 不可变发布历史';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release"."revision" IS 'catalog 原文 SHA-256 前 16 位';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release"."release_sequence" IS '全目录单调 u64 发布序列';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release"."key_id" IS '签名信任环中的稳定公钥标识';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release"."catalog_version" IS '签名正文中的目录版本';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release"."expires_at" IS '发布信封失效时间';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release"."catalog_json" IS '离线签名 catalog 逐字节原文';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release"."model_summary" IS '管理展示用模型摘要，非验签权威';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release"."published_by" IS '发布方审计标识';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release"."created_time" IS '发布时间';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release"."updated_time" IS '不可变 release 保留字段，正常为空';
