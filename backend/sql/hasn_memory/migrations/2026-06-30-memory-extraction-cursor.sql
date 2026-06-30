-- 迁移：记忆提取游标表（doc16 Phase C2：单一云端提取 worker 的每-owner 增量水位）。
-- 配套 `docs/hasn-node设计文档/02-记忆与知识库/16-记忆系统云端权威重构（消息上云+工作会话同步+单一提取）.md`。
--
-- 单一云端提取 worker 需要一个「每 owner 的增量水位」记录「上次提取处理到哪条消息 / 哪个会话摘要」：
--   - 增量：每轮只取 hasn_messages.id > last_message_id 的新消息（owner 输入 + 任务结果/摘要）；
--   - 幂等：重复触发不重复提取同一窗口（按 message id 单调推进）。
-- 模型 backend/app/hasn_memory/model/memory_extraction_cursor.py（MappedBase / hasn_memory schema / epoch ms）。
--
-- 幂等：CREATE SCHEMA / TABLE IF NOT EXISTS，可重复执行；全新库与已迁移库均安全。
-- 执行：psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 本文件

CREATE SCHEMA IF NOT EXISTS hasn_memory;

CREATE TABLE IF NOT EXISTS hasn_memory.memory_extraction_cursor (
    owner_id                   varchar(40) PRIMARY KEY,
    last_message_id            bigint NOT NULL DEFAULT 0,
    last_session_checkpoint_at bigint NOT NULL DEFAULT 0,
    facts_written              bigint NOT NULL DEFAULT 0,
    last_run_at                bigint NOT NULL DEFAULT 0,
    created_at                 bigint NOT NULL,
    updated_at                 bigint NOT NULL
);

COMMENT ON TABLE  hasn_memory.memory_extraction_cursor IS 'HASN 记忆系统 - 提取游标（每 owner 增量水位）';
COMMENT ON COLUMN hasn_memory.memory_extraction_cursor.owner_id IS 'Owner ID';
COMMENT ON COLUMN hasn_memory.memory_extraction_cursor.last_message_id IS '上次提取处理到的 hasn_messages.id';
COMMENT ON COLUMN hasn_memory.memory_extraction_cursor.last_session_checkpoint_at IS '上次处理的会话摘要水位 (epoch ms)';
COMMENT ON COLUMN hasn_memory.memory_extraction_cursor.facts_written IS '累计写入事实数';
COMMENT ON COLUMN hasn_memory.memory_extraction_cursor.last_run_at IS '上次提取运行时间 (epoch ms)';
COMMENT ON COLUMN hasn_memory.memory_extraction_cursor.created_at IS '创建时间 (epoch ms)';
COMMENT ON COLUMN hasn_memory.memory_extraction_cursor.updated_at IS '更新时间 (epoch ms)';
