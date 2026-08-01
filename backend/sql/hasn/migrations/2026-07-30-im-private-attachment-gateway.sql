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

    IF to_regclass('hasn_im.hasn_asset_grants') IS NOT NULL THEN
        INSERT INTO hasn_im.hasn_asset_grants
            (asset_id, conversation_id, created_time)
        VALUES
            (p_asset_id, p_conversation_id, now())
        ON CONFLICT (asset_id, conversation_id) DO NOTHING;
    ELSIF to_regclass('public.hasn_asset_grants') IS NOT NULL THEN
        INSERT INTO public.hasn_asset_grants
            (asset_id, conversation_id, created_time)
        VALUES
            (p_asset_id, p_conversation_id, now())
        ON CONFLICT (asset_id, conversation_id) DO NOTHING;
    ELSE
        RAISE EXCEPTION 'STORAGE_ASSET_GRANTS_TABLE_NOT_FOUND'
            USING ERRCODE = '42P01';
    END IF;

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

-- Owner 资产解析只需要回答“请求者能否通过指定会话读取哪些附件”，不得因此给普通
-- Python 角色开放 hasn_im 会话、成员或授权基表。该只读接缝把参与关系与附件 grant
-- 在 IM 边界内一次性求交，只返回调用方原始请求中的资产 ID。
CREATE OR REPLACE FUNCTION public.hasn_authorized_conversation_assets(
    p_asset_ids varchar[],
    p_conversation_id uuid,
    p_requester_hasn_id varchar
)
RETURNS TABLE(asset_id varchar)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    v_im_schema text;
BEGIN
    IF to_regclass('hasn_im.hasn_conversations') IS NOT NULL THEN
        v_im_schema := 'hasn_im';
    ELSIF to_regclass('public.hasn_conversations') IS NOT NULL THEN
        v_im_schema := 'public';
    ELSE
        RAISE EXCEPTION 'IM_CONVERSATIONS_TABLE_NOT_FOUND'
            USING ERRCODE = '42P01';
    END IF;

    RETURN QUERY EXECUTE format(
        $query$
        WITH conversation_participants AS (
            SELECT c.participant_a_id AS hasn_id
            FROM %1$I.hasn_conversations AS c
            WHERE c.id = $1
            UNION
            SELECT c.participant_b_id AS hasn_id
            FROM %1$I.hasn_conversations AS c
            WHERE c.id = $1
              AND c.participant_b_id IS NOT NULL
            UNION
            SELECT m.member_hasn_id AS hasn_id
            FROM %1$I.hasn_conversation_memberships AS m
            WHERE m.conversation_id = $1
              AND m.left_seq IS NULL
              AND m.state = 'active'
        ),
        requester_is_participant AS (
            SELECT EXISTS (
                SELECT 1
                FROM conversation_participants AS p
                WHERE p.hasn_id = $2
            ) OR EXISTS (
                SELECT 1
                FROM conversation_participants AS p
                JOIN public.hasn_agents AS a
                  ON a.hasn_id = p.hasn_id
                WHERE a.owner_id = $2
            ) AS allowed
        )
        SELECT g.asset_id
        FROM %1$I.hasn_asset_grants AS g
        CROSS JOIN requester_is_participant AS participant
        WHERE participant.allowed
          AND g.conversation_id = $1
          AND g.asset_id = ANY($3)
        $query$,
        v_im_schema
    )
    USING p_conversation_id, p_requester_hasn_id, p_asset_ids;
END;
$function$;

REVOKE ALL ON FUNCTION public.hasn_authorized_conversation_assets(
    varchar[], uuid, varchar
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.hasn_authorized_conversation_assets(
    varchar[], uuid, varchar
) TO astra_python_backend;
