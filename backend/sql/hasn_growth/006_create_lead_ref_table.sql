-- 006: 用户↔线索引用表（统一线索池重构·2026-06-27 福仔「统一的线索池，用户只是引用线索池中的线索」）
-- 架构：contact 表退化为**纯公共线索池**（每条线索全局存一份，pool_visibility 区分公共/私有），
-- 用户对线索的「拥有/状态」全部下沉到本引用表——用户列表 = lead_ref JOIN contact WHERE user_id=X。
-- 一个用户对同一条池线索至多一条引用（UNIQUE user_id+lead_contact_id）；
-- 用户级状态（new/qualified/dismissed）落本表（非池行），晋级/忽略互不影响他人对同条线索的视图。
-- 来源 source 记这条引用怎么来的：request 请求匹配 / manual 手动登记 / collect 分身采集 / backfill 缺口补爬。
-- PostgreSQL 语法；落 schema hasn_growth。纯内部引用表（对齐 lead_quota）：仅手写 model，无独立 CRUD API。

SET search_path TO hasn_growth, public;

CREATE TABLE IF NOT EXISTS lead_ref (
    id bigserial PRIMARY KEY,
    user_id bigint NOT NULL,
    lead_contact_id bigint NOT NULL,
    source varchar(16) NOT NULL DEFAULT 'request',
    status varchar(16) NOT NULL DEFAULT 'new',
    dismiss_reason varchar(255),
    note text,
    acquired_at timestamptz NOT NULL DEFAULT now(),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_lead_ref_user_lead UNIQUE (user_id, lead_contact_id)
);
CREATE INDEX IF NOT EXISTS ix_growth_lead_ref_user ON lead_ref (user_id);
CREATE INDEX IF NOT EXISTS ix_growth_lead_ref_contact ON lead_ref (lead_contact_id);

COMMENT ON TABLE lead_ref IS '用户↔线索引用表（统一线索池：用户引用池中线索·用户级状态落本表不污染池行）';
COMMENT ON COLUMN lead_ref.user_id IS '引用线索的用户 ID（owner）';
COMMENT ON COLUMN lead_ref.lead_contact_id IS '被引用的线索池行 ID（hasn_growth.contact.id）';
COMMENT ON COLUMN lead_ref.source IS '来源 (request:请求匹配:blue/manual:手动登记:cyan/collect:分身采集:green/backfill:缺口补爬:orange)';
COMMENT ON COLUMN lead_ref.status IS '状态 (new:新线索:blue/qualified:已晋级:green/dismissed:已忽略:gray)';
COMMENT ON COLUMN lead_ref.dismiss_reason IS '忽略原因（status=dismissed 时记录）';
COMMENT ON COLUMN lead_ref.note IS '用户对该线索的备注';
COMMENT ON COLUMN lead_ref.acquired_at IS '获得该线索引用的时间';
