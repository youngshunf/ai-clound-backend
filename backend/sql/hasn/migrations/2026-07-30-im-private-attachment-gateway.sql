-- IM 消息事务需要原子写入附件会话授权与删除保护，但不得获得 Owner 存储表的直接写权限。
-- 该接缝只接受活动私有资产与规范消息 URI；函数外仍由 IM 角色的表级权限阻断任意写入。
CREATE OR REPLACE FUNCTION public.hasn_bind_private_attachment(
    p_asset_id varchar,
    p_conversation_id uuid,
    p_resource_uri varchar,
    p_binding_id varchar
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    v_owner_hasn_id varchar(40);
BEGIN
    IF p_resource_uri IS NULL
       OR length(p_resource_uri) > 1024
       OR p_resource_uri !~ '^hasn://messages/c/[0-9a-fA-F-]+#[0-9]+$' THEN
        RAISE EXCEPTION 'STORAGE_RESOURCE_URI_INVALID'
            USING ERRCODE = '22023';
    END IF;
    IF p_binding_id IS NULL
       OR p_binding_id !~ '^bnd_[0-9a-f]{32}$' THEN
        RAISE EXCEPTION 'STORAGE_BINDING_ID_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT a.owner_hasn_id
    INTO v_owner_hasn_id
    FROM public.hasn_assets AS a
    WHERE a.asset_id = p_asset_id
      AND a.access = 'private'
      AND a.lifecycle_status = 'active'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'STORAGE_PRIVATE_ASSET_NOT_FOUND'
            USING ERRCODE = 'P0002';
    END IF;

    INSERT INTO public.hasn_asset_grants
        (asset_id, conversation_id, created_time)
    VALUES
        (p_asset_id, p_conversation_id, now())
    ON CONFLICT (asset_id, conversation_id) DO NOTHING;

    INSERT INTO public.hasn_asset_bindings
        (binding_id, owner_hasn_id, asset_id, resource_uri, role,
         status, created_time, updated_time)
    VALUES
        (p_binding_id, v_owner_hasn_id, p_asset_id, p_resource_uri, 'attachment',
         'active', now(), now())
    ON CONFLICT (asset_id, resource_uri, role)
    DO UPDATE SET
        status = 'active',
        updated_time = EXCLUDED.updated_time;
END;
$function$;

REVOKE ALL ON FUNCTION public.hasn_bind_private_attachment(
    varchar, uuid, varchar, varchar
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.hasn_bind_private_attachment(
    varchar, uuid, varchar, varchar
) TO astra_im_service;
