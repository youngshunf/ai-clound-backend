-- 清洗 hasn_artifacts 存量旧枚举值（doc35 A1 收敛的补课）
--
-- 根因：doc35（2026-07-15）把 kind 收敛为 6 值闭集、source_kind 重定义为 6 值，但当时的迁移
-- （2026-07-15-artifacts-resource-kind-taxonomy.sql）选择「本地测试环境存量可全删 → TRUNCATE
-- 重来，不做存量值改写」。TRUNCATE 之后 E2E 测试又造了一批**用旧值**的 fixture 行
-- （kind='deck'/'other' + source_kind='tool_output'，2026-07-16 跑 doc35 E2E 时写入）。
--
-- 症状：这些行本身是历史 fixture，但**读端点整个挂掉**——云端响应模型 `ArtifactKind` /
-- `ArtifactSourceKind` 是 Literal 闭集，序列化撞到任一旧值行即 ValidationError → 整个
-- `GET /api/v1/artifacts` 返 422，主人的产物库、会议副驾产物页全部打不开（一行脏数据炸全表）。
-- 且 422 的 msg 文案是「请求参数非法」，极具误导——真正的 loc 是 ["kind"] 而非 ["query","kind"]。
--
-- 处置：**改值不删行**（fixture 保留给 UI 验证，[[feedback_test_data_no_delete_keep_for_ui_verify]]）。
-- 幂等：WHERE 只命中非法值，重复执行无副作用。

BEGIN;

-- ① kind='deck' → resource（doc35：deck 是应用名不是类型，「哪个应用」已由 source_app_id 表达；
--    「是什么」归 resource_kind）。source_kind='tool_output' 是被砍掉的垃圾桶值 → 应用产出即 app。
UPDATE hasn_artifacts
SET kind = 'resource',
    source_kind = 'app',
    resource_kind = COALESCE(resource_kind, 'deck.presentation')
WHERE kind = 'deck';

-- ② plan 的 kind='other' → resource（'other' 是白名单拒绝的降级产物，不是设计；这些行 title
--    为目标名、source_tool='hasn.plan.write'，对应 descriptor `plan.goal`）。
UPDATE hasn_artifacts
SET kind = 'resource',
    source_kind = 'app',
    resource_kind = COALESCE(resource_kind, 'plan.goal')
WHERE kind = 'other'
  AND source_app_id = 'plan';

-- ③ 兜底：其余任何漏网的非法值一律落到最保守的合规值，确保读端点不再被单行炸穿。
--    kind 无法判定本体 → file（doc35：dataset/other 与 file 走同一渲染分支）。
UPDATE hasn_artifacts
SET kind = 'file'
WHERE kind NOT IN ('resource', 'document', 'image', 'video', 'voice', 'file');

--    source_kind 无法判定来源 → platform_tool（旧 tool_output 的多数实为平台工具产出）。
UPDATE hasn_artifacts
SET source_kind = 'platform_tool'
WHERE source_kind NOT IN ('app', 'platform_tool', 'external_tool', 'runtime_file', 'agent_note', 'upload');

COMMIT;
