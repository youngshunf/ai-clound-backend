-- 迁移：演示文稿 PG schema 重命名 deck → hasn_deck（ADR-15：AI-Native 应用统一 hasn_ 前缀）
-- 模块目录 app/deck → app/hasn_deck，PG schema deck → hasn_deck。
-- app_id / catalog / URL（/api/v1/deck/*）保持 deck 不变。
-- 幂等：仅当旧 schema "deck" 存在且新 schema "hasn_deck" 不存在时才 RENAME；
--       全新库（新建表脚本已落 hasn_deck）/ 已迁移库 → 自动跳过，无副作用。
-- ⚠️ 破坏性 DDL：生产执行须在停机/低峰窗口，先全库备份。
-- 执行：psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 本文件

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'deck')
       AND NOT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'hasn_deck') THEN
        ALTER SCHEMA "deck" RENAME TO "hasn_deck";
        RAISE NOTICE 'schema deck → hasn_deck 重命名完成';
    ELSE
        RAISE NOTICE 'schema deck 不存在或 hasn_deck 已存在，跳过（幂等）';
    END IF;
END $$;
