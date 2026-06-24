-- P6-A 待办输出要求：todo 加 output_spec jsonb（编排期定义产物 kind/format/验收 + required）。
-- 设计：docs/hasn-node设计文档/19-规划与目标管理/02-待办产物化与产物落地闭环设计.md §3.1
-- 纯加列幂等，随常规云端部署执行。
SET search_path TO hasn_plan, public;

ALTER TABLE todo ADD COLUMN IF NOT EXISTS output_spec jsonb;
COMMENT ON COLUMN todo.output_spec IS '输出要求 (期望产物 kind/format/验收 + required，编排期定义，分身执行据此产出并落 hasn_artifacts)';
