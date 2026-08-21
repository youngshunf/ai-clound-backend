from datetime import timedelta

from celery.schedules import schedule

from backend.app.task.utils.tzcrontab import TzAwareCrontab

# 参考：https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html
LOCAL_BEAT_SCHEDULE = {
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
    '存量免费合同补履约': {
        'task': 'free_contract_fulfillment_backfill',
        # 每天凌晨 2:25。doc94 之前建的免费合同从未投递给 NewAPI（external_subscription_id 为空），
        # 那批用户合同上写着「每 30 天 100 积分」而权威侧一个订阅池都没有。
        # 排在「履约 outbox 投递」之前，补的命令当轮即可投出；补齐后本任务自然空转。
        'schedule': TzAwareCrontab('25', '2'),
    },
    '履约 outbox 投递': {
        'task': 'credit_outbox_dispatch',
        # 每分钟投递一轮待履约命令。云端事务只写命令，真正调用 NewAPI 在这里；
        # 抢占用 FOR UPDATE SKIP LOCKED，多副本并发安全。
        'schedule': TzAwareCrontab('*'),
    },
    'Agent 控制边关系 outbox 投递': {
        'task': 'hasn_relation_outbox_dispatch',
        # 提交后即时唤醒失败时，每分钟扫描一次持久命令，保证控制边最终落入 IM 关系域。
        'schedule': TzAwareCrontab('*'),
    },
    'HASN 离线恢复影子对账': {
        'task': 'hasn_offline_shadow_reconcile',
        # 仅 dual 模式执行真实 Redis/PG 集合对账；其余模式任务显式 no-op。
        'schedule': TzAwareCrontab('*/5'),
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
    '用户云存储作业投递': {
        'task': 'owner_storage_job_dispatch',
        # 每分钟推进导出、迁移、物理回收与补偿 outbox；作业内部使用 SKIP LOCKED 支持多副本。
        'schedule': TzAwareCrontab('*'),
    },
    '用户云存储上传生命周期清理': {
        'task': 'owner_storage_upload_lifecycle_sweep',
        # 每 5 分钟终止过期 multipart 并核实过期预占，避免供应商分片和额度长期悬挂。
        'schedule': TzAwareCrontab('*/5'),
    },
    '用户云存储旧资产回填': {
        'task': 'owner_storage_legacy_backfill',
        # 兼容窗口内持续补齐 object_id；读取路径保留双读，任务失败不会中断旧资产访问。
        'schedule': TzAwareCrontab('*/5'),
    },
    '用户云存储保留期清理': {
        'task': 'owner_storage_retention_sweep',
        # 每天 3:10 清理过期导出、迁移源对象和策略允许的长期无引用资产。
        'schedule': TzAwareCrontab('10', '3'),
    },
    '用户云存储周期对账': {
        'task': 'owner_storage_reconcile',
        # 每天 3:30 以数据库对象行逐 Owner 核实真实对象与计数器，不依赖桶前缀遍历。
        'schedule': TzAwareCrontab('30', '3'),
    },
    '云端节点生命周期 sweep': {
        'task': 'cloud_node_retention_sweep',
        # 每天 04:20（低峰，且排在 2:05 商业化 sweep 之后——订阅 status 先收敛成 expired，
        # 本任务再据此判「订阅到期」）：进 30 天保留期 → 续订恢复 → 逾期销毁 → 到期前 7/3/1 天提醒。
        # 托管设计 §7 生命周期表 / D-14。
        'schedule': TzAwareCrontab('20', '4'),
    },
    '模型注册表-每日从网关同步': {
        'task': 'hasn_model_registry_sync',
        # 每天凌晨 4:40 从 new-api 定价表同步一轮模型清单（upsert，绝不删行）。
        # 排在云端节点 sweep（4:20）之后；运营也可在 Admin 页点「立即同步」。
        'schedule': TzAwareCrontab('40', '4'),
    },
    '技能市场-ClawHub 定时同步': {
        'task': 'marketplace_sync_clawhub',
        # 每 3 天增量同步一次（真 72h 间隔）。只读取元数据与文件清单，技能 ZIP 由 ClawHub 分发。
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
    '获客-采集 pending 作业恢复投递': {
        'task': 'lead_automation_reconcile_pending',
        # early ACK 后若 worker 在事务提交前退出，作业仍为 pending；每 5 分钟按数据库权威状态重投。
        'schedule': TzAwareCrontab('*/5'),
    },
    '获客-项目开通恢复对账': {
        'task': 'growth_project_provision_reconcile',
        # 到期失败与 worker 崩溃留下的 running 每 5 分钟重投；步骤写点自身保持幂等。
        'schedule': TzAwareCrontab('*/5'),
    },
    # doc19 §10 退役（2026-07-31）：'owner_memory_retry_pending_merges' 与 'peer_portrait_sweep'
    # 两条记忆语义处理调度已随云端 LLM 合并/画像合成整体删除。合并改由**主脑分身在自己的设备上**
    # 执行（§5.1 判断力应在分身身上、可向主人汇报、成本归位），整轮结果经云端合并闸
    # `POST /api/v1/hasn/memory/agent/merge/apply` 提交；触发源改为 daemon `task_scheduler` 的
    # 内置定时任务 `memory_review`（§9）。云端不再有任何记忆语义处理（§8.5）。
}
