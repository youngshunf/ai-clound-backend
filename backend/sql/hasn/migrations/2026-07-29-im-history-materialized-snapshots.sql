-- 跨设备会话与消息历史恢复：短期服务端物化快照。
-- 同时覆盖 R3 切换前 public 与已存在的 hasn_im schema；迁移可幂等重跑。

CREATE OR REPLACE FUNCTION pg_temp.ensure_im_history_constraint(
    target_schema text,
    target_table text,
    constraint_name text,
    constraint_definition text
) RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS constraint_row
        JOIN pg_class AS table_row
          ON table_row.oid = constraint_row.conrelid
        JOIN pg_namespace AS schema_row
          ON schema_row.oid = table_row.relnamespace
        WHERE schema_row.nspname = target_schema
          AND table_row.relname = target_table
          AND constraint_row.conname = constraint_name
    ) THEN
        EXECUTE format(
            'ALTER TABLE %I.%I ADD CONSTRAINT %I %s',
            target_schema,
            target_table,
            constraint_name,
            constraint_definition
        );
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION pg_temp.create_im_history_snapshot_tables(
    target_schema text
) RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I.hasn_im_history_snapshots (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            owner_id varchar(40) NOT NULL,
            identity_ids jsonb NOT NULL,
            head_revision bigint NOT NULL,
            message_upper_bound bigint NOT NULL DEFAULT 0,
            conversation_count integer NOT NULL DEFAULT 0,
            message_count integer NOT NULL DEFAULT 0,
            history_complete boolean NOT NULL DEFAULT false,
            expires_time timestamptz NOT NULL,
            created_time timestamptz NOT NULL DEFAULT now(),
            updated_time timestamptz,
            CONSTRAINT pk_hasn_im_history_snapshots PRIMARY KEY (id),
            CONSTRAINT ck_hasn_im_history_snapshots_head_revision
                CHECK (head_revision >= 0),
            CONSTRAINT ck_hasn_im_history_snapshots_message_upper_bound
                CHECK (message_upper_bound >= 0),
            CONSTRAINT ck_hasn_im_history_snapshots_counts
                CHECK (conversation_count >= 0 AND message_count >= 0)
        )',
        target_schema
    );

    EXECUTE format(
        'CREATE INDEX IF NOT EXISTS idx_hasn_im_history_snapshots_owner_expiry
            ON %I.hasn_im_history_snapshots (owner_id, expires_time)',
        target_schema
    );
    EXECUTE format(
        'ALTER TABLE %I.hasn_im_history_snapshots
            ALTER COLUMN updated_time DROP NOT NULL',
        target_schema
    );
    EXECUTE format(
        'ALTER TABLE %I.hasn_im_history_snapshots
            ADD COLUMN IF NOT EXISTS history_complete boolean
            NOT NULL DEFAULT false',
        target_schema
    );
    PERFORM pg_temp.ensure_im_history_constraint(
        target_schema,
        'hasn_im_history_snapshots',
        'ck_hasn_im_history_snapshots_head_revision',
        'CHECK (head_revision >= 0)'
    );
    PERFORM pg_temp.ensure_im_history_constraint(
        target_schema,
        'hasn_im_history_snapshots',
        'ck_hasn_im_history_snapshots_message_upper_bound',
        'CHECK (message_upper_bound >= 0)'
    );
    PERFORM pg_temp.ensure_im_history_constraint(
        target_schema,
        'hasn_im_history_snapshots',
        'ck_hasn_im_history_snapshots_counts',
        'CHECK (conversation_count >= 0 AND message_count >= 0)'
    );

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I.hasn_im_history_snapshot_conversations (
            id bigserial NOT NULL,
            snapshot_id uuid NOT NULL,
            item_index integer NOT NULL,
            conversation_id uuid NOT NULL,
            payload jsonb NOT NULL,
            created_time timestamptz NOT NULL DEFAULT now(),
            updated_time timestamptz,
            CONSTRAINT pk_hasn_im_history_snapshot_conversations PRIMARY KEY (id),
            CONSTRAINT fk_hasn_im_history_snapshot_conversations_snapshot
                FOREIGN KEY (snapshot_id)
                REFERENCES %I.hasn_im_history_snapshots (id)
                ON DELETE CASCADE,
            CONSTRAINT uq_hasn_im_history_snapshot_conversations_index
                UNIQUE (snapshot_id, item_index),
            CONSTRAINT uq_hasn_im_history_snapshot_conversations_source
                UNIQUE (snapshot_id, conversation_id),
            CONSTRAINT ck_hasn_im_history_snapshot_conversations_index
                CHECK (item_index > 0)
        )',
        target_schema,
        target_schema
    );

    EXECUTE format(
        'CREATE INDEX IF NOT EXISTS idx_hasn_im_history_snapshot_conversations_page
            ON %I.hasn_im_history_snapshot_conversations
            (snapshot_id, item_index)',
        target_schema
    );
    EXECUTE format(
        'ALTER TABLE %I.hasn_im_history_snapshot_conversations
            ALTER COLUMN updated_time DROP NOT NULL',
        target_schema
    );
    PERFORM pg_temp.ensure_im_history_constraint(
        target_schema,
        'hasn_im_history_snapshot_conversations',
        'fk_hasn_im_history_snapshot_conversations_snapshot',
        format(
            'FOREIGN KEY (snapshot_id) REFERENCES %I.hasn_im_history_snapshots (id) ON DELETE CASCADE',
            target_schema
        )
    );
    PERFORM pg_temp.ensure_im_history_constraint(
        target_schema,
        'hasn_im_history_snapshot_conversations',
        'uq_hasn_im_history_snapshot_conversations_index',
        'UNIQUE (snapshot_id, item_index)'
    );
    PERFORM pg_temp.ensure_im_history_constraint(
        target_schema,
        'hasn_im_history_snapshot_conversations',
        'uq_hasn_im_history_snapshot_conversations_source',
        'UNIQUE (snapshot_id, conversation_id)'
    );
    PERFORM pg_temp.ensure_im_history_constraint(
        target_schema,
        'hasn_im_history_snapshot_conversations',
        'ck_hasn_im_history_snapshot_conversations_index',
        'CHECK (item_index > 0)'
    );

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I.hasn_im_history_snapshot_messages (
            id bigserial NOT NULL,
            snapshot_id uuid NOT NULL,
            item_index integer NOT NULL,
            message_id bigint NOT NULL,
            payload jsonb NOT NULL,
            created_time timestamptz NOT NULL DEFAULT now(),
            updated_time timestamptz,
            CONSTRAINT pk_hasn_im_history_snapshot_messages PRIMARY KEY (id),
            CONSTRAINT fk_hasn_im_history_snapshot_messages_snapshot
                FOREIGN KEY (snapshot_id)
                REFERENCES %I.hasn_im_history_snapshots (id)
                ON DELETE CASCADE,
            CONSTRAINT uq_hasn_im_history_snapshot_messages_index
                UNIQUE (snapshot_id, item_index),
            CONSTRAINT uq_hasn_im_history_snapshot_messages_source
                UNIQUE (snapshot_id, message_id),
            CONSTRAINT ck_hasn_im_history_snapshot_messages_index
                CHECK (item_index > 0),
            CONSTRAINT ck_hasn_im_history_snapshot_messages_message_id
                CHECK (message_id > 0)
        )',
        target_schema,
        target_schema
    );

    EXECUTE format(
        'CREATE INDEX IF NOT EXISTS idx_hasn_im_history_snapshot_messages_page
            ON %I.hasn_im_history_snapshot_messages
            (snapshot_id, item_index)',
        target_schema
    );
    EXECUTE format(
        'ALTER TABLE %I.hasn_im_history_snapshot_messages
            ALTER COLUMN updated_time DROP NOT NULL',
        target_schema
    );
    PERFORM pg_temp.ensure_im_history_constraint(
        target_schema,
        'hasn_im_history_snapshot_messages',
        'fk_hasn_im_history_snapshot_messages_snapshot',
        format(
            'FOREIGN KEY (snapshot_id) REFERENCES %I.hasn_im_history_snapshots (id) ON DELETE CASCADE',
            target_schema
        )
    );
    PERFORM pg_temp.ensure_im_history_constraint(
        target_schema,
        'hasn_im_history_snapshot_messages',
        'uq_hasn_im_history_snapshot_messages_index',
        'UNIQUE (snapshot_id, item_index)'
    );
    PERFORM pg_temp.ensure_im_history_constraint(
        target_schema,
        'hasn_im_history_snapshot_messages',
        'uq_hasn_im_history_snapshot_messages_source',
        'UNIQUE (snapshot_id, message_id)'
    );
    PERFORM pg_temp.ensure_im_history_constraint(
        target_schema,
        'hasn_im_history_snapshot_messages',
        'ck_hasn_im_history_snapshot_messages_index',
        'CHECK (item_index > 0)'
    );
    PERFORM pg_temp.ensure_im_history_constraint(
        target_schema,
        'hasn_im_history_snapshot_messages',
        'ck_hasn_im_history_snapshot_messages_message_id',
        'CHECK (message_id > 0)'
    );

    EXECUTE format(
        'COMMENT ON TABLE %I.hasn_im_history_snapshots IS
            ''跨设备会话与消息历史物化快照''',
        target_schema
    );
    EXECUTE format(
        'COMMENT ON TABLE %I.hasn_im_history_snapshot_conversations IS
            ''跨设备历史快照的不可变会话投影''',
        target_schema
    );
    EXECUTE format(
        'COMMENT ON TABLE %I.hasn_im_history_snapshot_messages IS
            ''跨设备历史快照的不可变消息投影''',
        target_schema
    );
END;
$$;

SELECT pg_temp.create_im_history_snapshot_tables('public');

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.schemata
        WHERE schema_name = 'hasn_im'
    ) THEN
        PERFORM pg_temp.create_im_history_snapshot_tables('hasn_im');
    END IF;
END;
$$;

DO $$
DECLARE
    target_schema text;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = 'astra_im_service'
    ) THEN
        RETURN;
    END IF;

    FOREACH target_schema IN ARRAY ARRAY['public', 'hasn_im']
    LOOP
        IF target_schema = 'public' OR EXISTS (
            SELECT 1
            FROM information_schema.schemata
            WHERE schema_name = target_schema
        ) THEN
            EXECUTE format(
                'GRANT USAGE ON SCHEMA %I TO astra_im_service',
                target_schema
            );
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON
                    %I.hasn_im_history_snapshots,
                    %I.hasn_im_history_snapshot_conversations,
                    %I.hasn_im_history_snapshot_messages
                 TO astra_im_service',
                target_schema,
                target_schema,
                target_schema
            );
            EXECUTE format(
                'GRANT USAGE, SELECT ON SEQUENCE
                    %I.hasn_im_history_snapshot_conversations_id_seq,
                    %I.hasn_im_history_snapshot_messages_id_seq
                 TO astra_im_service',
                target_schema,
                target_schema
            );
        END IF;
    END LOOP;
END;
$$;
