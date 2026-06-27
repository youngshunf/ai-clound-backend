-- APPBETA-1：hasn_app_catalog 增加「发布阶段（内测）」+「自定义角标」三列。
--
-- 背景（福仔 2026-06-27）：应用中心需要给应用加状态与角标——
--   1) 内测状态：全量内测（人人可见可用、带内测标）/ 灰度内测（仅被邀请或申请且通过审批的用户可见可打开）；
--   2) 自定义角标：热门 / 推荐 / 限免 等任意文字 + 自定义颜色；
--   3) 排序：sort_order 已存在且 list_published_catalog 已按它升序——本迁移不动。
--
-- 设计要点：release_phase 与现有 status（上架/下架/草稿）**正交**——内测是「发布阶段」不是「上架状态」。
--   灰度/全量内测应用仍需 status='published' 才进列表；灰度门控在 resolve_app_access 里按
--   hasn_app_beta_access 判定（见 2026-06-27-app-beta-access 表）。
--
-- 幂等：IF NOT EXISTS，可重复执行。

ALTER TABLE "public"."hasn_app_catalog"
    ADD COLUMN IF NOT EXISTS "release_phase" varchar(16) NOT NULL DEFAULT 'ga';
ALTER TABLE "public"."hasn_app_catalog"
    ADD COLUMN IF NOT EXISTS "badge_text" varchar(32);
ALTER TABLE "public"."hasn_app_catalog"
    ADD COLUMN IF NOT EXISTS "badge_color" varchar(16);

COMMENT ON COLUMN "public"."hasn_app_catalog"."release_phase" IS
    '发布阶段 (ga:正式:green/beta_full:全量内测:blue/beta_gray:灰度内测:orange)';
COMMENT ON COLUMN "public"."hasn_app_catalog"."badge_text" IS
    '自定义角标文字（如 热门/推荐/限免；空=无角标）';
COMMENT ON COLUMN "public"."hasn_app_catalog"."badge_color" IS
    '角标颜色 hex（如 #10B981；空=品牌默认紫）';
