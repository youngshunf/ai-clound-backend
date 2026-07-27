-- R3：确定性门控只保存待放行命令，不提前写消息或占用 conversation_seq。
--
-- 历史版本先写 hasn_messages，再用 message_id 标记“待主人放行”。本迁移把尚未解决的
-- 历史记录还原为完整发送命令，撤掉提前产生的消息，并重建会话与未读投影。已经分配的
-- conversation_seq 不回退；空洞是协议允许的历史事实。
--
-- 本迁移可在 R3 forward 前对 public 执行，也可用于已完成本机 cutover 的 hasn_im 演练库。

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION pg_temp.hasn_r3_canonical_jsonb(value jsonb)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $function$
DECLARE
    value_kind text := jsonb_typeof(value);
    canonical text;
BEGIN
    CASE value_kind
        WHEN 'object' THEN
            SELECT '{' || COALESCE(
                string_agg(
                    to_jsonb(entry.key)::text || ':' ||
                    pg_temp.hasn_r3_canonical_jsonb(entry.value),
                    ',' ORDER BY entry.key
                ),
                ''
            ) || '}'
            INTO canonical
            FROM jsonb_each(value) AS entry;
            RETURN canonical;
        WHEN 'array' THEN
            SELECT '[' || COALESCE(
                string_agg(
                    pg_temp.hasn_r3_canonical_jsonb(entry.value),
                    ',' ORDER BY entry.ordinality
                ),
                ''
            ) || ']'
            INTO canonical
            FROM jsonb_array_elements(value)
                 WITH ORDINALITY AS entry(value, ordinality);
            RETURN canonical;
        WHEN 'string' THEN
            RETURN to_jsonb(value #>> '{}')::text;
        ELSE
            RETURN value::text;
    END CASE;
END
$function$;

DO $migration$
DECLARE
    target_schema text;
    orphan_count bigint;
    invalid_count bigint;
    client_message_expression text;
BEGIN
    FOREACH target_schema IN ARRAY ARRAY['public', 'hasn_im']
    LOOP
        IF to_regclass(format('%I.hasn_suppressed_messages', target_schema)) IS NULL THEN
            CONTINUE;
        END IF;
        IF to_regclass(format('%I.hasn_messages', target_schema)) IS NULL
           OR to_regclass(format('%I.hasn_conversations', target_schema)) IS NULL THEN
            RAISE EXCEPTION
                'R3 抑制命令迁移缺少同域消息或会话表：schema=%',
                target_schema;
        END IF;

        EXECUTE format(
            'ALTER TABLE %I.hasn_suppressed_messages '
            'ALTER COLUMN message_id DROP NOT NULL',
            target_schema
        );
        EXECUTE format(
            'ALTER TABLE %I.hasn_suppressed_messages '
            'ADD COLUMN IF NOT EXISTS sender_hasn_id varchar(40), '
            'ADD COLUMN IF NOT EXISTS idempotency_scope varchar(64), '
            'ADD COLUMN IF NOT EXISTS command_hash varchar(64), '
            'ADD COLUMN IF NOT EXISTS command_payload jsonb NOT NULL DEFAULT ''{}''::jsonb',
            target_schema
        );

        client_message_expression := CASE
            WHEN EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = target_schema
                  AND table_name = 'hasn_messages'
                  AND column_name = 'client_message_id'
            )
            THEN 'NULLIF(m.client_message_id, '''')'
            ELSE 'NULL::text'
        END;

        -- 不允许静默丢弃找不到原消息的历史记录；此类数据必须先人工修复再继续迁移。
        EXECUTE format(
            'SELECT count(*) '
            'FROM %I.hasn_suppressed_messages s '
            'LEFT JOIN %I.hasn_messages m ON m.id = s.message_id '
            'WHERE s.resolved_at IS NULL '
            'AND s.message_id IS NOT NULL '
            'AND m.id IS NULL',
            target_schema,
            target_schema
        )
        INTO orphan_count;
        IF orphan_count <> 0 THEN
            RAISE EXCEPTION
                'R3 抑制命令迁移发现 % 条未解决记录缺少原消息：schema=%',
                orphan_count,
                target_schema;
        END IF;

        -- 从历史消息完整还原 release_suppressed 所需的 SendMessageCommand。
        EXECUTE format(
            'UPDATE %1$I.hasn_suppressed_messages s '
            'SET sender_hasn_id = m.from_id, '
            'command_payload = jsonb_build_object('
            '''conversation_id'', s.conversation_id::text, '
            '''from_id'', m.from_id, '
            '''to_id'', m.to_id, '
            '''content'', m.content, '
            '''content_type'', m.content_type, '
            '''msg_type'', m.msg_type, '
            '''priority'', m.priority, '
            '''reply_to_id'', m.reply_to_id, '
            '''idempotency_key'', COALESCE('
            'NULLIF(m.local_id::text, ''''), '
            '%2$s, '
            '''r3-legacy-suppressed-'' || s.id::text'
            '), '
            '''context'', m.context, '
            '''origin_node_id'', m.origin_node_id, '
            '''origin_session_id'', m.origin_session_id, '
            '''owner_id'', s.owner_id'
            '), '
            'updated_time = now() '
            'FROM %1$I.hasn_messages m '
            'WHERE s.resolved_at IS NULL '
            'AND s.message_id = m.id '
            'AND (s.command_payload = ''{}''::jsonb '
            'OR s.sender_hasn_id IS NULL)',
            target_schema,
            client_message_expression
        );

        -- 作用域算法与 suppression_command_identity 完全一致：
        -- sender + NUL + origin_node + NUL + idempotency_key。
        EXECUTE format(
            'UPDATE %1$I.hasn_suppressed_messages s '
            'SET idempotency_scope = encode(digest('
            'convert_to(s.sender_hasn_id, ''UTF8'') || decode(''00'', ''hex'') || '
            'convert_to(COALESCE(s.command_payload->>''origin_node_id'', ''''), ''UTF8'') || '
            'decode(''00'', ''hex'') || '
            'convert_to(s.command_payload->>''idempotency_key'', ''UTF8''), '
            '''sha256''), ''hex''), '
            'command_hash = encode(digest(convert_to('
            'pg_temp.hasn_r3_canonical_jsonb(s.command_payload), '
            '''UTF8''), ''sha256''), ''hex''), '
            'updated_time = now() '
            'WHERE s.resolved_at IS NULL '
            'AND s.message_id IS NOT NULL',
            target_schema
        );

        -- 旧未读计数仍可能参与 reverse 或 cutover 回填，先按被删除的收件消息数扣减。
        IF to_regclass('public.hasn_unread_counts') IS NOT NULL THEN
            EXECUTE format(
                'WITH removed AS ('
                'SELECT s.conversation_id, m.to_id AS hasn_id, count(*) AS amount '
                'FROM %1$I.hasn_suppressed_messages s '
                'JOIN %1$I.hasn_messages m ON m.id = s.message_id '
                'WHERE s.resolved_at IS NULL '
                'GROUP BY s.conversation_id, m.to_id'
                ') '
                'UPDATE public.hasn_unread_counts u '
                'SET unread_count = GREATEST(0, u.unread_count - removed.amount), '
                'updated_time = now() '
                'FROM removed '
                'WHERE u.conversation_id = removed.conversation_id '
                'AND u.hasn_id = removed.hasn_id',
                target_schema
            );
            EXECUTE format(
                'WITH removed AS ('
                'SELECT s.message_id, s.conversation_id, m.conversation_seq '
                'FROM %1$I.hasn_suppressed_messages s '
                'JOIN %1$I.hasn_messages m ON m.id = s.message_id '
                'WHERE s.resolved_at IS NULL'
                ') '
                'UPDATE public.hasn_unread_counts u '
                'SET last_read_msg_id = COALESCE(('
                'SELECT prior.id '
                'FROM %1$I.hasn_messages prior '
                'WHERE prior.conversation_id = removed.conversation_id '
                'AND prior.conversation_seq < removed.conversation_seq '
                'ORDER BY prior.conversation_seq DESC, prior.id DESC '
                'LIMIT 1'
                '), 0), '
                'updated_time = now() '
                'FROM removed '
                'WHERE u.conversation_id = removed.conversation_id '
                'AND u.last_read_msg_id = removed.message_id',
                target_schema
            );
        END IF;

        -- 先解除 message_id 引用，再物理删除旧版提前落库的消息。current_seq 明确保留。
        EXECUTE format(
            'WITH legacy AS ('
            'SELECT s.id AS suppressed_id, s.message_id '
            'FROM %1$I.hasn_suppressed_messages s '
            'WHERE s.resolved_at IS NULL '
            'AND s.message_id IS NOT NULL'
            '), detached AS ('
            'UPDATE %1$I.hasn_suppressed_messages s '
            'SET message_id = NULL, updated_time = now() '
            'FROM legacy '
            'WHERE s.id = legacy.suppressed_id '
            'RETURNING legacy.message_id'
            ') '
            'DELETE FROM %1$I.hasn_messages m '
            'USING detached '
            'WHERE m.id = detached.message_id',
            target_schema
        );

        -- 会话最后消息与计数是可重建投影；按现存消息修复，序号游标不回退。
        EXECUTE format(
            'UPDATE %1$I.hasn_conversations c '
            'SET message_count = ('
            'SELECT count(*) FROM %1$I.hasn_messages m '
            'WHERE m.conversation_id = c.id'
            '), '
            'last_message_id = ('
            'SELECT m.id FROM %1$I.hasn_messages m '
            'WHERE m.conversation_id = c.id '
            'ORDER BY m.conversation_seq DESC, m.id DESC LIMIT 1'
            '), '
            'last_message_at = ('
            'SELECT m.server_received_at FROM %1$I.hasn_messages m '
            'WHERE m.conversation_id = c.id '
            'ORDER BY m.conversation_seq DESC, m.id DESC LIMIT 1'
            '), '
            'last_message_from = ('
            'SELECT m.from_id FROM %1$I.hasn_messages m '
            'WHERE m.conversation_id = c.id '
            'ORDER BY m.conversation_seq DESC, m.id DESC LIMIT 1'
            '), '
            'last_message_preview = ('
            'SELECT CASE m.content_type '
            'WHEN 1 THEN left(COALESCE(m.content->>''text'', ''''), 200) '
            'WHEN 2 THEN ''[图片]'' '
            'WHEN 3 THEN ''[文件]'' '
            'WHEN 4 THEN ''[语音]'' '
            'WHEN 5 THEN ''[卡片]'' '
            'ELSE ''[消息]'' END '
            'FROM %1$I.hasn_messages m '
            'WHERE m.conversation_id = c.id '
            'ORDER BY m.conversation_seq DESC, m.id DESC LIMIT 1'
            '), '
            'updated_time = now() '
            'WHERE EXISTS ('
            'SELECT 1 FROM %1$I.hasn_suppressed_messages s '
            'WHERE s.resolved_at IS NULL '
            'AND s.conversation_id = c.id '
            'AND s.command_payload <> ''{}''::jsonb'
            ')',
            target_schema
        );

        -- 未读投影按现存消息与活动 membership 精确重算，不能用 current_seq 差值，
        -- 因为历史删除会留下合法的 seq 空洞。
        IF to_regclass(format('%I.hasn_unread_projection', target_schema)) IS NOT NULL
           AND to_regclass(format('%I.hasn_conversation_memberships', target_schema)) IS NOT NULL THEN
            EXECUTE format(
                'UPDATE %1$I.hasn_unread_projection p '
                'SET unread_count = ('
                'SELECT count(*) '
                'FROM %1$I.hasn_conversation_memberships membership '
                'JOIN %1$I.hasn_messages m '
                'ON m.conversation_id = membership.conversation_id '
                'WHERE membership.conversation_id = p.conversation_id '
                'AND membership.member_hasn_id = p.member_hasn_id '
                'AND membership.left_seq IS NULL '
                'AND membership.state = ''active'' '
                'AND m.conversation_seq > membership.read_seq '
                'AND m.conversation_seq >= membership.joined_seq '
                'AND m.status <> 4 '
                'AND m.from_id <> membership.member_hasn_id'
                '), '
                'computed_at_seq = c.current_seq, '
                'updated_time = now() '
                'FROM %1$I.hasn_conversations c '
                'WHERE c.id = p.conversation_id '
                'AND EXISTS ('
                'SELECT 1 FROM %1$I.hasn_suppressed_messages s '
                'WHERE s.resolved_at IS NULL '
                'AND s.conversation_id = p.conversation_id '
                'AND s.command_payload <> ''{}''::jsonb'
                ')',
                target_schema
            );
        END IF;

        EXECUTE format(
            'SELECT count(*) '
            'FROM %I.hasn_suppressed_messages s '
            'WHERE s.resolved_at IS NULL '
            'AND (s.message_id IS NOT NULL '
            'OR s.sender_hasn_id IS NULL '
            'OR s.idempotency_scope IS NULL '
            'OR s.command_hash IS NULL '
            'OR s.command_payload = ''{}''::jsonb)',
            target_schema
        )
        INTO invalid_count;
        IF invalid_count <> 0 THEN
            RAISE EXCEPTION
                'R3 抑制命令迁移后仍有 % 条不可重放记录：schema=%',
                invalid_count,
                target_schema;
        END IF;

        EXECUTE format(
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_hasn_suppressed_idempotency_scope '
            'ON %I.hasn_suppressed_messages (idempotency_scope) '
            'WHERE idempotency_scope IS NOT NULL',
            target_schema
        );
        EXECUTE format(
            'COMMENT ON COLUMN %I.hasn_suppressed_messages.message_id '
            'IS ''放行后生成的权威消息 ID；待放行时为空''',
            target_schema
        );
        EXECUTE format(
            'COMMENT ON COLUMN %I.hasn_suppressed_messages.sender_hasn_id '
            'IS ''待放行命令的权威发送方 hasn_id''',
            target_schema
        );
        EXECUTE format(
            'COMMENT ON COLUMN %I.hasn_suppressed_messages.idempotency_scope '
            'IS ''发送方、来源节点与幂等键规范化后的 SHA-256''',
            target_schema
        );
        EXECUTE format(
            'COMMENT ON COLUMN %I.hasn_suppressed_messages.command_hash '
            'IS ''待放行命令规范化载荷 SHA-256，用于同键冲突检测''',
            target_schema
        );
        EXECUTE format(
            'COMMENT ON COLUMN %I.hasn_suppressed_messages.command_payload '
            'IS ''待放行的完整发送命令；放行时重新分配 conversation_seq''',
            target_schema
        );
    END LOOP;
END
$migration$;
