-- =====================================================
-- 模型别名映射表 - 将 Anthropic 模型名映射到实际可用模型
-- =====================================================
-- 用于将 Claude Agent SDK 请求的 Anthropic 模型名称映射到实际可用的模型
-- 例如：claude-sonnet-4-5-20250929 -> [gpt-4o, deepseek-chat, ...]
-- 支持按优先级故障转移：如果第一个模型失败，自动切换到下一个

CREATE TABLE IF NOT EXISTS llm_model_alias (
    id BIGSERIAL PRIMARY KEY,
    alias_name VARCHAR(128) NOT NULL,
    model_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    display_name VARCHAR(128),
    description VARCHAR(512),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_time TIMESTAMP
);

-- 添加索引
CREATE UNIQUE INDEX IF NOT EXISTS uk_alias_name ON llm_model_alias(alias_name);
CREATE INDEX IF NOT EXISTS idx_enabled ON llm_model_alias(enabled);

-- 添加字段注释
COMMENT ON TABLE llm_model_alias IS '模型别名映射表';
COMMENT ON COLUMN llm_model_alias.id IS '主键ID';
COMMENT ON COLUMN llm_model_alias.alias_name IS '别名（Anthropic 模型名称）';
COMMENT ON COLUMN llm_model_alias.model_ids IS '映射的模型 ID 列表（按优先级排序）';
COMMENT ON COLUMN llm_model_alias.display_name IS '显示名称';
COMMENT ON COLUMN llm_model_alias.description IS '描述';
COMMENT ON COLUMN llm_model_alias.enabled IS '是否启用';
COMMENT ON COLUMN llm_model_alias.created_time IS '创建时间';
COMMENT ON COLUMN llm_model_alias.updated_time IS '更新时间';

-- =====================================================
-- 初始化 Anthropic 模型别名映射数据
-- =====================================================
-- 注意：model_ids 中的 ID 需要根据实际的 llm_model_config 表中的模型 ID 填写
-- 这里假设：
-- - gpt-4o 的 ID 是 1
-- - deepseek-chat 的 ID 是 2
-- - gpt-4o-mini 的 ID 是 3
-- 实际使用时需要根据你的 llm_model_config 表调整

-- Claude Opus 映射
INSERT INTO llm_model_alias (alias_name, model_ids, display_name, description, enabled) VALUES
('claude-opus-4-5-20251101', '[1, 2]'::jsonb, 'Claude Opus 4.5', '最强能力模型，映射到 gpt-4o 和 deepseek-chat', TRUE)
ON CONFLICT (alias_name) DO NOTHING;

-- Claude Sonnet 映射
INSERT INTO llm_model_alias (alias_name, model_ids, display_name, description, enabled) VALUES
('claude-sonnet-4-5-20250929', '[1, 2]'::jsonb, 'Claude Sonnet 4.5', '平衡模型，映射到 gpt-4o 和 deepseek-chat', TRUE)
ON CONFLICT (alias_name) DO NOTHING;

-- Claude Haiku 映射
INSERT INTO llm_model_alias (alias_name, model_ids, display_name, description, enabled) VALUES
('claude-haiku-4-5-20251001', '[3, 2]'::jsonb, 'Claude Haiku 4.5', '快速高效模型，映射到 gpt-4o-mini 和 deepseek-chat', TRUE)
ON CONFLICT (alias_name) DO NOTHING;
