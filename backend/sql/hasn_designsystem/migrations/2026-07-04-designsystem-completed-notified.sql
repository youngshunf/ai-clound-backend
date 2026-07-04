-- 设计系统「完成发卡」幂等水位：hasn_designsystem.design_system 增加 completed_notified_at 列（DSFIX-1）。
-- 需求（福仔 2026-07-04）：分身生成设计系统「不要用自动完成」——分身写满设计系统必填字段（详情页四区块
-- 所需内容：tokens.css + 契约评分报告 + 设计说明 design.md + 组件画廊 HTML + 组件清单 JSON）后，
-- 云端 save 判定首次完整且作者是分身 → 发一次「设计系统已完成·查看」卡给主人，深链
-- hasn://designsystem/{云端权威 id} 直达详情。本列作幂等水位：非空=已发过（此后再 save 不重复发）。
--
-- 幂等：可重复执行。
ALTER TABLE "hasn_designsystem"."design_system"
  ADD COLUMN IF NOT EXISTS "completed_notified_at" timestamptz;

COMMENT ON COLUMN "hasn_designsystem"."design_system"."completed_notified_at"
  IS '首次完整（必填字段齐全）发完成卡的时间（幂等水位，非空=已发过）';
