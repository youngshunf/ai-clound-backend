-- =====================================================
-- 通用语音模型签名目录（云端权威·哑存储·单行下发）SPCAT-4
-- 一行权威（config_key='global'）承载「离线签名的语音模型 catalog」全文：
--   发布方离线持 Ed25519 私钥对 catalog 签名（hasn-node speech-catalog-tool），云端只做
--   「哑存储 + 下发」——不验签、不改写，daemon 持内置公钥自行验签才是安全执行点（同 hasn_release
--   minisign 哲学）。故 catalog_json 存**逐字节原文**（TEXT，非 JSONB）：daemon verify 时会
--   serde 反序列化 payload 再重算签名，任何字段增删/JSONB 归一都会破坏验签，绝不可解析后重序列化。
-- 模型 zip 包托管在公开桶（category=speech_model，长效 https 直链），URL 已内嵌进签名 catalog；
--   daemon 据 catalog 内 URL 无鉴权纯 GET 下载 + sha256 + 包级 Ed25519 双重校验。
-- 变更传播：revision = sha256(catalog_json 原文)[:16]，daemon 比对 revision 变化即重拉（KIND_SPEECH_CATALOG）。
-- 形态属「配置/元数据单行」（非实体 CRUD），沿用 hasn_platform_default_config 同类手写约定，
--   不走 4-scope fba 代码生成；节点 GET + CI 发布由 service/API 自定义。
-- 备注：无 seed 行——service 无行时返回空 catalog + 空 revision（daemon 保持「未装配」态，零 fake）；
--   首次 CI 发布即建行。
-- 设计事实源：docs/hasn-node设计文档/通用语音能力/ + SPCAT-4 云端生产管线。
-- =====================================================
CREATE TABLE IF NOT EXISTS "public"."hasn_speech_catalog" (
  "id"              bigserial      PRIMARY KEY,
  "config_key"      varchar(32)    NOT NULL DEFAULT 'global',
  "catalog_json"    text           NOT NULL DEFAULT '',
  "revision"        varchar(16)    NOT NULL DEFAULT '',
  "catalog_version" varchar(64)    NOT NULL DEFAULT '',
  "model_summary"   jsonb          NOT NULL DEFAULT '[]'::jsonb,
  "published_by"    varchar(64),
  "created_time"    timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"    timestamptz(6),
  CONSTRAINT "uq_speech_catalog_config_key" UNIQUE ("config_key")
);

COMMENT ON TABLE "public"."hasn_speech_catalog" IS '通用语音模型签名目录（云端权威·哑存储·单行下发）';
COMMENT ON COLUMN "public"."hasn_speech_catalog"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_speech_catalog"."config_key" IS '配置键（单行权威，恒 global）';
COMMENT ON COLUMN "public"."hasn_speech_catalog"."catalog_json" IS '离线签名的 catalog 逐字节原文（TEXT，daemon 验签用，绝不解析后重序列化）';
COMMENT ON COLUMN "public"."hasn_speech_catalog"."revision" IS 'catalog 原文指纹 sha256(catalog_json)[:16]，daemon 比对重拉';
COMMENT ON COLUMN "public"."hasn_speech_catalog"."catalog_version" IS 'catalog 内声明的版本号（展示/回滚判定用，非权威）';
COMMENT ON COLUMN "public"."hasn_speech_catalog"."model_summary" IS '模型摘要（model_id/version/包平台，仅管理端展示，非权威）';
COMMENT ON COLUMN "public"."hasn_speech_catalog"."published_by" IS '最后发布方标识（CI/发布者标签）';
COMMENT ON COLUMN "public"."hasn_speech_catalog"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_speech_catalog"."updated_time" IS '更新时间';
