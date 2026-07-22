-- 平台项目联邦挂靠列（模块 14 doc38 §4.1）——两处云端权威表加可空 project_id。
-- 三条铁律：project 不是权限边界、不是应用挂载点、不接管应用容器；仅以可空 project_id 打标。
--   删除任一挂靠（置 NULL）后对应表一切功能不受影响，仅聚合视图少行（§10-5 验收红线）。
--
-- ① hasn_artifacts.project_id（产物挂靠，§3.2/§5.1）：register-on-write 公共接缝经 ContextVar
--    自动打标（U2）；产物流并集读按 (owner_hasn_id, project_id) 索引过滤（§5.1-4）。
--    注意列名 owner_hasn_id（本表历史列名，非 owner_id）。
-- ② hasn_sessions.project_id（工作会话挂靠，§3.2/§5.2）：build_app_launch_spec 透传 → daemon
--    会话表落值 → 摘要上行携带（U5）。
--
-- 幂等：IF NOT EXISTS（列）+ IF NOT EXISTS（索引）；跨 schema 逻辑引用 hasn_project.hasn_project(id)，
--   不建物理 FK（跨 schema + 端云 id 不一致，硬 FK 会拖累挂靠/摘除，§3.2）。

-- ① 产物挂靠列 + 并集读索引
ALTER TABLE public.hasn_artifacts
    ADD COLUMN IF NOT EXISTS project_id uuid;

COMMENT ON COLUMN public.hasn_artifacts.project_id IS '平台项目挂靠 id（hasn_project.hasn_project.id，可空；register-on-write 经 ContextVar 自动打标，doc38 §5.1）';

CREATE INDEX IF NOT EXISTS idx_hasn_artifacts_project
    ON public.hasn_artifacts (owner_hasn_id, project_id)
    WHERE project_id IS NOT NULL;

-- ② 工作会话摘要挂靠列
ALTER TABLE public.hasn_sessions
    ADD COLUMN IF NOT EXISTS project_id uuid;

COMMENT ON COLUMN public.hasn_sessions.project_id IS '平台项目挂靠 id（hasn_project.hasn_project.id，可空；派发透传落值，摘要上行携带，doc38 §5.2）';

CREATE INDEX IF NOT EXISTS idx_hasn_sessions_project
    ON public.hasn_sessions (owner_id, project_id)
    WHERE project_id IS NOT NULL;
