-- R3 三真实 LOGIN 权限矩阵验收。
--
-- 调用方必须以待测 LOGIN 直接连接，并注入 expected_role；禁止以超级用户 SET ROLE 代替。
-- 演练器会在迁移后创建 public.astra_r3_permission_probe，供 Python 普通业务 DML 正测使用。
-- 所有测试写入都位于本脚本显式事务内，末尾无条件 ROLLBACK，不留下消息、事件或探针数据。

\set ON_ERROR_STOP on

\if :{?expected_role}
\else
  \echo '缺少 psql 变量 expected_role'
  \quit 3
\endif

BEGIN;

SELECT set_config('hasn.r3_expected_role', :'expected_role', true) AS configured_role
\gset

CREATE OR REPLACE FUNCTION pg_temp.assert_denied(p_sql text) RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    BEGIN
        EXECUTE p_sql;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RETURN;
    END;
    RAISE EXCEPTION 'PERM-NEG FAIL：current_user=% 竟能执行 [%]（应返回 42501）',
        current_user, p_sql;
END;
$$;

CREATE OR REPLACE FUNCTION pg_temp.assert_check_violation(p_sql text) RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    BEGIN
        EXECUTE p_sql;
    EXCEPTION
        WHEN check_violation THEN
            RETURN;
    END;
    RAISE EXCEPTION 'APPEND-VALIDATION FAIL：current_user=% 未拒绝非法调用 [%]',
        current_user, p_sql;
END;
$$;

DO $$
DECLARE
    expected_role text := current_setting('hasn.r3_expected_role');
    probe_marker text := 'r3_perm_' || current_user;
    row_count bigint;
    first_revision bigint;
    first_event_id text;
    first_deduped boolean;
    second_revision bigint;
    second_event_id text;
    second_deduped boolean;
