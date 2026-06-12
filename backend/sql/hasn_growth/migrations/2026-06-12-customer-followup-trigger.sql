-- 迁移：customer 加 last_followup_trigger_at（J3 即时跟进 run_now 的云端侧 10 分钟防抖状态）。
--
-- 背景：M6 J3 事件驱动 run_now——客户回复 inbound 落库后，若 customer.followup_task_id 已绑定跟进任务，
-- 云端复用 hasn_task run_now 接缝（置 next_run_at=now，由持有 runtime 的节点本地 tick 拾取）触发即时跟进。
-- 防抖：同客户 10 分钟窗口仅触发一次（云端侧窗口合并），用本列记录上次触发时刻。
--
-- 幂等：列不存在才 ADD。

SET search_path TO hasn_growth, public;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'hasn_growth' AND table_name = 'customer'
          AND column_name = 'last_followup_trigger_at'
    ) THEN
        ALTER TABLE hasn_growth.customer ADD COLUMN last_followup_trigger_at timestamptz NULL;
        RAISE NOTICE 'customer.last_followup_trigger_at ADD COLUMN';
    END IF;
END $$;
