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
    '获客-触达发送 worker': {
        'task': 'growth_dispatch_approved_outreach',
        'schedule': TzAwareCrontab('*/5'),  # 每 5 分钟扫 approved 触达分发（quiet hours 窗口内才实发）
    },
}
