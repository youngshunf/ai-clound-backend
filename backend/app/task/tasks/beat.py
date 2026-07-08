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
    '年度订阅积分发放': {
        'task': 'grant_yearly_subscription_credits',
        'schedule': TzAwareCrontab('0', '1'),  # 每天凌晨 1 点执行
    },
    '订阅过期检查': {
        'task': 'expire_overdue_subscriptions',
        'schedule': TzAwareCrontab('30', '1'),  # 每天凌晨 1:30（年度发放后收敛存量 status）
    },
    '应用权益过期检查': {
        'task': 'app_entitlement_expire_sweep',
        'schedule': TzAwareCrontab('0', '2'),  # 每天凌晨 2 点收敛「active 但已过期」的应用权益 status
    },
    '群拉分身邀请过期检查': {
        'task': 'hasn_group_agent_invite_expire_sweep',
        # 每天凌晨 2:10 收敛超 7 天未处理的 pending 拉分身邀请（doc10 §3.2；读路径已惰性过期，本任务兜底）
        'schedule': TzAwareCrontab('10', '2'),
    },
    '关系生命周期过期检查': {
        'task': 'hasn_contact_lifecycle_expire_sweep',
        # 每天凌晨 2:20 收敛好友请求 30 天未响应过期 + 联系人 auto_expire 到期（doc08 RT5·B7）
        'schedule': TzAwareCrontab('20', '2'),
    },
    '积分账本每小时对账': {
        'task': 'newapi_hourly_credit_sync',
        'schedule': TzAwareCrontab('0'),  # 每小时整点：new-api 真实消费增量回扣账本 + 重设 quota（§5A.5）
    },
    'Agent 心跳超时检测': {
        'task': 'hasn_check_agent_heartbeat_timeout',
        'schedule': TzAwareCrontab('*/5'),  # 每 5 分钟执行一次
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
    '云端记忆提取 worker': {
        'task': 'memory_extraction_sweep',
        # 每 10 分钟扫一次有未提取消息的 owner（doc16 Phase C2 单一云端提取管线）。增量水位
        # memory_extraction_cursor 按 message id 单调推进，幂等；只取 owner 输入 + 任务结果/摘要，
        # 跳过 agent verbose；平台廉价模型、平台吸收成本；candidate→PolicyGate→confidence→semantic_fact。
        'schedule': TzAwareCrontab('*/10'),
    },
    'Peer 画像合成 worker': {
        'task': 'peer_portrait_sweep',
        # 每 10 分钟扫一次「有新 peer 事实但画像未追上」的 (owner, peer) 对（doc17 PEERSYN-P4）。
        # 错开提取 worker 5 分钟（提取先写 peer 事实、本 sweep 再据事实合成画像）。方案B 脏判定
        # MAX(peer 事实.updated_at) > 画像.last_synthesized_at；逐对独立事务，跨全部分身聚合合成一份，
        # 合成后发 memory.peer_portrait.upserted 下行 daemon。
        'schedule': TzAwareCrontab('5-59/10'),
    },
}
