-- 迁移：hasn_growth 所有表 updated_time 改为 NULLABLE，对齐 fba DateTimeMixin 约定。
--
-- 背景：fba `DateTimeMixin.updated_time` 是 `Mapped[datetime | None]`（init=False，仅 onupdate=now，
-- 插入时不赋值）。ORM 插入会显式写 `updated_time=NULL`，覆盖 DB 端 `DEFAULT now()`。
-- 若列为 NOT NULL（hasn_task / 绝大多数 public 表均为 NULLABLE），任何经 ORM 的 INSERT 都会
-- 触发 `null value in column "updated_time" violates not-null constraint`。
--
-- hasn_growth 的收编表（M1：contact/collection_job/... ）与 7 张新业务表（M2：customer/opportunity/
-- outreach_message/activity/playbook/form_submission/optout_record）原 DDL 均误用 NOT NULL，
-- 此迁移统一改回 NULLABLE。保留 DEFAULT now()（仅手工 SQL 插入时兜底，不影响 ORM）。
--
-- 幂等：仅对 is_nullable='NO' 的 updated_time 执行 DROP NOT NULL。

SET search_path TO hasn_growth, public;

DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT table_name
        FROM information_schema.columns
        WHERE table_schema = 'hasn_growth'
          AND column_name = 'updated_time'
          AND is_nullable = 'NO'
    LOOP
        EXECUTE format('ALTER TABLE hasn_growth.%I ALTER COLUMN updated_time DROP NOT NULL', r.table_name);
        RAISE NOTICE 'updated_time DROP NOT NULL: hasn_growth.%', r.table_name;
    END LOOP;
END $$;
