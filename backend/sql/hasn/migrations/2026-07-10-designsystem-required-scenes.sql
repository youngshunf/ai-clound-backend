-- DSGAL-3：设计系统组件画廊「场景标准」——required_scenes 列
--
-- owner 派发分身生成设计系统时，可勾选组件画廊要求覆盖的交付物场景（品牌网站/演示文稿/产品海报/移动端），
-- 默认仅「品牌网站」。当前版 components.manifest 的 scenes[] 与本列交叉，产出「品牌网站 3/5 · 缺 CTA/页脚」
-- 软提示（福仔拍板：软提示不阻断发卡，完成判定仍只看五项必填字段）。
--
-- 幂等：IF NOT EXISTS；存量行由 server_default 回填 ["brand_website"]。

ALTER TABLE hasn_designsystem.design_system
    ADD COLUMN IF NOT EXISTS required_scenes JSONB NOT NULL DEFAULT '["brand_website"]'::jsonb;

COMMENT ON COLUMN hasn_designsystem.design_system.required_scenes IS
    '组件画廊要求覆盖的交付物场景 id 列表（brand_website/deck/poster/mobile；默认 [brand_website]，软提示不阻断）';
