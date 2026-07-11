-- NOTIF-N1 存量「自分身→主人（汇报面）」通知中心行归档
-- 事实源：docs/hasn-node设计文档/通知系统统一设计/01-通知中心重构（分面归属·折叠进消息列表·卡片化）设计.md R1/R2
--
-- 背景：v0.4 把「分身向主人汇报/请示」（designsystem.ready / task.pending_approval 等）当通知落进
-- hasn_notifications 权威行，污染了通知中心。N1 在 emit() 加了 OwnerLoopback 守卫拦截这类事件（改投
-- 主会话汇报卡）。此迁移把**历史已落库**的同类行一并归档（read=TRUE + state=archived），使通知中心即刻
-- 只剩「外部用户/agent → 主人」的真通知。
--
-- 判据 = 新守卫同款：source.kind==agent 且该分身的主人==target（收件人）。
--   · 快路：source.on_behalf_of == target_id
--   · 兜底：join hasn_agents，source.id 对应分身的 owner_id == target_id
--
-- 幂等：state<>'archived' 前置守卫，重复执行无副作用；纯 UPDATE 不删行（保留可回溯）。
-- 注意：不做 category 破坏性重映射——通知面 category 收窄由前端 tab 归并（N4）+ 生产端重分类（N2）实现，
--       历史行 category 原样保留，避免不可逆数据丢失。

UPDATE hasn_notifications n
SET state = 'archived',
    read = TRUE
WHERE n.state <> 'archived'
  AND n.source ->> 'kind' = 'agent'
  AND (
    n.source ->> 'on_behalf_of' = n.target_id
    OR EXISTS (
      SELECT 1
      FROM hasn_agents a
      WHERE a.hasn_id = n.source ->> 'id'
        AND a.owner_id = n.target_id
    )
  );
