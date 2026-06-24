-- P6-B 产物层（public schema，无 CREATE SCHEMA）：
--   1) body          —— 文本/markdown 正文直接入库（kind=document 文本产物用，不上传文件）
--   2) origin_ref    —— 产出所属业务资源回指（resource:plan:todo:{id} 等，按业务对象反查产物）
--   3) kind 枚举加 video
-- 设计：docs/hasn-node设计文档/19-规划与目标管理/02-待办产物化与产物落地闭环设计.md §3.2/§5
-- 纯加列幂等，随常规云端部署执行。

ALTER TABLE hasn_artifacts ADD COLUMN IF NOT EXISTS body text;
ALTER TABLE hasn_artifacts ADD COLUMN IF NOT EXISTS origin_ref varchar(128);

COMMENT ON COLUMN hasn_artifacts.body IS '文本/markdown 正文直接入库 (kind=document 文本产物用，不上传文件；二进制走 asset_id，资源走 resource_uri，三选一)';
COMMENT ON COLUMN hasn_artifacts.origin_ref IS '产出所属业务资源 (resource:plan:todo:{id} 等，来自 work_session.origin_ref，按业务对象反查产物)';
COMMENT ON COLUMN hasn_artifacts.kind IS '产物类型 (image:图片:blue/voice:语音:purple/video:视频:rose/file:文件:gray/document:文档:cyan/deck:演示文稿:violet/webpage:网页:green/dataset:数据集:orange/other:其它:default)';

CREATE INDEX IF NOT EXISTS idx_hasn_artifacts_origin ON hasn_artifacts (origin_ref) WHERE origin_ref IS NOT NULL;
