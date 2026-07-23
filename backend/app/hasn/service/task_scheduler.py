"""HASN Task Scheduler（legacy 云端后台调度器）

每分钟 tick 一次，查询到期任务，通过 HASN 协议发送 TaskExec 消息到 Agent 所在的节点。

v2.1 主链路不再默认启动该中心 tick。任务调度由本地/云端 Runtime Host
基于 task sync mirror 自行执行；此服务只保留旧协议兼容和手动调试能力。

关键设计：
- at-most-once：预先推进 next_run_at，防止重复执行
- 预期最长执行时间 600s
- 链式任务：context_from_task_id 注入上次执行结果
"""

import asyncio
import logging

from datetime import datetime, timedelta
from datetime import timezone as tz
from typing import Any

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_task import HasnTask
from backend.app.hasn.model.hasn_task_run import HasnTaskRun
from backend.app.hasn_im.application.ws_node_runtime import ws_node_runtime
from backend.app.hasn_task.model.skill_bundle import HasnSkillBundle
from backend.common.exception import errors
from backend.database.db import async_db_session

logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 60
TASK_EXEC_TIMEOUT_SECONDS = 600


class TaskSchedulerService:
    """云端任务调度器"""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动调度器"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info('[TaskScheduler] started')

    async def stop(self) -> None:
        """停止调度器"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info('[TaskScheduler] stopped')

    async def _run_loop(self) -> None:
        """主循环：每分钟 tick 一次"""
        while self._running:
            try:
                count = await self.tick()
                if count > 0:
                    logger.info(f'[TaskScheduler] tick dispatched {count} tasks')
            except Exception:
                logger.exception('[TaskScheduler] tick error')
            await asyncio.sleep(TICK_INTERVAL_SECONDS)

    async def tick(self) -> int:
        """
        调度器 tick：
        1. 查找 enabled=true AND next_run_at <= NOW() 的任务
        2. 预先推进 next_run_at（at-most-once）
        3. 创建 hasn_task_run 记录（status=pending）
        4. 发送 TaskExec 消息到 Agent 所在节点
        """
        now = datetime.now(tz.utc)
        dispatched = 0

        async with async_db_session() as session:
            # 1. 查找到期任务
            stmt = select(HasnTask).where(HasnTask.enabled.is_(True)).where(HasnTask.next_run_at <= now).limit(100)
            result = await session.execute(stmt)
            tasks = result.scalars().all()

            for task in tasks:
                if await self._dispatch_task_safely(session, task, now):
                    dispatched += 1

            await session.commit()

        return dispatched

    async def _dispatch_task_safely(
        self,
        session: AsyncSession,
        task: HasnTask,
        now: datetime,
    ) -> bool:
        try:
            await self._dispatch_task(session, task, now)
        except Exception:
            logger.exception(f'[TaskScheduler] dispatch task {task.id} failed')
            return False
        return True

    async def _dispatch_task(
        self,
        session: AsyncSession,
        task: HasnTask,
        now: datetime,
    ) -> None:
        """处理单个到期任务"""
        # 预先推进 next_run_at（at-most-once）
        next_run = self._calc_next_run(task, now)
        task.next_run_at = next_run
        task.last_run_at = now
        task.run_count = (task.run_count or 0) + 1
        task.repeat_completed = (task.repeat_completed or 0) + 1

        # 检查是否需要标记 completed
        if task.schedule_type == 'once' or (
            task.repeat_times is not None and task.repeat_completed >= task.repeat_times
        ):
            task.enabled = False
            task.state = 'completed'

        # 2. 构建 prompt（含链式上下文）
        prompt = task.prompt
        context: dict[str, Any] = {}
        if task.context_from_task_id:
            ctx = await self._load_context_from(session, task.context_from_task_id)
            if ctx:
                context['previous_output'] = ctx
        skill_bundle_definitions = await self._load_skill_bundle_definitions(
            session,
            task.owner_id,
            task.skill_bundle_ids or [],
        )

        # 3. 创建 hasn_task_run（status=pending）
        task_run = HasnTaskRun(
            task_id=task.id,
            agent_id=task.agent_id,
            source_conversation_id=None,
            source_message_id=None,
            status='pending',
            prompt_snapshot=prompt,
        )
        session.add(task_run)
        await session.flush()  # 获取 task_run.id
        task_run.session_id = f'sess_task_{task_run.id}'

        # 4. 发送 TaskExec 消息到 Agent
        task_exec_params = {
            'type': 'task_exec',
            'task_id': task.id,
            'run_id': task_run.id,
            'session_id': task_run.session_id,
            'source_conversation_id': task_run.source_conversation_id,
            'source_message_id': task_run.source_message_id,
            'agent_id': task.agent_id,
            'prompt': prompt,
            'skill_bundles': task.skill_bundle_ids or [],
            'skill_bundle_definitions': skill_bundle_definitions,
            'skills': task.skill_ids or [],
            'enabled_toolsets': task.enabled_toolsets,
            'context': context,
        }
        task_exec_msg = {
            'hasn': 'hasn/0.2',
            'method': 'hasn.task.exec',
            'params': task_exec_params,
        }

        pushed = await ws_node_runtime.push_message_to(task.agent_id, task_exec_msg)
        if not pushed:
            logger.warning(f'[TaskScheduler] task {task.id} agent {task.agent_id} offline, message queued')

    def _calc_next_run(self, task: HasnTask, now: datetime) -> datetime | None:
        """根据 schedule_type 计算下一次执行时间"""
        config = task.schedule_config or {}

        if task.schedule_type == 'once':
            return None

        if task.schedule_type == 'interval':
            minutes = config.get('minutes', 60)
            return now + timedelta(minutes=minutes)

        if task.schedule_type == 'cron':
            expr = config.get('expr', '0 * * * *')
            try:
                cron = croniter(expr, now)
                return cron.get_next(datetime)
            except Exception:
                logger.error(f'[TaskScheduler] invalid cron expr: {expr}')
                return now + timedelta(hours=1)

        return None

    async def _load_context_from(self, session: AsyncSession, task_id: int) -> str | None:
        """加载链式任务的上下文（上一次执行结果）"""
        stmt = (
            select(HasnTaskRun)
            .where(HasnTaskRun.task_id == task_id)
            .where(HasnTaskRun.status == 'success')
            .order_by(HasnTaskRun.created_time.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()
        return run.output if run else None

    async def _load_skill_bundle_definitions(
        self,
        session: AsyncSession,
        owner_id: str,
        bundle_names: list[str],
    ) -> list[dict[str, Any]]:
        """加载任务引用的 Skill Bundle 定义，供节点构建实际执行 prompt。"""
        if not bundle_names:
            return []
        stmt = (
            select(HasnSkillBundle)
            .where(HasnSkillBundle.owner_id == owner_id)
            .where(HasnSkillBundle.name.in_(bundle_names))
        )
        result = await session.execute(stmt)
        by_name = {bundle.name: bundle for bundle in result.scalars().all()}
        definitions: list[dict[str, Any]] = []
        for name in bundle_names:
            bundle = by_name.get(name)
            if not bundle:
                continue
            definitions.append(
                {
                    'name': bundle.name,
                    'display_name': bundle.display_name,
                    'description': bundle.description,
                    'skill_ids': bundle.skill_ids or [],
                    'instruction': bundle.instruction,
                }
            )
        return definitions

    async def handle_task_result(
        self,
        run_id: int,
        status: str,
        reporting_agent_id: str,
        prompt_snapshot: str | None = None,
        output: str | None = None,
        error: str | None = None,
        model: str | None = None,
        token_usage: dict[str, int] | None = None,
        duration_ms: int | None = None,
    ) -> bool:
        """处理 hasn-node 回传的 TaskResult"""
        async with async_db_session() as session:
            return await self._handle_task_result_in_session(
                session=session,
                run_id=run_id,
                status=status,
                reporting_agent_id=reporting_agent_id,
                prompt_snapshot=prompt_snapshot,
                output=output,
                error=error,
                model=model,
                token_usage=token_usage,
                duration_ms=duration_ms,
            )

    async def _handle_task_result_in_session(
        self,
        session: AsyncSession,
        run_id: int,
        status: str,
        reporting_agent_id: str,
        prompt_snapshot: str | None = None,
        output: str | None = None,
        error: str | None = None,
        model: str | None = None,
        token_usage: dict[str, int] | None = None,
        duration_ms: int | None = None,
    ) -> bool:
        stmt = select(HasnTaskRun).where(HasnTaskRun.id == run_id)
        result = await session.execute(stmt)
        task_run = result.scalar_one_or_none()

        if not task_run:
            logger.warning(f'[TaskScheduler] task_run {run_id} not found')
            return False

        if task_run.agent_id != reporting_agent_id:
            raise errors.ForbiddenError(msg='agent cannot report this task_run')

        task_run.status = status
        if prompt_snapshot:
            task_run.prompt_snapshot = prompt_snapshot
        task_run.output = output
        task_run.error = error
        task_run.model = model
        task_run.token_usage = token_usage
        task_run.duration_ms = duration_ms
        task_run.finished_at = datetime.now(tz.utc)

        # 更新 hasn_task 的 last_status/last_error
        task_stmt = select(HasnTask).where(HasnTask.id == task_run.task_id)
        task_result = await session.execute(task_stmt)
        task = task_result.scalar_one_or_none()
        if task:
            task.last_status = status
            task.last_error = error

        await session.commit()
        return True


task_scheduler = TaskSchedulerService()
