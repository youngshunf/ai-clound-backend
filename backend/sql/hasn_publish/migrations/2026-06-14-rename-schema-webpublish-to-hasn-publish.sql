-- 迁移：通用网页发布 PG schema 重命名 webpublish → hasn_publish（ADR-15：AI-Native 应用统一 hasn_ 前缀）
-- 模块目录 app/publish → app/hasn_publish，PG schema webpublish → hasn_publish。
-- app_id / catalog / URL（/api/v1/publish/*）保持 publish 不变。
-- 旧 schema 名 webpublish 是历史命名不对称（app_id=publish 但 schema=webpublish），本次统一为 hasn_publish。
-- 幂等：仅当旧 schema "webpublish" 存在且新 schema "hasn_publish" 不存在时才 RENAME；
--       全新库（新建表脚本已落 hasn_publish）/ 已迁移库 → 自动跳过，无副作用。
-- ⚠️ 破坏性 DDL：生产执行须在停机/低峰窗口，先全库备份。
-- 执行：psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 本文件

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'webpublish')
       AND NOT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'hasn_publish') THEN
        ALTER SCHEMA "webpublish" RENAME TO "hasn_publish";
        RAISE NOTICE 'schema webpublish → hasn_publish 重命名完成';
    ELSE
        RAISE NOTICE 'schema webpublish 不存在或 hasn_publish 已存在，跳过（幂等）';
    END IF;
END $$;
