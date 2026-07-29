import sqlalchemy as sa

from celery import states
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import PickleType
from typing import Any, cast

from backend.common.model import MappedBase, TimeZone
from backend.utils.timezone import timezone

"""
重写 celery.backends.database.models 内部所有模型，适配 fba 创建表和 alembic 迁移
"""


class Task(MappedBase):
    """Task result/status."""

    __tablename__ = 'task_result'
    __table_args__ = {'comment': '任务结果表'}

    id: Mapped[int] = mapped_column(
        sa.Integer,
        sa.Sequence('task_id_sequence'),
        primary_key=True,
        autoincrement=True,
    )
    task_id: Mapped[str] = mapped_column(sa.String(155), unique=True)
    status: Mapped[str] = mapped_column(sa.String(64), default=states.PENDING)
    result: Mapped[object | None] = mapped_column(PickleType, nullable=True)
    date_done: Mapped[object | None] = mapped_column(
        TimeZone,
        default=timezone.now,
        onupdate=timezone.now,
        nullable=True,
    )
    traceback: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    def to_dict(self) -> dict[str, object | None]:
        return {
            'task_id': self.task_id,
            'status': self.status,
            'result': self.result,
            'traceback': self.traceback,
            'date_done': self.date_done,
        }

    def __repr__(self) -> str:
        return f'<Task {self.task_id} state: {self.status}>'

    @classmethod
    def configure(cls, schema: str | None = None, name: str | None = None) -> None:
        table = cast(Any, cls.__table__)
        table.schema = schema
        sequence = table.c.id.default
        if isinstance(sequence, sa.Sequence):
            sequence.schema = schema
        table.name = name or cls.__tablename__


class TaskExtended(Task):
    """For the extend result."""

    __tablename__ = 'task_result'
    __table_args__ = {'extend_existing': True, 'comment': '任务结果表'}

    name: Mapped[str | None] = mapped_column(sa.String(155), nullable=True)
    args: Mapped[bytes | None] = mapped_column(sa.LargeBinary, nullable=True)
    kwargs: Mapped[bytes | None] = mapped_column(sa.LargeBinary, nullable=True)
    worker: Mapped[str | None] = mapped_column(sa.String(155), nullable=True)
    retries: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    queue: Mapped[str | None] = mapped_column(sa.String(155), nullable=True)

    @property
    def task_kwargs(self) -> bytes | None:
        """为响应 schema 暴露不与 Pydantic 构造参数冲突的属性名。"""
        return self.kwargs

    def to_dict(self) -> dict[str, object | None]:
        task_dict = super().to_dict()
        task_dict.update({
            'name': self.name,
            'args': self.args,
            'kwargs': self.kwargs,
            'worker': self.worker,
            'retries': self.retries,
            'queue': self.queue,
        })
        return task_dict


class TaskSet(MappedBase):
    """TaskSet result."""

    __tablename__ = 'task_set_result'
    __table_args__ = {'comment': '任务集结果表'}

    id: Mapped[int] = mapped_column(
        sa.Integer,
        sa.Sequence('taskset_id_sequence'),
        autoincrement=True,
        primary_key=True,
    )
    taskset_id: Mapped[str] = mapped_column(sa.String(155), unique=True)
    result: Mapped[object | None] = mapped_column(PickleType, nullable=True)
    date_done: Mapped[object | None] = mapped_column(TimeZone, default=timezone.now, nullable=True)

    def __init__(self, taskset_id: str, result: object) -> None:
        self.taskset_id = taskset_id
        self.result = result

    def to_dict(self) -> dict[str, object | None]:
        return {
            'taskset_id': self.taskset_id,
            'result': self.result,
            'date_done': self.date_done,
        }

    def __repr__(self) -> str:
        return f'<TaskSet: {self.taskset_id}>'

    @classmethod
    def configure(cls, schema: str | None = None, name: str | None = None) -> None:
        table = cast(Any, cls.__table__)
        table.schema = schema
        sequence = table.c.id.default
        if isinstance(sequence, sa.Sequence):
            sequence.schema = schema
        table.name = name or cls.__tablename__
