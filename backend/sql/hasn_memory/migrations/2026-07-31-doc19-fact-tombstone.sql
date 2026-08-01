-- =====================================================
-- doc19 S3 · fact_tombstone（purge 删除凭证兼墓碑）
-- =====================================================
-- 设计事实源：docs/hasn-node设计文档/02-记忆与知识库/19-多节点记忆分层与分身自治整理设计.md §4.5
--
-- 主人发起硬删（purge）后，各处只留一条删除凭证：fact_id + 删除时间 + 发起人，
-- **绝不存被删事实的任何内容**（无 predicate / object / rationale 快照）——否则「不留任何
-- 内容」是空话。该凭证同时兼作 tombstone：origin 节点若在 purge 时离线、outbox 里还积压着
-- 该 fact 的事件，重新上线推送时云端按本表**永久拒绝**（一次性 warn）并回令来源节点清理。
--
-- cascade_from 记录级联来源：派生事实因其 merged_from 含被删 fact 而被一并物理删除时，
-- 指向触发级联的源 fact_id（沿血缘链递归），主键仍是被删事实自身的 fact_id。
--
-- 幂等：CREATE TABLE / CREATE INDEX IF NOT EXISTS，可重复执行。
-- =====================================================

SET search_path TO hasn_memory, public;

CREATE TABLE IF NOT EXISTS fact_tombstone (
    fact_id varchar(40) NOT NULL,
    owner_id varchar(40) NOT NULL,
    purged_time timestamptz(6) NOT NULL DEFAULT now(),
    purged_by varchar(40) NOT NULL,
    cascade_from varchar(40),
    reason text,
    created_time timestamptz(6) NOT NULL DEFAULT now(),
    updated_time timestamptz(6),
    CONSTRAINT pk_fact_tombstone PRIMARY KEY (fact_id)
);

CREATE INDEX IF NOT EXISTS idx_fact_tombstone_owner_time
    ON fact_tombstone (owner_id, purged_time DESC);

COMMENT ON TABLE fact_tombstone IS 'HASN 记忆系统 - 事实删除凭证（墓碑，绝不存被删内容）';
COMMENT ON COLUMN fact_tombstone.fact_id IS '被物理删除的事实 ID（主键）';
COMMENT ON COLUMN fact_tombstone.owner_id IS '主人 hasn_id（hasn_humans.hasn_id）';
COMMENT ON COLUMN fact_tombstone.purged_time IS '物理删除执行时间';
COMMENT ON COLUMN fact_tombstone.purged_by IS '发起人 hasn_id（purge 只有主人可发起）';
COMMENT ON COLUMN fact_tombstone.cascade_from IS '级联来源 fact_id（因血缘级联被删时指向源事实；直接删除为空）';
COMMENT ON COLUMN fact_tombstone.reason IS '删除原因（面向主人的说明，不含被删事实内容）';
COMMENT ON COLUMN fact_tombstone.created_time IS '创建时间';
COMMENT ON COLUMN fact_tombstone.updated_time IS '更新时间';
