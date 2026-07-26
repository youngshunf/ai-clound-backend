-- 创作运营容器接入平台项目联邦挂靠：业务 project.id 与平台项目 UUID 严格分列。
ALTER TABLE hasn_creator.project
  ADD COLUMN IF NOT EXISTS platform_project_id uuid
  REFERENCES hasn_project.hasn_project(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_creator_project_platform_project
  ON hasn_creator.project (assignee, platform_project_id)
  WHERE platform_project_id IS NOT NULL;

COMMENT ON COLUMN hasn_creator.project.platform_project_id
  IS '挂靠的平台项目 id（独立于创作业务 project_id；仅作跨应用归集视角）';
