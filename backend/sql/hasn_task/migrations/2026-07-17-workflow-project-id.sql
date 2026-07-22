-- 场景工作流项目轴接入 P9-A · 数据层（实施 95 号 §1）
-- doc11 v2.0 B5「项目为根」：场景实例化的 workflow 必须挂平台项目（doc38 联邦挂靠·可空列）。
-- 三条铁律（doc38）：项目不是权限边界、不拥有执行语义、不引入轻项目；删项目/摘项目仅令产物散落回
-- 各自应用（降级），绝不中断执行。故 project_id 只是「为了哪件事」的业务归属标签。
--
-- 幂等：ADD COLUMN IF NOT EXISTS + CREATE INDEX IF NOT EXISTS + 存量迁移只挑 project_id IS NULL。
-- 注意 PostgreSQL 语法：COMMENT ON（非 MySQL 内联注释）。

-- ① workflow 加可空 project_id 列 + owner+project 复合索引（P9-D/P9-F 按项目过滤 run）
ALTER TABLE hasn_task.workflow
    ADD COLUMN IF NOT EXISTS project_id UUID;

COMMENT ON COLUMN hasn_task.workflow.project_id IS '所属平台项目 (hasn_project.hasn_project.id，可空: 裸工程图允许为空; 场景实例化路径业务层硬闸必填，doc38 联邦挂靠·实施95 P9-A)';

CREATE INDEX IF NOT EXISTS idx_workflow_owner_project
    ON hasn_task.workflow (owner_id, project_id)
    WHERE project_id IS NOT NULL;

-- ② 存量迁移（幂等）：只迁「场景实例化(template_key 非空)但尚未挂项目(project_id 空)」的行。
--    - 每条建一条 hasn_project：name 取 workflow.name（空则 goal 截断，再空则「未命名场景」），
--      owner_id 取 workflow.owner_id；回填 workflow.project_id。
--    - 裸工程图（template_key IS NULL）留 NULL，不建项目（doc11 §11-7）。
--    - 不追溯改写存量 hasn_artifacts.project_id——存量产物没打标是历史事实，硬造归属属于 fake。
--    - 幂等：project_id IS NOT NULL 即跳过；重跑不产生重复项目。
DO $$
DECLARE
    r RECORD;
    new_pid uuid;
BEGIN
    FOR r IN
        SELECT
            id,
            owner_id,
            COALESCE(
                NULLIF(btrim(name), ''),
                NULLIF(left(btrim(COALESCE(goal, '')), 200), ''),
                '未命名场景'
            ) AS proj_name
        FROM hasn_task.workflow
        WHERE template_key IS NOT NULL AND project_id IS NULL
    LOOP
        INSERT INTO hasn_project.hasn_project (owner_id, name, status)
        VALUES (r.owner_id, r.proj_name, 'active')
        RETURNING id INTO new_pid;

        UPDATE hasn_task.workflow SET project_id = new_pid WHERE id = r.id;
    END LOOP;
END $$;
