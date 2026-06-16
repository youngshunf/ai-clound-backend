"""平台默认配置服务（云端权威，单行下发）。

职责：
  - get_effective_config：取生效配置 + revision；无行时返回出厂默认 + 其确定性 revision（零 fake）。
  - update_config：Admin 覆盖式写 + 重算 revision（sha256(canonical_json)[:16]）。
  - coalesce_runtime_models：把平台默认 agent 运行时四槽逐槽合并到 per-agent 配置之下
    （agent 显式非空必胜，None → 平台默认）。

revision 范式对齐 ``common_skills_service``：内容变 → 指纹变 → daemon 比对重拉。
设计事实源：docs/hasn-node设计文档/运行时配置下发/01-平台默认配置下发机制.md
"""

from __future__ import annotations

import hashlib
import json

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn.model.hasn_platform_default_config import HasnPlatformDefaultConfig
from backend.app.hasn.schema.hasn_agents import AgentRuntimeModels
from backend.app.hasn.schema.hasn_platform_default_config import (
    PlatformDefaultConfig,
    PlatformDefaultConfigResponse,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 单行权威键
_CONFIG_KEY = 'global'

# 平台出厂默认（与 hasn-node/config/default.toml [media] 对齐；Admin 未配置时的兜底）。
# agent_runtime.models 全 None = 不强制平台默认（分身/owner/节点既有链路决定），运营按需在 Admin 填。
DEFAULT_PLATFORM_CONFIG: dict = {
    'node': {
        'media': {
            'image_models': ['gpt-image-2', 'dall-e-3'],
            'tts_models': ['tts-1', 'tts-1-hd'],
            'stt_models': ['whisper-1'],
        }
    },
    'agent_runtime': {
        'models': {
            'main': None,
            'fast': None,
            'vision': None,
            'delegation': None,
        }
    },
}


def compute_revision(config_json: dict) -> str:
    """配置内容指纹：sha256(canonical_json)[:16]。

    canonical = json.dumps(sort_keys, 紧凑分隔, 不转义非 ASCII)——同一配置恒得同一 revision。
    """
    canonical = json.dumps(config_json, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]


def coalesce_runtime_models(agent: AgentRuntimeModels, platform: AgentRuntimeModels) -> AgentRuntimeModels:
    """逐槽合并 agent 运行时模型：agent 显式非空必胜，None → 平台默认。"""
    return AgentRuntimeModels(
        main=agent.main or platform.main,
        fast=agent.fast or platform.fast,
        vision=agent.vision or platform.vision,
        delegation=agent.delegation or platform.delegation,
    )


class PlatformDefaultConfigService:
    """平台默认配置读写（云端权威单行）。"""

    @staticmethod
    async def _get_row(db: AsyncSession) -> HasnPlatformDefaultConfig | None:
        return (
            await db.execute(
                sa.select(HasnPlatformDefaultConfig).where(HasnPlatformDefaultConfig.config_key == _CONFIG_KEY).limit(1)
            )
        ).scalar_one_or_none()

    async def get_effective_config(self, db: AsyncSession) -> tuple[PlatformDefaultConfig, str]:
        """取生效配置 + revision。

        无行 → 出厂默认 + compute_revision(默认)（确定性，daemon 拉到稳定 revision）。
        有行但 revision 为空（历史/迁移） → 据 config_json 现算（保持比对稳定）。
        """
        row = await self._get_row(db)
        raw = row.config_json if (row and row.config_json) else DEFAULT_PLATFORM_CONFIG
        config = PlatformDefaultConfig.model_validate(raw)
        dumped = config.model_dump(mode='json')
        revision = row.revision if (row and row.revision) else compute_revision(dumped)
        return config, revision

    async def get_response(self, db: AsyncSession) -> PlatformDefaultConfigResponse:
        """组装读取出参（含 revision + 元信息）。"""
        row = await self._get_row(db)
        config, revision = await self.get_effective_config(db)
        return PlatformDefaultConfigResponse(
            config=config,
            revision=revision,
            updated_by=(row.updated_by if row else None),
            updated_time=(row.updated_time if row else None),
        )

    async def get_platform_runtime_models(self, db: AsyncSession) -> AgentRuntimeModels:
        """取平台默认 agent 运行时四槽（供 per-agent coalesce）。"""
        config, _ = await self.get_effective_config(db)
        return config.agent_runtime.models

    async def update_config(
        self, db: AsyncSession, *, config: PlatformDefaultConfig, updated_by: str | None
    ) -> PlatformDefaultConfigResponse:
        """Admin 覆盖式写 + 重算 revision；首次保存即建单行。

        不在此 commit（API 经 CurrentSessionTransaction 自动提交），仅 flush。
        """
        config_json = config.model_dump(mode='json')
        revision = compute_revision(config_json)
        row = await self._get_row(db)
        if row is None:
            row = HasnPlatformDefaultConfig(
                config_key=_CONFIG_KEY,
                config_json=config_json,
                revision=revision,
                updated_by=updated_by,
            )
            db.add(row)
        else:
            row.config_json = config_json
            row.revision = revision
            row.updated_by = updated_by
        await db.flush()
        await db.refresh(row)
        return PlatformDefaultConfigResponse(
            config=config,
            revision=revision,
            updated_by=row.updated_by,
            updated_time=row.updated_time,
        )


platform_default_config_service = PlatformDefaultConfigService()
