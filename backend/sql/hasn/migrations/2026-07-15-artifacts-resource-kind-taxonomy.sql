-- 产物类型与来源分类体系重构（doc35 刀 A1）
--
-- 一个字段扛三个维度是病根：kind 同时想回答「怎么打开」「哪个应用」「MIME 大类」。
-- 本次拆开：artifact_kind 只答「怎么打开」（6 枚举），resource_kind 新增列答「是什么」，
-- source_app_id（已有）答「哪个应用」，source_kind 重定义答「怎么来的」（6 枚举）。
--
-- 前提：本地测试环境，存量可全删（福仔 2026-07-15 明确）→ 无迁移路径，清库重来。
-- 施工时已 TRUNCATE hasn_artifacts，故不做存量值改写。

BEGIN;

-- ① 新增 resource_kind：应用资源类型（descriptor.resource_kind 原值）。
-- 这个数据早就存在于 descriptor，只是登记时被丢掉了——知识库塌成 dataset、知识文档塌成
-- document，两者在 UI 上再也分不开。存原值后 UI 查 registry 拿展示名，新应用零改前端。
ALTER TABLE hasn_artifacts ADD COLUMN IF NOT EXISTS resource_kind VARCHAR(64) NULL;

COMMENT ON COLUMN hasn_artifacts.resource_kind IS
    '应用资源类型 (descriptor.resource_kind 原值，如 knowledge.base/deck.presentation；仅 artifact_kind=resource 有值；UI 据它查 registry 取展示名)';

-- ② kind 重定义为 6 枚举：与「本体存在哪」严格对齐。
-- 砍掉 deck（应用名当类型，source_app_id 已表达）/ webpage（同上）/ dataset（同名不同物，
-- 且与 file 走同一渲染分支）/ other（它只是白名单拒绝的降级产物，不是设计）。
COMMENT ON COLUMN hasn_artifacts.kind IS
    '产物类型·怎么打开 (resource:应用资源:violet/document:文档:cyan/image:图片:blue/video:视频:rose/voice:语音:purple/file:文件:gray)';

-- ③ source_kind 重定义为 6 枚举：让 doc34 §3 的「来源图标判定链」本身成为字段。
-- 砍掉 tool_output（垃圾桶，按实际产出者拆开）/ task_result（那是产出场景，由 session_id
-- 表达，不是来源）/ external（并入 external_tool）。
COMMENT ON COLUMN hasn_artifacts.source_kind IS
    '产出来源·怎么来的 (app:应用产出:violet/platform_tool:平台工具:blue/external_tool:外部取材:orange/runtime_file:运行时文件:gray/agent_note:分身自撰:cyan/upload:主人上传:default)';

COMMIT;
