-- 本地原件快照上传由服务端计算 sha256，并按主人、资产类型和内容幂等。

ALTER TABLE public.hasn_assets
    ADD COLUMN IF NOT EXISTS content_sha256 VARCHAR(64) NULL;

COMMENT ON COLUMN public.hasn_assets.content_sha256 IS
    '资产内容的 64 位小写 sha256；本地原件快照据此幂等上传';

ALTER TABLE public.hasn_assets
    DROP CONSTRAINT IF EXISTS ck_hasn_assets_content_sha256;

ALTER TABLE public.hasn_assets
    ADD CONSTRAINT ck_hasn_assets_content_sha256 CHECK (
        content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'
    );

CREATE UNIQUE INDEX IF NOT EXISTS uq_hasn_assets_owner_kind_sha256
    ON public.hasn_assets (owner_hasn_id, kind, content_sha256)
    WHERE content_sha256 IS NOT NULL;
