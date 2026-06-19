-- 设计系统列表卡预览色板：hasn_designsystem.design_system 增加 preview_swatches 列（内置库预览图）。
-- 需求（福仔 2026-06-19）：浏览库列表「要有一个预览图」。做法 = denorm 当前版 tokens.css 的关键色
-- 为紧凑 JSONB（{bg,surface,fg,muted,border,accent,accent_on}），前端列表卡直接渲染迷你 mockup 当
-- 预览图，免去逐项取产物/渲染整套组件。随 save()/set_current_revision() 刷新；内置 seed 直接带值。
-- 端云：云端权威 + 本地 SQLite 镜像（随 _ds_dict 双通道下行）。
--
-- 幂等：可重复执行。

ALTER TABLE "hasn_designsystem"."design_system"
  ADD COLUMN IF NOT EXISTS "preview_swatches" jsonb;

COMMENT ON COLUMN "hasn_designsystem"."design_system"."preview_swatches"
  IS '列表卡预览色板（denorm 自当前版 tokens.css 关键色，前端列表渲染迷你预览）';