BEGIN
    IF current_user <> expected_role THEN
        RAISE EXCEPTION '登录角色错误：current_user=%，expected_role=%',
            current_user, expected_role;
    END IF;

    IF current_user = 'astra_python_backend' THEN
        INSERT INTO public.astra_r3_permission_probe (marker) VALUES (probe_marker);
        SELECT count(*) INTO row_count
        FROM public.astra_r3_permission_probe p
        WHERE p.marker = probe_marker;
        IF row_count <> 1 THEN
            RAISE EXCEPTION 'PERM-POS FAIL：Python 角色普通业务 INSERT 结果=%，期望=1', row_count;
        END IF;

        PERFORM pg_temp.assert_denied(
            format(
                'INSERT INTO hasn_im.agent_communication_settings(agent_hasn_id) VALUES (%L)',
                probe_marker
            )
        );
        PERFORM pg_temp.assert_denied(
            'SELECT count(*) FROM hasn_im.hasn_messages'
        );
        PERFORM pg_temp.assert_denied(
            format(
                'INSERT INTO hasn_sync.hasn_sync_events'
                '(event_id,owner_id,hasn_id,event_type,aggregate_type,aggregate_id,revision) '
                'VALUES (%L,%L,%L,%L,%L,%L,1)',
                probe_marker, probe_marker, probe_marker, 'im.permission.v1', 'probe', probe_marker
            )
        );

        SELECT revision, event_id, deduped
        INTO first_revision, first_event_id, first_deduped
        FROM hasn_sync.append_event(
            probe_marker, probe_marker, 'im.permission.v1', 'probe', probe_marker, '{}'::jsonb,
            'r3_permission_test', probe_marker, NULL
        );
        SELECT revision, event_id, deduped
        INTO second_revision, second_event_id, second_deduped
        FROM hasn_sync.append_event(
            probe_marker, probe_marker, 'im.permission.v1', 'probe', probe_marker, '{}'::jsonb,
            'r3_permission_test', probe_marker, NULL
        );
        IF first_revision < 1
           OR first_event_id IS NULL
           OR first_deduped
           OR second_revision <> first_revision
           OR second_event_id <> first_event_id
           OR NOT second_deduped THEN
            RAISE EXCEPTION
                'PERM-POS FAIL：append_event 真实结果不符，first=(%,%,%) second=(%,%,%)',
                first_revision, first_event_id, first_deduped,
                second_revision, second_event_id, second_deduped;
        END IF;
        PERFORM pg_temp.assert_check_violation(
            format(
                'SELECT * FROM hasn_sync.append_event'
                '(%L,%L,%L,%L,%L,%L::jsonb,%L,%L,NULL)',
                probe_marker, probe_marker, 'im.permission.v1', 'probe',
                probe_marker, '{}', 'Invalid Producer', probe_marker
            )
        );

    ELSIF current_user = 'astra_im_service' THEN
        INSERT INTO hasn_im.agent_communication_settings (agent_hasn_id)
        VALUES (probe_marker);
        SELECT count(*) INTO row_count
        FROM hasn_im.agent_communication_settings s
        WHERE s.agent_hasn_id = probe_marker;
        IF row_count <> 1 THEN
            RAISE EXCEPTION 'PERM-POS FAIL：IM 角色域内 INSERT 结果=%，期望=1', row_count;
        END IF;

        PERFORM 1 FROM public.hasn_agents LIMIT 1;
        PERFORM 1 FROM public.hasn_nodes LIMIT 1;
        PERFORM pg_temp.assert_denied(
            format(
                'INSERT INTO public.astra_r3_permission_probe(marker) VALUES (%L)',
                probe_marker
            )
        );
        PERFORM pg_temp.assert_denied(
            format(
                'INSERT INTO hasn_sync.hasn_sync_events'
                '(event_id,owner_id,hasn_id,event_type,aggregate_type,aggregate_id,revision) '
                'VALUES (%L,%L,%L,%L,%L,%L,1)',
                probe_marker, probe_marker, probe_marker, 'im.permission.v1', 'probe', probe_marker
            )
        );

        SELECT revision, event_id, deduped
        INTO first_revision, first_event_id, first_deduped
        FROM hasn_sync.append_event(
            probe_marker, probe_marker, 'im.permission.v1', 'probe', probe_marker, '{}'::jsonb,
            'r3_permission_test', probe_marker, NULL
        );
        IF first_revision < 1 OR first_event_id IS NULL OR first_deduped THEN
            RAISE EXCEPTION 'PERM-POS FAIL：IM append_event 结果不符';
        END IF;
        PERFORM pg_temp.assert_check_violation(
            format(
                'SELECT * FROM hasn_sync.append_event'
                '(%L,%L,%L,%L,%L,jsonb_build_object(''content'',repeat(''中'',100000)),%L,%L,NULL)',
                probe_marker, probe_marker, 'im.permission.v1', 'probe',
                probe_marker, 'r3_permission_test', probe_marker
            )
        );

    ELSIF current_user = 'astra_sync_service' THEN
        PERFORM pg_temp.assert_denied(
            format(
                'INSERT INTO hasn_sync.hasn_sync_events'
                '(event_id,owner_id,hasn_id,event_type,aggregate_type,aggregate_id,revision) '
                'VALUES (%L,%L,%L,%L,%L,%L,1)',
                probe_marker, probe_marker, probe_marker, 'sync.permission.v1', 'probe', probe_marker
            )
        );

        INSERT INTO hasn_sync.hasn_sync_inbox_events (
            client_event_id, owner_id, hasn_id, node_id, event_type, payload, status,
            attempt_count, received_at, created_time
        ) VALUES (
            probe_marker, probe_marker, probe_marker, probe_marker,
            'sync.permission.v1', '{}'::jsonb, 'accepted', 0, now(), now()
        );
        UPDATE hasn_sync.hasn_sync_inbox_events
        SET status = 'processing'
        WHERE owner_id = probe_marker
          AND node_id = probe_marker
          AND client_event_id = probe_marker;
        SELECT count(*) INTO row_count
        FROM hasn_sync.hasn_sync_inbox_events e
        WHERE e.owner_id = probe_marker
          AND e.node_id = probe_marker
          AND e.client_event_id = probe_marker
          AND e.status = 'processing';
        IF row_count <> 1 THEN
            RAISE EXCEPTION 'PERM-POS FAIL：sync 角色 inbox 状态更新结果=%，期望=1', row_count;
        END IF;

        PERFORM pg_temp.assert_denied(
            'SELECT count(*) FROM hasn_im.hasn_messages'
        );
        PERFORM pg_temp.assert_denied(
            'SELECT count(*) FROM public.astra_r3_permission_probe'
        );
        RAISE NOTICE 'sync 角色仍可绕过 append_event：否';

    ELSIF current_user = 'astra_r3_unauthorized_probe' THEN
        PERFORM pg_temp.assert_denied(
            format(
                'SELECT * FROM hasn_sync.append_event'
                '(%L,%L,%L,%L,%L,%L::jsonb,%L,%L,NULL)',
                probe_marker, probe_marker, 'im.permission.v1', 'probe', probe_marker, '{}',
                'r3_permission_test', probe_marker
            )
        );

    ELSE
        RAISE EXCEPTION '未登记的权限测试角色：%', current_user;
    END IF;

    RAISE NOTICE 'R3 权限矩阵通过：current_user=%；所有写入将在脚本末尾回滚', current_user;
END;
$$;

ROLLBACK;
