-- doc08 RT1 / D3：Owner-Agent 控制边 service+5 → social+5 迁移
--
-- 背景：协议 Core/02 §7.4.2 规定「主人 ↔ 自己分身」的控制边 MUST 编码为
--   relation_type=social + trust_level=5，MUST NOT 用 service。历史注册路径
--   （hasn_auth.register_hasn_agent / hasn_agents_service.create）误写 service+5，
--   违反协议与 validate_relation_constraints。本迁移把存量控制边就地迁为 social+5。
--
-- 安全性：自有分身的判定全链靠 peer_owner_id == owner_id（+ trust_level=5），
--   不依赖 relation_type='service'（已 grep 确认无「靠 service 排除自有分身」的读取点）。
--   commerce/service/professional/platform 四类关系维持协议预留（D5），service 语义
--   还给订单履约，不再被控制边占用。
--
-- 幂等：可重复执行。若新注册路径（已改 social+5）已为某分身建了 social+5 孪生行，
--   先删旧 service+5 行避免 UPDATE 撞唯一键 uq_hasn_contact_relation，再迁其余。

BEGIN;

-- 步骤 1：删除「已存在 social 孪生行」的旧 service+5 自有分身控制边
--   （新注册路径 social+5 与旧 service+5 relation_type 不同、不触发 ON CONFLICT，
--    会并存两行；此处清掉冗余的 service 行，让 UPDATE 不撞唯一键）。
DELETE FROM hasn_contacts s
WHERE s.relation_type = 'service'
  AND s.trust_level = 5
  AND s.peer_owner_id = s.owner_id
  AND EXISTS (
    SELECT 1 FROM hasn_contacts t
    WHERE t.owner_id = s.owner_id
      AND t.peer_id = s.peer_id
      AND t.relation_type = 'social'
  );

-- 步骤 2：其余 service+5 自有分身控制边就地迁为 social+5。
UPDATE hasn_contacts
SET relation_type = 'social',
    updated_time = now()
WHERE relation_type = 'service'
  AND trust_level = 5
  AND peer_owner_id = owner_id;

COMMIT;
