-- =====================================================
-- hasn_task.task 增 builtin_key / builtin_synced_revision + 去重唯一索引
-- 设计：docs/hasn-node设计文档/12-任务系统实施方案/08-内置定时任务体系与内置Agent设计.md §4.4
--
-- 内置任务直接写进 hasn_task.task（INSERT-only），不另建表：
--   builtin_key            = catalog.builtin_key（用户任务为 NULL，作去重键）
--   builtin_synced_revision = INSERT 时 = catalog.revision；用户手动更新时追平最新
--                             （用于「可更新」检测：catalog.revision > builtin_synced_revision）
-- 去重唯一索引：一个 owner 同一 builtin_key 只允许一行存活 → 支撑 INSERT-only「已存在则跳过」。
-- 幂等。
-- =====================================================
ALTER TABLE "hasn_task"."task"
  ADD COLUMN IF NOT EXISTS "builtin_key" varchar(64);
COMMENT ON COLUMN "hasn_task"."task"."builtin_key"
  IS '内置任务来源键（=builtin_catalog.builtin_key）；用户任务为 NULL';

ALTER TABLE "hasn_task"."task"
  ADD COLUMN IF NOT EXISTS "builtin_synced_revision" bigint;
COMMENT ON COLUMN "hasn_task"."task"."builtin_synced_revision"
  IS '内置任务已同步的 catalog.revision；用于检测官方是否有更新（用户任务为 NULL）';

-- 去重键：一个 owner 同一 builtin_key 只允许一行存活
CREATE UNIQUE INDEX IF NOT EXISTS "uq_task_owner_builtin_key"
  ON "hasn_task"."task" ("owner_id", "builtin_key")
  WHERE "builtin_key" IS NOT NULL AND "deleted_at" IS NULL;
