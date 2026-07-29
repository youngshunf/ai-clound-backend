import uuid

from typing import Any
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_project.model._base import HasnProjectAppBase
from backend.common.model import UniversalText

_ASSET_URI_TEMPLATE = 'hasn://asset/' + '{id}'


class HasnProject(HasnProjectAppBase):
    """平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）

    云端权威 ID 源：本表 id（UUID）即项目「云端权威 ID」（= server_id），凡进
    hasn://project/{id} URI / 卡片 / 分享路径 / 深链一律用此 ID（守「本地 ID 永不上 URI」铁律）。
    定位（doc38）：第三条轴（项目轴）——只回答「为了哪件事」；不是权限边界、不是应用挂载点、
    不接管应用容器。各应用容器 / 产物 / 工作会话以可空 project_id 联邦挂靠到本表（doc38 §4）。
    """

    __tablename__ = 'hasn_project'
    __table_args__: Any = (
        sa.UniqueConstraint(
            'owner_id',
            'client_request_id',
            name='uq_hasn_project_owner_client_request',
        ),
        {
            'schema': 'hasn_project',
            'comment': '平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）',
        },
    )

    id: Mapped[UUID] = mapped_column(
        sa.UUID(), primary_key=True, default=uuid.uuid4, init=False, comment='云端权威 ID（server_id）'
    )
    owner_id: Mapped[str] = mapped_column(
        sa.String(40), default='', comment='归属主人 HASN ID（owner 隔离键，逻辑引用 public.hasn_humans，绝不跨 owner）'
    )
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment='项目名')
    goal: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='一句话目标（分身建项目时采集，供聚合视图与派发上下文注入，可空）'
    )
    cover_asset_uri: Mapped[str | None] = mapped_column(
        UniversalText,
        default=None,
        comment=(
            f'封面图资产引用（{_ASSET_URI_TEMPLATE}，来源=上传/素材下载/AI 生成；'
            '序列化边界换 CDN 签名 URL，不存直链；可空回落品牌渐变+首字）'
        ),
    )
    status: Mapped[str] = mapped_column(
        sa.String(16), default='active', comment='状态 (active:进行中:blue/archived:已归档:gray)'
    )
    bound_agent_id: Mapped[str | None] = mapped_column(
        sa.String(40),
        default=None,
        comment='默认协作分身 HASN ID（owner 名下 a_* 分身，null=未绑定；对齐 doc21 AppCollab，列名铁律 doc38 §8）',
    )
    client_request_id: Mapped[str | None] = mapped_column(
        sa.String(128),
        default=None,
        comment='创建请求幂等键（主人范围唯一；如两阶段派发 launch_trace_id；可空表示普通非幂等创建）',
    )
    enterprise_id: Mapped[str | UUID | None] = mapped_column(
        sa.UUID(), default=None, comment='企业归属（双模化，个人 NULL / 企业非空，对齐 GE，可空）'
    )
