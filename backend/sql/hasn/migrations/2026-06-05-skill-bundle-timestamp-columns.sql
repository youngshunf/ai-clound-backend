-- 技能包重新设计（doc15-技能市场/13 实施 B0.3）：hasn_skill_bundle 时间戳列正式迁移。
-- model 已是 created_time/updated_time（项目规范，timestamptz），但历史靠带明文口令的一次性脚本
-- RENAME，缺正式可重放迁移。此处补幂等 DO 块：存量库若仍是 create_time/update_time 则改名收敛；
-- 已改名或新建库（本就是 created_time/updated_time）则无操作。

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'hasn_skill_bundle' AND column_name = 'create_time') THEN
    ALTER TABLE public.hasn_skill_bundle RENAME COLUMN create_time TO created_time;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'hasn_skill_bundle' AND column_name = 'update_time') THEN
    ALTER TABLE public.hasn_skill_bundle RENAME COLUMN update_time TO updated_time;
  END IF;
END $$;
