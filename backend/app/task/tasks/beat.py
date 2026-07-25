from datetime import timedelta

from celery.schedules import schedule

from backend.app.task.utils.tzcrontab import TzAwareCrontab

# 参考：https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html
LOCAL_BEAT_SCHEDULE = {
    '测试同步任务': {
        'task': 'task_demo',
        'schedule': schedule(30),
    },
    '测试异步任务': {
        'task': 'task_demo_async',
        'schedule': TzAwareCrontab('1'),
    },
    '测试传参任务': {
        'task': 'task_demo_params',
        'schedule': TzAwareCrontab('1'),
        'args': ['你好，'],
        'kwargs': {'world': '世界'},
    },
    '清理操作日志': {
        'task': 'backend.app.task.tasks.db_log.tasks.delete_db_opera_log',
        'schedule': TzAwareCrontab('0', '0', day_of_week='6'),
    },
    '清理登录日志': {
        'task': 'backend.app.task.tasks.db_log.tasks.delete_db_login_log',
        'schedule': TzAwareCrontab('0', '0', day_of_month='15'),
    },
    '订阅过期检查': {
        'task': 'expire_overdue_subscriptions',
        'schedule': TzAwareCrontab('30', '1'),  # 每天凌晨 1:30（年度发放后收敛存量 status）
    },
    '应用权益过期检查': {
        'task': 'app_entitlement_expire_sweep',
        'schedule': TzAwareCrontab('0', '2'),  # 每天凌晨 2 点收敛「active 但已过期」的应用权益 status
    },
    '商业化生命周期 sweep（MK-5 统一）': {
        'task': 'billing_lifecycle_sweep',
        # 每天凌晨 2:05 统一执行：到期前 7/3/1 天提醒 + 权益/订阅到期收敛 + 变更 WSPUSH billing kind。
        # 收编「订阅过期检查」「应用权益过期检查」两支旧任务的动作——旧任务保留双跑一期（幂等），
        # MK-9 摘除。排在两支旧任务（1:30/2:00）之后，作到期后的权威提醒/变更事件单入口。
        'schedule': TzAwareCrontab('5', '2'),
    },
    '群拉分身邀请过期检查': {
        'task': 'hasn_group_agent_invite_expire_sweep',
        # 每天凌晨 2:10 收敛超 7 天未处理的 pending 拉分身邀请（doc10 §3.2；读路径已惰性过期，本任务兜底）
        'schedule': TzAwareCrontab('10', '2'),
    },
    '设备绑定租约过期检查': {
        'task': 'hasn_node_binding_expire_sweep',
        # 每天凌晨 2:15 把 expires_at 已过仍标 active 的 Owner Binding 租约收敛为 expired
        # （热路径已按 expires_at 过滤兜住安全，本任务让设备管理页/审计反映真实租约状态）
        'schedule': TzAwareCrontab('15', '2'),
    },
    '关系生命周期过期检查': {
        'task': 'hasn_contact_lifecycle_expire_sweep',
        # 每天凌晨 2:20 收敛好友请求 30 天未响应过期 + 联系人 auto_expire 到期（doc08 RT5·B7）
        'schedule': TzAwareCrontab('20', '2'),
    },
    '履约 outbox 投递': {
        'task': 'credit_outbox_dispatch',
        # 每分钟投递一轮待履约命令。云端事务只写命令，真正调用 NewAPI 在这里；
        # 抢占用 FOR UPDATE SKIP LOCKED，多副本并发安全。
        'schedule': TzAwareCrontab('*'),
    },
    '履约对账': {
        'task': 'credit_outbox_reconcile',
        # 每 15 分钟核对死信事件在 NewAPI 侧的真实结果，收敛「其实成功了只是回执丢了」的事件。
        # 只补投影不覆盖余额。
        'schedule': TzAwareCrontab('*/15'),
    },
    '履约指标刷新': {
        'task': 'credit_outbox_metrics_refresh',
        # 每分钟刷新「已支付未履约」与 outbox 积压 Gauge，供告警判定。
        'schedule': TzAwareCrontab('*'),
    },
    'Agent 心跳超时检测': {
        'task': 'hasn_check_agent_heartbeat_timeout',
        'schedule': TzAwareCrontab('*/5'),  # 每 5 分钟执行一次
    },
    'Agent 产物登记意图对账': {
        'task': 'hasn_artifact_registration_reconcile',
        # 每 5 分钟重放 savepoint 失败后留下的真实登记意图，并补齐异常中断的 outbox。
        'schedule': TzAwareCrontab('*/5'),
    },
    '技能市场-ClawHub 定时同步': {
        'task': 'marketplace_sync_clawhub',
        # 每 3 天增量同步一次（真 72h 间隔）。增量：上游版本未变只刷计数、零下载零翻译；
        # 磁盘硬闸 MARKETPLACE_CLAWHUB_MAX_DISK_GB（默认 50GB）——clawhub 目录占用达上限即暂停下载。
        'schedule': schedule(timedelta(days=3)),
    },
    '技能市场-公共技能共享目录 reconcile': {
        'task': 'marketplace_shared_skills_reconcile',
        # 每 20 分钟兜底一次（doc11 §6 B3；common_skills revision bump 时另有即时 .delay() 触发）。
        # 未配置 HERMES_SHARED_SKILLS_ROOT（本机无 hermes sidecar）→ 任务内 no-op；
        # 内容寻址增量：无变更整轮零下载，兜底成本≈两条快照查询。
        'schedule': TzAwareCrontab('*/20'),
    },
    '获客-触达发送 worker': {
        'task': 'growth_dispatch_approved_outreach',
        'schedule': TzAwareCrontab('*/5'),  # 每 5 分钟扫 approved 触达分发（quiet hours 窗口内才实发）
    },
    'Owner 记忆 pending 合并兜底重试': {
        'task': 'owner_memory_retry_pending_merges',
        # 每 10 分钟扫一次滞留 pending（同步内联合并失败的兜底重试）。只重试最老 pending 已超
        # 120s 的 owner，避开刚 contribute 的内联路径；网关恢复后下一轮即合并下发，杜绝采访完
        # coverage 永不更新。
        'schedule': TzAwareCrontab('*/10'),
    },
    'Peer 画像合成 worker': {
        'task': 'peer_portrait_sweep',
        # 每 10 分钟扫一次「有新 peer 事实但画像未追上」的 (owner, peer) 对（doc17 PEERSYN-P4）。
        # peer 事实上游来源为分身主动 hasn.memory.save（doc18 退役自动提取后单一来源）。方案B 脏判定
        # MAX(peer 事实.updated_at) > 画像.last_synthesized_at；逐对独立事务，跨全部分身聚合合成一份，
        # 合成后发 memory.peer_portrait.upserted 下行 daemon。
        'schedule': TzAwareCrontab('5-59/10'),
    },
}
