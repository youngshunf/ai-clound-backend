-- 积分包配置表
CREATE TABLE "public"."credit_package" (
  "id" bigserial PRIMARY KEY,
  "package_name" varchar(64) COLLATE "pg_catalog"."default" NOT NULL,
  "credits" numeric(15, 2) NOT NULL,
  "price" numeric(10, 2) NOT NULL,
  "bonus_credits" numeric(15, 2) NOT NULL DEFAULT 0,
  "description" varchar(512) COLLATE "pg_catalog"."default",
  "enabled" bool NOT NULL DEFAULT true,
  "sort_order" int4 NOT NULL DEFAULT 0,
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6)
);

COMMENT ON COLUMN "public"."credit_package"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."credit_package"."package_name" IS '积分包名称';
COMMENT ON COLUMN "public"."credit_package"."credits" IS '基础积分数量';
COMMENT ON COLUMN "public"."credit_package"."price" IS '价格';
COMMENT ON COLUMN "public"."credit_package"."bonus_credits" IS '额外赠送积分';
COMMENT ON COLUMN "public"."credit_package"."description" IS '描述';
COMMENT ON COLUMN "public"."credit_package"."enabled" IS '是否启用';
COMMENT ON COLUMN "public"."credit_package"."sort_order" IS '排序权重';
COMMENT ON TABLE "public"."credit_package" IS '积分包配置表';
