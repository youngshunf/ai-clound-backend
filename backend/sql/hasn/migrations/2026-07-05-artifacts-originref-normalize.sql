-- PLAN-LOOP L0（修 G1）：规范化 hasn_artifacts.origin_ref 待办键的连字符漂移。
--
-- 根因：daemon 派发历史写 `resource:plan:todo-{id}`（连字符），而 webui/云端反查用权威
-- `resource:plan:todo:{id}`（冒号）→ 分身在会话里产出的产物继承会话连字符键，冒号键反查**永远查空**
-- （福仔 F1「分身做了事情我看不到结果」的第一根因）。
--
-- 权威格式一律冒号分段（见 backend/app/hasn_plan/service/origin_ref.py 契约）。过渡期 by-origin
-- 端点**不做**双键兼容——一次迁完，避免兼容层永久化（doc06 §3.1）。幂等：仅命中 `todo-` 前缀行。
UPDATE public.hasn_artifacts
SET origin_ref = regexp_replace(origin_ref, '^resource:plan:todo-', 'resource:plan:todo:')
WHERE origin_ref LIKE 'resource:plan:todo-%';
