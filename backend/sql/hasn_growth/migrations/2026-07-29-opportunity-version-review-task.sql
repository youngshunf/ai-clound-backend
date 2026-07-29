-- S9：商机并发版本与成交/流失复盘任务引用。
SET search_path TO hasn_growth, public;

ALTER TABLE opportunity
    ADD COLUMN IF NOT EXISTS version bigint NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS review_task_id varchar(64);

UPDATE opportunity
SET version = 1
WHERE version IS NULL OR version < 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_growth_opportunity_version'
          AND conrelid = 'hasn_growth.opportunity'::regclass
    ) THEN
        ALTER TABLE opportunity
            ADD CONSTRAINT ck_growth_opportunity_version CHECK (version >= 1);
    END IF;
END
$$;

COMMENT ON COLUMN opportunity.version IS '并发控制版本；每次阶段变化或关闭单调递增';
COMMENT ON COLUMN opportunity.review_task_id IS '成交或流失后幂等创建的复盘任务 UUID';
