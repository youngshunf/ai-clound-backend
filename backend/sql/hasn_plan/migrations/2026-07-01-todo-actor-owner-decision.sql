-- PLAN-TRIAGE A2 · 到期分诊数据层（todo.actor 四态 + owner_decision + 留痕三列）
-- 事实源：docs/hasn-node设计文档/19-规划与目标管理/01-规划与目标管理总体设计.md §3.2；
--         docs/hasn-node设计文档/19-规划与目标管理/05-*.md §11.6/§6/§6.6。
-- 冻结不变量 #8「决策≠亲手做」：owner_decision 是可派发的「待你决策」态（分身备选项+提问卡），
-- 区别于 owner（线下亲为·不派发）。owner_decision=14 字符，varchar(8) 装不下，必须先扩列宽。
-- 幂等：ALTER COLUMN TYPE 对已 varchar(16) 的库无害；ADD COLUMN IF NOT EXISTS；COMMENT ON 幂等。

SET search_path TO hasn_plan, public;

-- 1) 扩列宽（owner_decision 14 字符 > varchar(8)）——先扩宽再改字典注释，避免写入截断。
ALTER TABLE todo ALTER COLUMN actor TYPE varchar(16);

COMMENT ON COLUMN todo.actor IS '归属分诊 (owner:需你亲为·线下:red/owner_decision:待你决策:violet/collab:待你确认:amber/agent:分身自主:cyan)';

-- 2) 留痕三列（独立列，不复用 notes——notes=用户备注、completion_note=完成结论、
--    cancel_reason=放弃原因、decision_note=owner_decision 决策留痕，四者语义不同）。
ALTER TABLE todo ADD COLUMN IF NOT EXISTS decision_note text NULL;
ALTER TABLE todo ADD COLUMN IF NOT EXISTS completion_note text NULL;
ALTER TABLE todo ADD COLUMN IF NOT EXISTS cancel_reason text NULL;

COMMENT ON COLUMN todo.decision_note   IS 'owner_decision 决策留痕（主人经提问卡拍板后的结论/理由）';
COMMENT ON COLUMN todo.completion_note IS '完成结论（done 时的成果小结，区别于 notes 用户备注）';
COMMENT ON COLUMN todo.cancel_reason   IS '放弃原因（cancelled 时的原因，区别于 notes 用户备注）';
