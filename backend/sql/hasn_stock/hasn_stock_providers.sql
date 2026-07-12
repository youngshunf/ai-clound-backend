-- 素材站目录配置表（A-P2-0）：管理员在后台增删素材站、配 api_key、调默认 failover 顺序。
-- 独立 PG schema `hasn_stock`（ADR-15 应用命名空间隔离；与 external_mcp/hasn_diag 同构）。
-- api_key 用 KeyEncryption（common/security/encryption.py）Fernet 加密落库，明文绝不回显/入日志/进 PDC。
-- 设计事实源：docs/Agent产物系统/01-分身资源检索与素材站工具设计.md §4.5。

CREATE SCHEMA IF NOT EXISTS hasn_stock;

CREATE TABLE IF NOT EXISTS hasn_stock.hasn_stock_providers (
    id                bigserial     PRIMARY KEY,
    provider          varchar(40)   NOT NULL,
    display_name      varchar(80)   NOT NULL DEFAULT '',
    media_types       jsonb         NOT NULL DEFAULT '[]'::jsonb,
    api_key_cipher    text,
    download_domains  jsonb         NOT NULL DEFAULT '[]'::jsonb,
    enabled           boolean       NOT NULL DEFAULT true,
    priority          integer       NOT NULL DEFAULT 100,
    license_terms_url varchar(500),
    remark            varchar(255),
    created_time      timestamptz(6) NOT NULL DEFAULT now(),
    updated_time      timestamptz(6)
);

-- provider 唯一（一个素材站一行）
CREATE UNIQUE INDEX IF NOT EXISTS uq_hasn_stock_providers_provider
    ON hasn_stock.hasn_stock_providers (provider);

-- 默认 failover 链常用查询：enabled + priority 升序
CREATE INDEX IF NOT EXISTS ix_hasn_stock_providers_enabled_priority
    ON hasn_stock.hasn_stock_providers (enabled, priority);

COMMENT ON TABLE  hasn_stock.hasn_stock_providers IS '素材站目录（后台可配：provider/media_types/api_key加密/download_domains/enabled/priority）';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.provider IS '素材站唯一标识（pexels/pixabay/coverr/…）';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.display_name IS '展示名';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.media_types IS '支持的媒体类型 JSON 数组（image/video 子集，如 coverr 仅 ["video"]）';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.api_key_cipher IS 'api_key 密文（KeyEncryption Fernet 加密；明文绝不回显/入日志/进 PDC；未配为 NULL）';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.download_domains IS '该站下载直链合法域名 JSON 数组（驱动 stock.download SSRF 白名单）';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.enabled IS '启用开关';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.priority IS '默认 failover 顺序（升序在前）';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.license_terms_url IS '许可条款链接（随下载 meta_data 落库，供发布前合规审查）';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.remark IS '备注';

-- seed 内置三行（api_key 留空，待管理员在后台配 key 后方可真搜；enabled=true 但无 key 时 search 记 warn 降级）。
INSERT INTO hasn_stock.hasn_stock_providers
    (provider, display_name, media_types, download_domains, enabled, priority, license_terms_url)
VALUES
    ('pexels',  'Pexels',  '["image","video"]'::jsonb,
        '["images.pexels.com","videos.pexels.com","player.vimeo.com"]'::jsonb, true, 10,
        'https://www.pexels.com/license/'),
    ('pixabay', 'Pixabay', '["image","video"]'::jsonb,
        '["pixabay.com","cdn.pixabay.com"]'::jsonb, true, 20,
        'https://pixabay.com/service/license-summary/'),
    ('coverr',  'Coverr',  '["video"]'::jsonb,
        '["cdn.coverr.co"]'::jsonb, true, 30,
        'https://coverr.co/license')
ON CONFLICT (provider) DO NOTHING;
