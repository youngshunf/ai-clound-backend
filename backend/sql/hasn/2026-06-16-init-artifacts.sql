-- 分身产物登记表（Agent Artifacts）
-- 设计：docs/Agent产物系统/00-Agent产物存储与展示下载设计.md §5
-- 产物 = 「某分身在某条消息/某个 session 里，通过某个工具，产出了某个资产或某个 hasn:// 资源」这件事的记录。
-- 零拷贝：仅持指针（asset_id / resource_uri）+ 溯源（conversation/message/session），不复制内容本体。
-- public schema（与 hasn_assets/hasn_asset_grants/hasn_resource_share 同为平台底座 primitive）。
CREATE TABLE hasn_artifacts (
    id              BIGSERIAL    PRIMARY KEY,
    artifact_id     VARCHAR(40)  NOT NULL DEFAULT '',
    agent_hasn_id   VARCHAR(40)  NOT NULL DEFAULT '',
    owner_hasn_id   VARCHAR(40)  NOT NULL DEFAULT '',
    kind            VARCHAR(16)  NOT NULL DEFAULT 'other',
    title           VARCHAR(256),
    summary         TEXT,
    asset_id        VARCHAR(40),
    resource_uri    VARCHAR(512),
    conversation_id UUID,
    message_id      BIGINT,
    session_id      VARCHAR(40),
    source_tool     VARCHAR(128),
    source_kind     VARCHAR(16)  NOT NULL DEFAULT 'tool_output',
    dispatch_id     VARCHAR(64),
    metadata        JSONB        NOT NULL DEFAULT '{}',
    status          VARCHAR(16)  NOT NULL DEFAULT 'active',
    created_time    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_time    TIMESTAMPTZ
);

COMMENT ON TABLE hasn_artifacts IS '分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）';
COMMENT ON COLUMN hasn_artifacts.id IS '主键 ID';
COMMENT ON COLUMN hasn_artifacts.artifact_id IS '产物 ID (art_<ulid> 公开标识)';
COMMENT ON COLUMN hasn_artifacts.agent_hasn_id IS '产出分身 hasn_id';
COMMENT ON COLUMN hasn_artifacts.owner_hasn_id IS '分身主人 hasn_id (归属 + 隔离键)';
COMMENT ON COLUMN hasn_artifacts.kind IS '产物类型 (image:图片:blue/voice:语音:purple/file:文件:gray/document:文档:cyan/deck:演示文稿:violet/webpage:网页:green/dataset:数据集:orange/other:其它:default)';
COMMENT ON COLUMN hasn_artifacts.title IS '展示标题 (工具给/文件名/截断的 prompt)';
COMMENT ON COLUMN hasn_artifacts.summary IS '简要描述';
COMMENT ON COLUMN hasn_artifacts.asset_id IS '关联资产 ID (public.hasn_assets.asset_id，image/voice/file 主路径)';
COMMENT ON COLUMN hasn_artifacts.resource_uri IS 'hasn:// 资源 URI (客户端无关，deck/webpage/外部结果无 asset 本体时用)';
COMMENT ON COLUMN hasn_artifacts.conversation_id IS '来源会话 ID (public.hasn_conversations.id)';
COMMENT ON COLUMN hasn_artifacts.message_id IS '来源消息 ID (public.hasn_messages.id)';
COMMENT ON COLUMN hasn_artifacts.session_id IS '来源本地 runtime session (ULID)';
COMMENT ON COLUMN hasn_artifacts.source_tool IS '产出工具全名 (hasn.image.generate)';
COMMENT ON COLUMN hasn_artifacts.source_kind IS '产出来源 (tool_output:工具产出:blue/task_result:任务成果:violet/upload:上传:gray/external:外部结果:orange)';
COMMENT ON COLUMN hasn_artifacts.dispatch_id IS '派发关联 (审计/去重)';
COMMENT ON COLUMN hasn_artifacts.metadata IS '元数据 (mime/size/width/height 冗余 + 工具上下文快照)';
COMMENT ON COLUMN hasn_artifacts.status IS '状态 (active:正常:green/deleted:已删:red)';
COMMENT ON COLUMN hasn_artifacts.created_time IS '创建时间';
COMMENT ON COLUMN hasn_artifacts.updated_time IS '更新时间';

-- 唯一 + 去重：artifact_id 全局唯一；同一派发+同一资产只登记一次（重试幂等）
CREATE UNIQUE INDEX uq_hasn_artifacts_artifact_id ON hasn_artifacts (artifact_id);
CREATE UNIQUE INDEX uq_hasn_artifacts_dedup ON hasn_artifacts (agent_hasn_id, dispatch_id, asset_id)
    WHERE dispatch_id IS NOT NULL AND asset_id IS NOT NULL;

-- 查询：owner 时间线 / 单 agent 时间线 / 按会话
CREATE INDEX idx_hasn_artifacts_owner ON hasn_artifacts (owner_hasn_id, created_time DESC);
CREATE INDEX idx_hasn_artifacts_agent ON hasn_artifacts (agent_hasn_id, created_time DESC);
CREATE INDEX idx_hasn_artifacts_conv  ON hasn_artifacts (conversation_id);
