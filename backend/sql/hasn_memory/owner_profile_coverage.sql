-- 主人画像完整度（云端权威）——「了解主人」功能的结构化判定层
-- owner × dimension 一行：分身对主人 5 个画像维度各自的覆盖状态 + LLM 打分。
-- 驱动：首页「让分身了解你」入口显隐（全 sufficient 隐藏）+ 采访"缺什么采访什么" + 主动规划闭环开关。
-- 落 schema hasn_memory（ADR-15 应用独立 schema；与 owner_memory 同模块，画像完整度是记忆的结构化投影）。
-- 设计事实源：docs/hasn-node设计文档/19-规划与目标管理/03-了解主人：采访建档·完整度判定·主动规划闭环设计.md §4.1
-- PostgreSQL 语法；时间字段 created_time/updated_time timestamptz；字典字段 COMMENT ON (value:label:color/...) 格式。

SET search_path TO hasn_memory, public;

CREATE TABLE IF NOT EXISTS owner_profile_coverage (
    id bigserial PRIMARY KEY,
    owner_id varchar(40) NOT NULL,
    dimension varchar(20) NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'missing',
    confidence numeric(4, 3) NOT NULL DEFAULT 0,
    summary text,
    missing_hint text,
    evidence_version integer NOT NULL DEFAULT 0,
    assessed_time timestamptz(6),
    created_time timestamptz(6) NOT NULL DEFAULT now(),
    updated_time timestamptz(6)
);

-- 同一主人同一维度至多一行（判定按 owner+dimension upsert，再判=更新同一行）
CREATE UNIQUE INDEX IF NOT EXISTS uq_owner_profile_coverage_owner_dim
    ON owner_profile_coverage (owner_id, dimension);

COMMENT ON TABLE owner_profile_coverage IS '主人画像完整度（云端权威，owner×维度）';
COMMENT ON COLUMN owner_profile_coverage.id IS '主键 ID';
COMMENT ON COLUMN owner_profile_coverage.owner_id IS '主人 hasn_id（hasn_humans.hasn_id）';
COMMENT ON COLUMN owner_profile_coverage.dimension IS '画像维度 (interests:兴趣爱好:blue/work:工作情况:cyan/residence:居住地址:green/goals:近期目标:orange/life_plan:人生规划:purple)';
COMMENT ON COLUMN owner_profile_coverage.status IS '覆盖状态 (missing:缺失:gray/partial:部分:orange/sufficient:充分:green)';
COMMENT ON COLUMN owner_profile_coverage.confidence IS 'LLM 打分置信度 0–1';
COMMENT ON COLUMN owner_profile_coverage.summary IS '该维度已知信息的一句话摘要（面向主人；驱动入口卡"已了解"）';
COMMENT ON COLUMN owner_profile_coverage.missing_hint IS '该维度还缺什么（驱动采访提问 + 入口卡"还需了解"）';
COMMENT ON COLUMN owner_profile_coverage.evidence_version IS '判定所依据的 owner_memory.version（防陈旧重判）';
COMMENT ON COLUMN owner_profile_coverage.assessed_time IS '最近一次判定时间';
COMMENT ON COLUMN owner_profile_coverage.created_time IS '创建时间';
COMMENT ON COLUMN owner_profile_coverage.updated_time IS '更新时间';
