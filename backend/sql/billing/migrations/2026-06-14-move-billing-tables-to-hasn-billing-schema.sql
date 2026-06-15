-- 迁移：计费 13 张表从 public 搬入独立 PG schema hasn_billing（ADR-15：AI-Native 应用一应用一 schema）。
-- 模块由 app/pay + app/user_tier 按 ADR-15 §4 合并为 app/billing；本迁移补齐云端轴①（schema 隔离）。
-- app_id=billing；URL（/api/v1/pay/*、/api/v1/user_tier/*）保持不变（daemon BillingCloud 代理依赖）；表名不改（仅 SET SCHEMA）。
-- 13 张表 = 支付 7（pay_*，含 pay_app 支付应用配置）+ 订阅积分 6（credit_*/subscription_tier/user_*/model_credit_rate）。
-- 注：pay_app 是 pay_module 原 6 表之一（pay_channel/order/contract 的 app_id 外键指向 pay_app.id），
--     批次3 首版漏迁（误留 app/huanxing + public），2026-06-15 补入本数组一并搬迁。
-- 幂等：逐表「仅当表在 public 且不在 hasn_billing 时」才 SET SCHEMA；
--       全新库（codegen 直接落 hasn_billing）/ 已迁移库 → 自动跳过，无副作用。可重复执行。
-- ⚠️ 破坏性 DDL（元数据操作、很快，但会移动表）：生产执行须在停机/低峰窗口，先全库备份。
-- ⚠️ 配套代码：凡裸 raw SQL 引用 billing 表的地方（huanxing analytics credit_transaction/user_subscription）
--    已同步全限定改写为 hasn_billing.<table>；ORM 经 BillingBase 自动落到新 schema。
-- 执行：psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 本文件

DO $$
DECLARE
    t text;
    tbls text[] := ARRAY[
        'pay_channel', 'pay_contract', 'pay_merchant',
        'pay_notify_log', 'pay_order', 'pay_refund',
        'credit_package', 'credit_transaction', 'model_credit_rate',
        'subscription_tier', 'user_credit_balance', 'user_subscription'
    ];
    moved int := 0;
BEGIN
    CREATE SCHEMA IF NOT EXISTS hasn_billing;

    FOREACH t IN ARRAY tbls LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = t
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'hasn_billing' AND table_name = t
        ) THEN
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA hasn_billing', t);
            moved := moved + 1;
            RAISE NOTICE 'moved public.% -> hasn_billing.%', t, t;
        END IF;
    END LOOP;

    RAISE NOTICE 'billing schema 迁移完成：本次搬迁 % 张表（其余已在 hasn_billing 或不存在，幂等跳过）', moved;
END $$;
