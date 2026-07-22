-- 语音 catalog release 与内容寻址包的签名元数据快照（SPEECH-FULL-C1）。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn/hasn_speech_catalog_release_package.sql --app hasn --execute
-- 同一个模型权重对象可供多个平台项复用；每个平台项仍独立冻结许可证、来源和展开大小。

CREATE TABLE "public"."hasn_speech_catalog_release_package" (
  "id"             bigserial      PRIMARY KEY,
  "release_id"     bigint         NOT NULL REFERENCES "public"."hasn_speech_catalog_release" ("id") ON DELETE CASCADE,
  "package_id"     bigint         NOT NULL REFERENCES "public"."hasn_speech_package" ("id") ON DELETE RESTRICT,
  "model_id"       varchar(64)    NOT NULL,
  "model_version"  varchar(64)    NOT NULL,
  "os"             varchar(32)    NOT NULL,
  "arch"           varchar(32)    NOT NULL,
  "acceleration"   varchar(32)    NOT NULL,
  "installed_size" bigint         NOT NULL,
  "license_name"   varchar(128)   NOT NULL,
  "license_url"    varchar(1024)  NOT NULL,
  "source_url"     varchar(1024)  NOT NULL,
  "created_time"   timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"   timestamptz(6),
  CONSTRAINT "uq_speech_release_package_platform"
    UNIQUE ("release_id", "model_id", "model_version", "os", "arch", "acceleration"),
  CONSTRAINT "ck_speech_release_package_installed_size" CHECK ("installed_size" > 0)
);

CREATE INDEX "idx_speech_release_package_release"
  ON "public"."hasn_speech_catalog_release_package" ("release_id");
CREATE INDEX "idx_speech_release_package_package"
  ON "public"."hasn_speech_catalog_release_package" ("package_id");

COMMENT ON TABLE "public"."hasn_speech_catalog_release_package" IS '语音 release 平台包与签名元数据快照';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release_package"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release_package"."release_id" IS '所属不可变 catalog release';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release_package"."package_id" IS '引用的内容寻址模型包';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release_package"."model_id" IS '签名 catalog 中的稳定模型标识';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release_package"."model_version" IS '签名 catalog 中的模型版本';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release_package"."os" IS '目标操作系统';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release_package"."arch" IS '目标 CPU 架构';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release_package"."acceleration" IS '目标加速后端';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release_package"."installed_size" IS '签名声明的安装展开字节数';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release_package"."license_name" IS '签名声明的许可证名称';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release_package"."license_url" IS '签名声明的许可证全文 HTTPS URL';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release_package"."source_url" IS '签名声明的权威来源 HTTPS URL';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release_package"."created_time" IS '快照创建时间';
COMMENT ON COLUMN "public"."hasn_speech_catalog_release_package"."updated_time" IS '不可变快照保留字段，正常为空';
