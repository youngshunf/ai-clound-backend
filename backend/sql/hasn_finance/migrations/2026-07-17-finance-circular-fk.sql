-- =====================================================
-- hasn_finance：补 strategy.latest_backtest_id 的复合 FK（循环依赖后置）
--
-- 为什么单独一支迁移：strategy.latest_backtest_id → backtest_report 与
-- backtest_report.strategy_id → strategy 互为外键，PG 建表阶段无法同时声明。
-- 建表 SQL 里 strategy 先只留列，两表都存在后由本迁移 ALTER 补上。
--
-- 为什么必须复合 FK（owner_id, latest_backtest_id）而非单列：
--   「关系也必须 owner 一致，单列 FK 不够」（05 §3.1 ★）——否则 A 的策略能缓存 B 的回测 id，
--   列表页会显示别人的夏普。latest_backtest_id 可空；PG 默认 MATCH SIMPLE 在任一列为 NULL 时
--   跳过检查，正是所需语义（策略刚建、还没回测过）。
--
-- ON DELETE SET NULL：回测被删时缓存指针置空即可——权威在 backtest_report，
--   latest_backtest_id 只是免 N+1 的冗余缓存（05 §3.1.3），不一致时以 backtest_report 为准。
--
-- 幂等：可重复执行（先查 pg_constraint 再加）。
-- 设计事实源：docs/hasn-node设计文档/金融投研与量化交易/05-数据与同步契约.md §3.1.3
-- =====================================================
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'fk_finance_strategy_latest_backtest'
      AND connamespace = 'hasn_finance'::regnamespace
  ) THEN
    ALTER TABLE "hasn_finance"."strategy"
      ADD CONSTRAINT "fk_finance_strategy_latest_backtest"
      FOREIGN KEY ("owner_id", "latest_backtest_id")
      REFERENCES "hasn_finance"."backtest_report" ("owner_id", "id")
      ON DELETE SET NULL;
  END IF;
END
$$;
