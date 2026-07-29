-- 额度账本的服务契约是“每用户一行”；修复早期环境中缺失或漂移的唯一约束。
SET search_path TO hasn_growth, public;

DO $$
DECLARE
    duplicate_user_count bigint;
    user_id_attnum smallint;
    has_expected_constraint boolean;
BEGIN
    SELECT attnum
      INTO user_id_attnum
      FROM pg_attribute
     WHERE attrelid = 'hasn_growth.lead_quota'::regclass
       AND attname = 'user_id'
       AND NOT attisdropped;

    SELECT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'hasn_growth.lead_quota'::regclass
           AND contype = 'u'
           AND conkey = ARRAY[user_id_attnum]::smallint[]
    )
      INTO has_expected_constraint;

    IF has_expected_constraint THEN
        RETURN;
    END IF;

    SELECT count(*)
      INTO duplicate_user_count
      FROM (
          SELECT user_id
            FROM hasn_growth.lead_quota
           GROUP BY user_id
          HAVING count(*) > 1
      ) duplicated_users;

    IF duplicate_user_count > 0 THEN
        RAISE EXCEPTION
            'lead_quota 存在 % 个重复 user_id，拒绝自动合并额度账本',
            duplicate_user_count;
    END IF;

    ALTER TABLE hasn_growth.lead_quota
        DROP CONSTRAINT IF EXISTS uq_growth_lead_quota_user;
    DROP INDEX IF EXISTS hasn_growth.uq_growth_lead_quota_user;
    ALTER TABLE hasn_growth.lead_quota
        ADD CONSTRAINT uq_growth_lead_quota_user UNIQUE (user_id);
END
$$;

COMMENT ON CONSTRAINT uq_growth_lead_quota_user
    ON hasn_growth.lead_quota
    IS '额度账本每用户唯一，免费额度通过 period_key 在同一行按月重置';
