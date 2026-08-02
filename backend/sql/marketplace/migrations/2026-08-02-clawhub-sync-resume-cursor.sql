-- ClawHub 目录分页断点：只在对应页面技能批次已提交后推进。
ALTER TABLE hasn_marketplace.marketplace_sync_log
    ADD COLUMN IF NOT EXISTS resume_cursor text;

-- statement-breakpoint
COMMENT ON COLUMN hasn_marketplace.marketplace_sync_log.resume_cursor
    IS '最后一个已提交目录批次后的续跑 cursor；NULL 表示从起点开始';
