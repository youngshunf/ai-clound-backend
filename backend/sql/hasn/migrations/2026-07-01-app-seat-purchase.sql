-- 应用与空间关系及企业席位购买（doc04 实施清单 P0-1）
-- 事实源：docs/hasn-node设计文档/12-企业与组织/04-应用与空间关系及企业席位购买设计.md §6.1
--
-- 两处只增列（不 codegen，直接改 model + 本迁移）：
--   ① hasn_app_catalog.purchasable_by —— 谁能买单（owner/enterprise/both），
--      仅对 access_type=purchase 有约束意义（拦「个人买纯企业应用」）。保守默认 owner：
--      新应用须显式开启企业购买。
--   ② hasn_app_entitlement.seats_total —— 企业席位制权益的席位总数；owner 主体恒 NULL。
--
-- 幂等：ADD COLUMN IF NOT EXISTS + COMMENT 可反复执行；存量回填用 WHERE 收敛不覆盖已改值。

ALTER TABLE "public"."hasn_app_catalog"
    ADD COLUMN IF NOT EXISTS "purchasable_by" varchar(16) NOT NULL DEFAULT 'owner';
COMMENT ON COLUMN "public"."hasn_app_catalog"."purchasable_by" IS
    '谁能买单 (owner:仅个人/enterprise:仅企业/both:双模)；仅 access_type=purchase 有约束意义';

ALTER TABLE "public"."hasn_app_entitlement"
    ADD COLUMN IF NOT EXISTS "seats_total" integer;
COMMENT ON COLUMN "public"."hasn_app_entitlement"."seats_total" IS
    '席位总数(subject_type=enterprise 席位制有效; owner 恒 null)';

-- 存量回填 purchasable_by（按现状 scope 形态；只刷仍是保守默认 owner 的行，不覆盖将来手工设定）：
--   纯企业 scope=[enterprise]        → enterprise（个人买不了）
--   双模   scope 含 enterprise+personal → both
--   纯个人 scope=[personal] / 其它     → owner（默认，无需动）
UPDATE "public"."hasn_app_catalog"
   SET "purchasable_by" = 'enterprise'
 WHERE "purchasable_by" = 'owner'
   AND "scope" @> '["enterprise"]'::jsonb
   AND NOT ("scope" @> '["personal"]'::jsonb);

UPDATE "public"."hasn_app_catalog"
   SET "purchasable_by" = 'both'
 WHERE "purchasable_by" = 'owner'
   AND "scope" @> '["enterprise"]'::jsonb
   AND "scope" @> '["personal"]'::jsonb;
