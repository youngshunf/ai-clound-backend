-- PLAN-LOOP L0（修 G1·补 plan- 键）：规范化 hasn_artifacts.origin_ref 中 plan 级委托键的连字符漂移。
--
-- 背景：同日 sibling 迁移 2026-07-05-artifacts-originref-normalize.sql
-- 只规范了 todo- 键；但 hasn-mcp `hasn.plan.delegate`(plan_id) 计划级委托会话产出的产物写的是
-- resource:plan:plan-{id}（连字符），同样与 webui/云端权威冒号形 resource:plan:plan:{id} 反查键不一致
-- → 计划级产物轨永远查空。此迁移补齐 plan- 键（并再覆盖 todo-，幂等·自包含，即便前一支未执行也兜底）。
--
-- 权威格式一律冒号分段（见 backend/app/hasn_plan/service/origin_ref.py 契约）。
--
-- 安全边界：正则**锚定** ^resource:plan:(todo|plan)-\d+$（连字符后必须紧跟纯数字 id 且到行尾），
-- 因此**绝不误伤** id-less 白名单常量 resource:plan:habit-onboarding / profile-onboarding /
-- proactive-planning / onboarding 等（它们连字符后是字母，不匹配 \d+）。冒号权威形本就不匹配 → 幂等。
UPDATE public.hasn_artifacts
SET origin_ref = regexp_replace(origin_ref, '^resource:plan:(todo|plan)-(\d+)$', 'resource:plan:\1:\2')
WHERE origin_ref ~ '^resource:plan:(todo|plan)-\d+$';
