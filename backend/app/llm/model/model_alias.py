"""模型别名映射表

用于将 Claude Agent SDK 请求的 Anthropic 模型名称映射到实际可用的模型。
例如：claude-sonnet-4-5-20250929 -> [gpt-4o, deepseek-chat, ...]

@author Ysf
"""

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class ModelAlias(Base):
    """模型别名映射表

    将 Anthropic 格式的模型名称映射到实际可用的模型列表。
    支持按优先级故障转移：如果第一个模型失败，自动切换到下一个。

    典型用例：
    - alias_name: "claude-sonnet-4-5-20250929"
    - model_ids: [1, 5, 3]  # 分别对应 gpt-4o, deepseek-chat, qwen-plus
    - 按优先级顺序尝试，第一个成功的返回结果
    """

    __tablename__ = 'llm_model_alias'

    id: Mapped[id_key] = mapped_column(init=False)

    # 别名（Anthropic 模型名称格式，如 claude-sonnet-4-5-20250929）
    alias_name: Mapped[str] = mapped_column(
        sa.String(128),
        unique=True,
        index=True,
        comment='别名（Anthropic 模型名称）'
    )

    # 映射的模型 ID 列表（按优先级排序，第一个优先级最高）
    model_ids: Mapped[list] = mapped_column(
        sa.JSON,
        default=list,
        comment='映射的模型 ID 列表（按优先级排序）'
    )

    # 显示名称（可选，用于管理界面）
    display_name: Mapped[str | None] = mapped_column(
        sa.String(128),
        default=None,
        comment='显示名称'
    )

    # 描述（可选）
    description: Mapped[str | None] = mapped_column(
        sa.String(512),
        default=None,
        comment='描述'
    )

    # 是否启用
    enabled: Mapped[bool] = mapped_column(
        default=True,
        index=True,
        comment='是否启用'
    )
