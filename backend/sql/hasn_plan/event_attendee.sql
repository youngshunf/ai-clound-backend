-- PLAN-ENT A1 · 企业事件参会人表（event_attendee）
-- 事实源：docs/hasn-node设计文档/19-规划与目标管理/04-规划应用双模化（个人+企业日历）设计.md §3.2/§6.2
-- 冻结不变量 #4「参会人仅企业」：event_attendee.event_id 指向的 event 必 enterprise_id IS NOT NULL；
-- 冗余 enterprise_id NOT NULL 便于按企业维度直查（恒前置 enterprise_id，不变量 #2）。
-- 幂等：CREATE TABLE / INDEX IF NOT EXISTS。

SET search_path TO hasn_plan, public;

CREATE TABLE IF NOT EXISTS event_attendee (
    id bigserial PRIMARY KEY,
    event_id bigint NOT NULL REFERENCES event (id) ON DELETE CASCADE,
    enterprise_id bigint NOT NULL,
    attendee_hasn_id varchar(40) NOT NULL DEFAULT '',
    role varchar(16) NOT NULL DEFAULT 'required',
    rsvp varchar(16) NOT NULL DEFAULT 'none',
    responded_at timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz,
    CONSTRAINT uq_event_attendee UNIQUE (event_id, attendee_hasn_id)
);

-- 幂等补齐 UNIQUE：若表已由 ORM metadata.create_all 抢先建出（无此约束），CREATE TABLE IF NOT EXISTS 会跳过，
-- 故这里独立补一次（PG 不支持 ADD CONSTRAINT IF NOT EXISTS，用 DO 块守卫）。
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_event_attendee' AND conrelid = 'hasn_plan.event_attendee'::regclass
    ) THEN
        ALTER TABLE event_attendee ADD CONSTRAINT uq_event_attendee UNIQUE (event_id, attendee_hasn_id);
    END IF;
END $$;

-- 按参会人 + 企业查「我被邀的会」（恒前置 enterprise_id；索引第二列标识符无尾随空格）。
CREATE INDEX IF NOT EXISTS idx_event_attendee_who ON event_attendee (attendee_hasn_id, enterprise_id);

COMMENT ON TABLE  event_attendee                  IS '企业事件参会人（RSVP）；event 必 enterprise_id IS NOT NULL（不变量 #4）';
COMMENT ON COLUMN event_attendee.event_id         IS '所属企业事件（外键 event.id，ON DELETE CASCADE）';
COMMENT ON COLUMN event_attendee.enterprise_id    IS '冗余企业 id（恒前置查询用；逻辑引用 public.hasn_enterprise.id）';
COMMENT ON COLUMN event_attendee.attendee_hasn_id IS '参会人 HASN id（human owner）';
COMMENT ON COLUMN event_attendee.role             IS '角色 (organizer:组织者:violet/required:必到:blue/optional:可选:gray)';
COMMENT ON COLUMN event_attendee.rsvp             IS '回执 (none:未回复:gray/accepted:接受:green/declined:拒绝:red/tentative:待定:amber)';
COMMENT ON COLUMN event_attendee.responded_at     IS 'RSVP 回复时间（NULL=尚未回复）';
