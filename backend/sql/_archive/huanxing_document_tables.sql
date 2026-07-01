-- 1. 文档表
CREATE TABLE huanxing_document (
    id BIGSERIAL PRIMARY KEY,
    uuid VARCHAR(36) NOT NULL UNIQUE DEFAULT gen_random_uuid()::text,
    user_id BIGINT NOT NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT,
    summary VARCHAR(500),
    tags VARCHAR(500),
    word_count INT NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    is_public BOOLEAN NOT NULL DEFAULT FALSE,
    created_by VARCHAR(20) NOT NULL DEFAULT 'user',
    agent_id VARCHAR(64),
    share_token VARCHAR(64),
    share_password VARCHAR(128),
    share_permission VARCHAR(10) DEFAULT 'view',
    share_expires_at TIMESTAMP WITH TIME ZONE,
    current_version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE,
    
    CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES sys_user(id) ON DELETE CASCADE
);

CREATE INDEX idx_huanxing_document_user_id ON huanxing_document(user_id);
CREATE INDEX idx_huanxing_document_status ON huanxing_document(status);
CREATE INDEX idx_huanxing_document_share_token ON huanxing_document(share_token);
CREATE INDEX idx_huanxing_document_created_at ON huanxing_document(created_at DESC);
CREATE INDEX idx_huanxing_document_created_by ON huanxing_document(created_by);

COMMENT ON TABLE huanxing_document IS '唤星文档表';
COMMENT ON COLUMN huanxing_document.id IS '主键ID';
COMMENT ON COLUMN huanxing_document.uuid IS '文档UUID';
COMMENT ON COLUMN huanxing_document.user_id IS '用户ID';
COMMENT ON COLUMN huanxing_document.title IS '文档标题';
COMMENT ON COLUMN huanxing_document.content IS 'Markdown内容';
COMMENT ON COLUMN huanxing_document.summary IS '摘要（自动截取或手动设置）';
COMMENT ON COLUMN huanxing_document.tags IS '标签（JSON数组）';
COMMENT ON COLUMN huanxing_document.word_count IS '字数统计';
COMMENT ON COLUMN huanxing_document.status IS '状态(draft草稿/published已发布/archived已归档)';
COMMENT ON COLUMN huanxing_document.is_public IS '是否公开';
COMMENT ON COLUMN huanxing_document.created_by IS '创建来源(user用户/agent智能体)';
COMMENT ON COLUMN huanxing_document.agent_id IS 'Agent ID';
COMMENT ON COLUMN huanxing_document.share_token IS '分享链接token';
COMMENT ON COLUMN huanxing_document.share_password IS '分享密码(bcrypt hash)';
COMMENT ON COLUMN huanxing_document.share_permission IS '分享权限(view只读/edit可编辑)';
COMMENT ON COLUMN huanxing_document.share_expires_at IS '分享链接过期时间';
COMMENT ON COLUMN huanxing_document.current_version IS '当前版本号';
COMMENT ON COLUMN huanxing_document.created_at IS '创建时间';
COMMENT ON COLUMN huanxing_document.updated_at IS '更新时间';
COMMENT ON COLUMN huanxing_document.deleted_at IS '删除时间(软删除)';

-- 2. 文档版本表
CREATE TABLE huanxing_document_version (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL,
    version_number INT NOT NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT fk_document FOREIGN KEY (document_id) REFERENCES huanxing_document(id) ON DELETE CASCADE,
    CONSTRAINT fk_created_by FOREIGN KEY (created_by) REFERENCES sys_user(id) ON DELETE CASCADE,
    CONSTRAINT uk_document_version UNIQUE (document_id, version_number)
);

CREATE INDEX idx_huanxing_document_version_document_id ON huanxing_document_version(document_id);
CREATE INDEX idx_huanxing_document_version_created_at ON huanxing_document_version(created_at DESC);

COMMENT ON TABLE huanxing_document_version IS '文档版本历史表';
COMMENT ON COLUMN huanxing_document_version.id IS '主键ID';
COMMENT ON COLUMN huanxing_document_version.document_id IS '文档ID';
COMMENT ON COLUMN huanxing_document_version.version_number IS '版本号';
COMMENT ON COLUMN huanxing_document_version.title IS '文档标题';
COMMENT ON COLUMN huanxing_document_version.content IS 'Markdown内容';
COMMENT ON COLUMN huanxing_document_version.created_by IS '创建者用户ID';
COMMENT ON COLUMN huanxing_document_version.created_at IS '创建时间';

-- 3. 自动保存表
CREATE TABLE huanxing_document_autosave (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    saved_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT fk_document FOREIGN KEY (document_id) REFERENCES huanxing_document(id) ON DELETE CASCADE,
    CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES sys_user(id) ON DELETE CASCADE,
    CONSTRAINT uk_document_user UNIQUE (document_id, user_id)
);

CREATE INDEX idx_huanxing_document_autosave_saved_at ON huanxing_document_autosave(saved_at DESC);

COMMENT ON TABLE huanxing_document_autosave IS '文档自动保存表（每文档每用户仅一条，UPSERT更新）';
COMMENT ON COLUMN huanxing_document_autosave.id IS '主键ID';
COMMENT ON COLUMN huanxing_document_autosave.document_id IS '文档ID';
COMMENT ON COLUMN huanxing_document_autosave.user_id IS '用户ID';
COMMENT ON COLUMN huanxing_document_autosave.content IS 'Markdown内容';
COMMENT ON COLUMN huanxing_document_autosave.saved_at IS '最后保存时间';
