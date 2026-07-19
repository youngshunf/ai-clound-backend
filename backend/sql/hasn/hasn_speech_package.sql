-- 通用语音模型包内容寻址登记表（SPEECH-FULL-C1）。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn/hasn_speech_package.sql --app hasn --execute
-- 对象 key 只由服务端根据实际上传字节的 SHA-256 派生；同摘要幂等，不允许调用方指定可变 key。

CREATE TABLE "public"."hasn_speech_package" (
  "id"           bigserial      PRIMARY KEY,
  "sha256"       varchar(64)    NOT NULL,
  "storage_id"   bigint         NOT NULL REFERENCES "public"."s3_storage" ("id") ON DELETE RESTRICT,
  "object_key"   varchar(1024)  NOT NULL,
  "size"         bigint         NOT NULL,
  "content_type" varchar(128)   NOT NULL DEFAULT 'application/zip',
  "created_time" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time" timestamptz(6),
  CONSTRAINT "uq_speech_package_sha256" UNIQUE ("sha256"),
  CONSTRAINT "uq_speech_package_object_key" UNIQUE ("object_key"),
  CONSTRAINT "ck_speech_package_sha256" CHECK ("sha256" ~ '^[0-9a-f]{64}$'),
  CONSTRAINT "ck_speech_package_size" CHECK ("size" > 0)
);

COMMENT ON TABLE "public"."hasn_speech_package" IS '语音模型不可变内容寻址包登记';
COMMENT ON COLUMN "public"."hasn_speech_package"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_speech_package"."sha256" IS '上传原始字节的规范小写 SHA-256，全局唯一';
COMMENT ON COLUMN "public"."hasn_speech_package"."storage_id" IS '实际承载对象的公共 S3 存储 ID';
COMMENT ON COLUMN "public"."hasn_speech_package"."object_key" IS '由 SHA-256 派生的不可变对象 key';
COMMENT ON COLUMN "public"."hasn_speech_package"."size" IS '对象字节数';
COMMENT ON COLUMN "public"."hasn_speech_package"."content_type" IS '对象媒体类型，模型包固定为 application/zip';
COMMENT ON COLUMN "public"."hasn_speech_package"."created_time" IS '首次暂存时间';
COMMENT ON COLUMN "public"."hasn_speech_package"."updated_time" IS '登记更新时间；内容字段不可变';
