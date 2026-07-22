-- 产物登记扩展：设备归属 + 本地路径 + 来源应用 + 新增/修改动作（doc34）
--
-- 福仔 2026-07-15 拍板：「分身产出的内容，都要登记，不管是本地还是云端。」
--
-- 此前产物本体只有 body/asset_id/resource_uri 三选一，**全是云端形态**——本地路径产物
-- （imagelab 的 local_only 导出、runtime 用 write_file/patch 写的文件）压根无处可登记。
-- 本次加 local_path 作为第四种本体（云端只存指针，正文留在设备磁盘上），并补上
-- node_id 让 UI 能判「本机可直接打开」还是「在其他设备上」。

ALTER TABLE public.hasn_artifacts
    ADD COLUMN IF NOT EXISTS node_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS local_path VARCHAR(512),
    ADD COLUMN IF NOT EXISTS source_app_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS action VARCHAR(16) NOT NULL DEFAULT 'create';

COMMENT ON COLUMN public.hasn_artifacts.node_id IS '产出设备节点 ID (本地路径产物必填，纯云端产物为空；UI 据此判本机直接打开 / 其他设备只提示)';
COMMENT ON COLUMN public.hasn_artifacts.local_path IS '本地绝对路径 (本地权威产物，云端只存指针不存正文；与 body/asset_id/resource_uri 构成四选一)';
COMMENT ON COLUMN public.hasn_artifacts.source_app_id IS '来源应用 ID (hasn_app_catalog.app_id，如 deck/imagelab/knowledge；UI 据此显示应用图标，非应用产出为空)';
COMMENT ON COLUMN public.hasn_artifacts.action IS '产出动作 (create:新增:green/update:修改:blue)';

-- 本地文件产物幂等键：同一文件在一次会话里反复写只留一行。
-- 这是 runtime 文件捕获不把产物列表刷成流水账的关键——分身改 10 次 report.md 是 1 个产物，不是 10 个。
CREATE UNIQUE INDEX IF NOT EXISTS uq_hasn_artifacts_local_file
    ON public.hasn_artifacts (agent_hasn_id, session_id, node_id, local_path)
    WHERE local_path IS NOT NULL AND session_id IS NOT NULL AND status = 'active';

-- 按设备反查产物（「这台机器上的产物」/ 设备下线后标灰）。
CREATE INDEX IF NOT EXISTS ix_hasn_artifacts_node
    ON public.hasn_artifacts (node_id)
    WHERE node_id IS NOT NULL;
