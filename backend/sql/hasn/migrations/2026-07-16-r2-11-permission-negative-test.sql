-- R2-11/R2-12 · DB 角色边界权限负测（§3.2 / §10.1-7）
--
-- 在**正向迁移已应用**的库（快照副本）上运行：断言三个服务角色的 DML/读/EXECUTE 边界与 §3.2 一致。
-- 任一断言不符即 RAISE EXCEPTION 硬失败（脚本整体报错），符合则静默通过——可直接进 R2-12 演练与 CI。
--
-- 断言矩阵（§3.2）：
--   astra_python_backend：禁 hasn_im/hasn_sync 表 DML；hasn_im 只读运营视图 OK；仅 EXECUTE append_event OK
--   astra_im_service：hasn_im.* DML OK；禁 hasn_sync 表 DML
--   astra_sync_service：hasn_sync.* DML OK；禁任意业务表读取
--
-- 关键：append_event = SECURITY DEFINER，故 backend「仅 EXECUTE」即可经函数完成跨域写（§3.2「允许的
--   跨域写 = 仅 EXECUTE append_event」），而**直接** INSERT hasn_sync 表仍被拒——负测同时钉死两面。
--
-- 依赖：以能 SET ROLE 到 astra_* 的连接运行（本地演练用超级用户 mac，SET ROLE 到非超级角色即按其权限判定）。

-- 断言辅助：期望被拒（insufficient_privilege / 42501）
CREATE OR REPLACE FUNCTION pg_temp.assert_denied(p_role text, p_sql text) RETURNS void AS $$
BEGIN
    EXECUTE format('SET LOCAL ROLE %I', p_role);
    BEGIN
        EXECUTE p_sql;
        RESET ROLE;
        RAISE EXCEPTION 'PERM-NEG FAIL：role=% 竟能执行 [%]（§3.2 应拒）', p_role, p_sql;
    EXCEPTION
        WHEN insufficient_privilege THEN
            NULL;  -- 预期：权限不足被拒
    END;
    RESET ROLE;
    RAISE NOTICE 'PASS(denied): % 被拒 [%]', p_role, left(p_sql, 60);
END;
$$ LANGUAGE plpgsql;

-- 断言辅助：期望放行（无 42501；约束等非权限错误也视为「已放行」）
CREATE OR REPLACE FUNCTION pg_temp.assert_allowed(p_role text, p_sql text) RETURNS void AS $$
BEGIN
    EXECUTE format('SET LOCAL ROLE %I', p_role);
    BEGIN
        EXECUTE p_sql;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RESET ROLE;
            RAISE EXCEPTION 'PERM-POS FAIL：role=% 应可执行 [%] 但被拒(42501)', p_role, p_sql;
        WHEN OTHERS THEN
            NULL;  -- 非权限错误（NOT NULL/约束等）= 权限已放行，符合预期
    END;
    RESET ROLE;
    RAISE NOTICE 'PASS(allowed): % 放行 [%]', p_role, left(p_sql, 60);
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    -- ── astra_python_backend：禁 hasn_im/hasn_sync 表 DML ──
    PERFORM pg_temp.assert_denied('astra_python_backend',
        'INSERT INTO hasn_im.agent_communication_settings(agent_hasn_id) VALUES (''perm_neg_backend'')');
    PERFORM pg_temp.assert_denied('astra_python_backend',
        'INSERT INTO hasn_sync.hasn_sync_events(event_id, owner_id, revision) VALUES (''pn1'', ''o1'', 1)');
    -- backend：hasn_im 只读运营视图 OK
    PERFORM pg_temp.assert_allowed('astra_python_backend',
        'SELECT 1 FROM hasn_im.hasn_messages LIMIT 1');
    -- backend：仅 EXECUTE append_event OK（SECURITY DEFINER 内部写入放行）——「允许的跨域写」唯一通道
    PERFORM pg_temp.assert_allowed('astra_python_backend',
        'SELECT * FROM hasn_sync.append_event(''pb_owner'',''pb_owner'',''im.perm.v1'',''conversation'',''c_pb'',''{}''::jsonb,''hasn_im'',''perm_exec_evt'',NULL)');

    -- ── astra_im_service：hasn_im DML OK；禁 hasn_sync 表 DML ──
    PERFORM pg_temp.assert_allowed('astra_im_service',
        'INSERT INTO hasn_im.agent_communication_settings(agent_hasn_id) VALUES (''perm_im_ok'')');
    PERFORM pg_temp.assert_denied('astra_im_service',
        'INSERT INTO hasn_sync.hasn_sync_events(event_id, owner_id, revision) VALUES (''pn2'', ''o2'', 1)');

    -- ── astra_sync_service：hasn_sync DML OK；禁任意业务表读取 ──
    PERFORM pg_temp.assert_allowed('astra_sync_service',
        'INSERT INTO hasn_sync.hasn_sync_events(event_id, owner_id, revision) VALUES (''ps_ok'', ''os'', 999)');
    PERFORM pg_temp.assert_denied('astra_sync_service',
        'SELECT 1 FROM hasn_im.hasn_messages LIMIT 1');

    RAISE NOTICE '======== R2-12 权限负测全部通过（§3.2 三角色边界钉死）========';
END $$;
