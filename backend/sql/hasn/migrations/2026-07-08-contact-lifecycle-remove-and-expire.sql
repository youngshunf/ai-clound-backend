-- doc08 RT5 / D4 + B7：关系生命周期补全（删除联系人 + 请求/关系过期）
--
-- 本切片为纯代码逻辑，无新增列 / 新表；唯一 DDL 是给 hasn_conversations.status
-- 补一个新枚举值 `unreachable`（列类型仍是 varchar(20)，无 CHECK 约束，故无需改类型，
-- 仅更新列 COMMENT 让字典/前端渲染口径与 model 一致）。
--
-- 语义（D4·删除联系人「会话不删但标不可达」）：
--   删除联系人后关系边双向删除，两人的 direct 会话历史**不删**，仅把 status 标 unreachable
--   ——「关系已解除、需重新加好友才能继续通信」。对端后续发消息按无关系门控/暂存。
--
-- 好友请求过期（B7）：hasn_contact_requests.status 的 `expired` 枚举早已在列 COMMENT 中定义，
--   本次只是补上写入路径（celery beat 每日 sweep），无需改 DDL。
--   联系人 auto_expire 到期同理走既有 `archived` 状态，无需改 DDL。
--
-- 幂等：可重复执行（COMMENT ON 覆盖写）。

BEGIN;

COMMENT ON COLUMN hasn_conversations.status IS
    '状态 (active:活跃:green/archived:已归档:gray/disbanded:已解散:red/unreachable:不可达:orange)';

COMMIT;
