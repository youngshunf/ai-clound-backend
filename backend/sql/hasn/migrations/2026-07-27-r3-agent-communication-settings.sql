-- R3：本迁移既可在 forward 前执行，也可在已完成 schema cutover 的库上补跑。
-- 身份表保留兼容列，但消息门控与写入口只读取目标 schema 的权威设置表。

DO $$
DECLARE
    target_schema text;
BEGIN
    target_schema := CASE
        WHEN to_regclass('hasn_im.hasn_contacts') IS NOT NULL THEN 'hasn_im'
        ELSE 'public'
    END;

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I.agent_communication_settings ('
        'agent_hasn_id varchar(40) PRIMARY KEY,'
        'social_enabled boolean NOT NULL DEFAULT true,'
        'inbound_policy varchar(20) NOT NULL DEFAULT ''auto'','
        'created_time timestamptz(6) NOT NULL DEFAULT now(),'
        'updated_time timestamptz(6) NOT NULL DEFAULT now()'
        ')',
        target_schema
    );
    EXECUTE format(
        'INSERT INTO %I.agent_communication_settings ('
        'agent_hasn_id, social_enabled, inbound_policy, created_time, updated_time'
        ') '
        'SELECT agent.hasn_id, COALESCE(agent.social_enabled, true), '
        'COALESCE(agent.inbound_policy, ''auto''), now(), now() '
        'FROM public.hasn_agents AS agent '
        'ON CONFLICT (agent_hasn_id) DO NOTHING',
        target_schema
    );
    EXECUTE format(
        'COMMENT ON TABLE %I.agent_communication_settings IS '
        '''Agent 通信设置（IM 权威；身份域兼容列不再作为消息门控写事实）''',
        target_schema
    );
    EXECUTE format(
        'COMMENT ON COLUMN %I.agent_communication_settings.social_enabled IS '
        '''Agent 是否开启社交通信''',
        target_schema
    );
    EXECUTE format(
        'COMMENT ON COLUMN %I.agent_communication_settings.inbound_policy IS '
        '''入站策略 (auto:自动/manual_all:全部人工/manual_strangers:陌生人人工)''',
        target_schema
    );
END
$$;
