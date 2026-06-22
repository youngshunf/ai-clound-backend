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
from backend.app.hasn.schema.hasn_agents import AgentRuntimeConfig, AgentRuntimeModels
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
            # 视频默认空：视频渠道尚需运营在 new-api 开通后，经 Admin 平台默认配置下发，
            # 否则填非空模型名会让分身 hasn.video.generate 直接撞 503 无渠道。
            'video_models': [],
        },
        # 应用专属配置（如 film 视频引擎 5 类模型 + 引擎包 manifest）已迁出 PDC，改由
        # hasn_app_catalog.config_json 权威承载、本服务 get_effective_config 聚合成
        # ``app_configs`` 下发（FILMCFG-1）；node 这里只保留跨应用的节点级媒体默认。
    },
    'agent_runtime': {
        'models': {
            'main': None,
            'fast': None,
            'vision': None,
            'delegation': None,
        },
        # 主模型 failover 全局兜底池（有序）。默认空=无兜底（单模型，行为不回归）；
        # 运营按需在 Admin「平台默认配置」页填入同网关可用的备选模型名，daemon 据此为每个
        # 分身的已解析主模型生成兜底链（剔除主模型自身、去重、保序）下发 runtime（LLMFAIL）。
        'model_fallback_pool': [],
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

        PDC 单行只承载 node + agent_runtime；各 AI-Native 应用专属配置由 hasn_app_catalog.config_json
        权威承载，这里聚合成 ``app_configs`` 拼进下发（FILMCFG-1）。

        revision 始终据"含 app_configs 的完整 dump"现算（compute_revision），而非取 row.revision——
        因为 PDC row.revision 只覆盖 node/agent_runtime（update_config 写库时已剥除 app_configs），
        若沿用会漏掉 catalog 配置变更。现算确保 catalog config_json 变 → revision 变 → daemon 重拉。
        """
        # 局部导入打破 service ↔ service 循环依赖（app_catalog_service 不反向依赖本服务）。
        from backend.app.hasn.service.app_catalog_service import get_all_app_configs

        row = await self._get_row(db)
        raw = row.config_json if (row and row.config_json) else DEFAULT_PLATFORM_CONFIG
        config = PlatformDefaultConfig.model_validate(raw)
        config = config.model_copy(update={'app_configs': await get_all_app_configs(db)})
        dumped = config.model_dump(mode='json')
        revision = compute_revision(dumped)
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

    async def build_effective_runtime_config(self, db: AsyncSession, raw: dict | None) -> dict | None:
        """把平台默认四槽合并进 per-agent runtime_config（runtime-facing 出参，如 Agent /profile 拉取）。

        - 仅合并 models 槽（agent 显式非空必胜，None → 平台默认）；knobs 原样透传（本期不做平台默认）。
        - raw=None 且平台四槽全空 → None（保持"全默认"语义，零行为变化）。
        - **不用于 owner GET 编辑器出参**：那里须返回 raw（null=跟随默认），否则覆盖式 PUT 会把平台默认冻结为 agent 显式值。
        """
        platform_models = await self.get_platform_runtime_models(db)
        has_platform = any([
            platform_models.main,
            platform_models.fast,
            platform_models.vision,
            platform_models.delegation,
        ])
        if raw is None and not has_platform:
            return None
        cfg = AgentRuntimeConfig.model_validate(raw or {})
        merged = coalesce_runtime_models(cfg.models, platform_models)
        cfg = cfg.model_copy(update={'models': merged})
        return cfg.model_dump(mode='json')

    async def update_config(
        self, db: AsyncSession, *, config: PlatformDefaultConfig, updated_by: str | None
    ) -> PlatformDefaultConfigResponse:
        """Admin 覆盖式写 PDC 单行（node + agent_runtime）+ 重算 row.revision；首次保存即建单行。

        ``app_configs`` 是各应用 hasn_app_catalog.config_json 的只读聚合，权威不在 PDC——写库前
        必须剥除，否则会把应用配置反向冻结进 PDC 行（污染权威、且与 catalog 漂移）。

        返回的 config/revision 经 get_effective_config 重新组装，使响应 revision 与真实下发口径
        （含 app_configs）一致，避免 Admin 看到的 revision 与 daemon 拉到的不符。

        不在此 commit（API 经 CurrentSessionTransaction 自动提交），仅 flush。
        """
        config_json = config.model_dump(mode='json')
        config_json.pop('app_configs', None)  # PDC 表只存 node + agent_runtime
        row_revision = compute_revision(config_json)
        row = await self._get_row(db)
        if row is None:
            row = HasnPlatformDefaultConfig(
                config_key=_CONFIG_KEY,
                config_json=config_json,
                revision=row_revision,
                updated_by=updated_by,
            )
            db.add(row)
        else:
            row.config_json = config_json
            row.revision = row_revision
            row.updated_by = updated_by
        await db.flush()
        await db.refresh(row)
        effective, revision = await self.get_effective_config(db)
        return PlatformDefaultConfigResponse(
            config=effective,
            revision=revision,
            updated_by=row.updated_by,
            updated_time=row.updated_time,
        )


platform_default_config_service = PlatformDefaultConfigService()
