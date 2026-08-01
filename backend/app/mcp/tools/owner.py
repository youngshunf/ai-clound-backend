"""平台工具 · owner 域（主人画像完整度，「了解主人」模块 19）。

`hasn.owner.coverage.get`：采访分身（规划参谋）开工先读「主人 5 个画像维度还缺哪几维」，
据此定向采访（缺什么采访什么）——不一次性甩问卷。纯云端只读：直调云端权威
`OwnerProfileCoverageService.assess_if_stale`（owner_memory 版本领先时惰性重判，否则快读）。

⚠️ doc19 §10（2026-07-31）：`hasn.owner.memory.contribute` **不再内联合并**——云端 LLM 合并已
整体退役，合并由主脑分身在它自己的设备上执行。因此「写入→合并→重判」不再在一次采访轮内闭环：
contribute 只入贡献流，`owner_memory.version` 要等下一轮记忆整理才推进，coverage 也要到那时才
重判。**这是显式承认的体验回退**（doc19 §10 缓解手段：主人「立即整理」+ 云端合并待办 §5.5），
工具描述必须如实告诉分身，不许让它对主人说「已合并」。

owner 身份由 Agent JWT/MCP Key 解析出的 `agent_context.owner_hasn_id` 强制，绝不入请求体
（CLAUDE.md hasn-mcp/BackendGateway 统一调用 + owner 隔离硬约束）。读类无 scope（只读自己主人数据）。

设计事实源：docs/hasn-node设计文档/19-规划与目标管理/03-了解主人：采访建档·完整度判定·主动规划闭环设计.md §5.3 / §6.1。
"""

from __future__ import annotations

from typing import Any

from backend.app.hasn_memory.service.owner_memory_service import (
    MEMORY_CONTRIBUTE_PENDING_NOTE,
    owner_memory_service,
)
from backend.app.hasn_memory.service.owner_profile_coverage_service import owner_profile_coverage_service
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool, require_owner_hasn_id
from backend.database.db import async_db_session

NAMESPACE = 'hasn.owner'


class OwnerCoverageGetTool(BaseTool):
    """读主人 5 维画像完整度（缺什么采访什么）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.owner.coverage.get'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def min_trust_level(self) -> int | None:
        # L3 工具门（doc08 §4·RT3·云端半场）：本工具披露主人 5 维画像——含**居住地址 residence
        # （位置）**+ 兴趣/工作/目标/规划（偏好详情）。位置口径 = 密友(4)，取最严档；对外会话里
        # 对端不足 4 不得读主人画像（主会话/主人自环不受限，见 server.call_tool）。
        return 4

    @property
    def description(self) -> str:
        return (
            '读取主人 5 个画像维度（兴趣爱好 interests / 工作情况 work / 居住地址 residence / '
            '近期目标 goals / 人生规划 life_plan）的了解完整度，用于采访建档时定向采访「还缺哪几维」。'
            '返回每维度 status（missing 完全不知 / partial 知道一点 / sufficient 已足够）+ 已知摘要 summary + '
            '待补提示 missing_hint，以及 all_sufficient（5 维全 sufficient 则无需再采访）、'
            'sufficient_count、next_dimensions（建议优先采访的非充分维度）、memory_version。'
            '只读主人自己的数据，无需授权。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}, 'additionalProperties': False}

    @property
    def required_scopes(self) -> list[str]:
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        # 只读：用普通 session（assess_if_stale 内部按需重判并自提交，独立 session 安全，
        # 与 owner 读 API 同范式）。owner 身份强制取自 agent_context，绝不读 arguments。
        async with async_db_session() as db:
            return await owner_profile_coverage_service.assess_if_stale(
                db, owner_id=require_owner_hasn_id(agent_context)
            )


class OwnerMemoryContributeTool(BaseTool):
    """采访建档时把「了解到的一段主人信息」写入 owner 记忆（contribute→合并→重判闭环）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.owner.memory.contribute'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '把采访/相处中了解到的一段主人信息写入 owner 记忆。每问到一段（如某个兴趣、工作角色、'
            '近期目标）就调一次，content 写自然语言的「事实陈述」（如「主人是后端工程师，主攻 Rust 与分布式系统」'
            '「主人近期目标是三个月内通过 PMP 认证」），落 contribution(pending)。'
            'owner/agent 身份恒取自调用凭证，绝不入参；隐私克制：居住地址只写粗粒度（城市/城区级，不写门牌），'
            '主人未明说的别替他臆造。'
            '返回 {accepted, contribution_id, pending_merge, merge_note, owner_memory_version}：'
            '**本工具只收录，不当场合并**——合并由主脑分身在它所在的设备上执行，这条观察会在下次记忆整理时'
            '并入主人档案。对主人就按 merge_note 如实说「已记下来了，下次整理时并入」，'
            '**绝不能**说「已合并 / 已更新档案」，也别编造「后台异步合并已完成」之类系统里不存在的说法。'
            'owner_memory_version 是当前档案版本，本次调用不会改变它；它变了才说明整理真的发生过。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'content': {
                    'type': 'string',
                    'description': '一段关于主人的自然语言事实陈述（采访所得），非空。',
                    'minLength': 1,
                },
            },
            'required': ['content'],
            'additionalProperties': False,
        }

    @property
    def required_scopes(self) -> list[str]:
        # 写自己主人记忆是分身本职（对齐既有 Agent REST /memory/contribute 无 scope 门）；
        # owner 隔离由 agent_context.owner_hasn_id 强制，经 MCP 网关审计。
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        content = str(arguments.get('content') or '').strip()
        if not content:
            return {'accepted': False, 'pending_merge': False, 'reason': 'empty_content'}
        owner_id = require_owner_hasn_id(agent_context)
        # 与 Agent REST /memory/contribute 同一服务、同一语义：**只落 contribution，不内联合并**
        # （doc19 §10，云端 LLM 合并已退役）。合并由主脑分身在它自己的设备上做。
        async with async_db_session() as db:
            accepted = await owner_memory_service.contribute(
                db, owner_id=owner_id, agent_hasn_id=agent_context.hasn_id, content=content
            )
            if not accepted.get('accepted'):
                await db.rollback()
                return {'accepted': False, 'pending_merge': False, 'reason': accepted.get('reason')}
            memory = await owner_memory_service.get_owner_memory(db, owner_id=owner_id)
            await db.commit()
            return {
                'accepted': True,
                'contribution_id': accepted.get('contribution_id'),
                'pending_merge': True,
                'merge_note': MEMORY_CONTRIBUTE_PENDING_NOTE,
                'owner_memory_version': int(memory.get('version') or 0),
            }


class _OwnerPeriodicClaimTool(BaseTool):
    """「每日关注·了解主人」周期节奏闸基类：每日简报每天跑，采访/成长会话不能每天派。

    分身先调 `hasn.owner.coverage.get` 看画像够不够，再据此调本组 claim 工具「认领本轮派发权」：
    认领成功（claimed=true，首次或距上次超冷却期）才真调 `hasn.task.dispatch` 派对应会话；
    认领失败（claimed=false，冷却期内已派过）就**别再派**，只在简报里留常驻卡片提醒。
    跨设备并发只赢一方（云端原子认领）。owner 恒取自调用凭证，绝不入参。默认冷却 7 天。
    """

    _NAME = ''
    _KIND = ''  # 「采访」/「成长复盘」，仅用于文案

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return self._NAME

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'cooldown_days': {
                    'type': 'integer',
                    'description': '冷却天数，距上次派发不足此天数则不认领（默认 7=每周一次）。',
                    'minimum': 1,
                    'maximum': 90,
                }
            },
            'additionalProperties': False,
        }

    @property
    def required_scopes(self) -> list[str]:
        return []

    async def _do_claim(self, db, owner: str, cooldown_days: int) -> bool:  # noqa: ANN001
        raise NotImplementedError

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        cooldown = arguments.get('cooldown_days')
        cooldown_days = int(cooldown) if isinstance(cooldown, int) and cooldown > 0 else 7
        async with async_db_session() as db:
            claimed = await self._do_claim(db, require_owner_hasn_id(agent_context), cooldown_days)
            await db.commit()
            return {'claimed': bool(claimed), 'cooldown_days': cooldown_days}


class OwnerOnboardingClaimTool(_OwnerPeriodicClaimTool):
    """认领「派一次了解主人采访会话」的权（周期闸，画像不完整时用）。"""

    _NAME = 'hasn.owner.onboarding.claim'
    _KIND = '采访'

    @property
    def description(self) -> str:
        return (
            '「每日关注·了解主人」节奏闸：主人画像还不完整（hasn.owner.coverage.get 返回 all_sufficient=false）时，'
            '调本工具认领「本轮是否该派一次了解主人的采访会话」。claimed=true 表示首次 / 距上次采访已超冷却期'
            '（默认 7 天）→ 你应真调 hasn.task.dispatch 派一个采访会话（会话里读 coverage、对不 sufficient 的维度'
            '用 hasn.session.ask 一次问一个、每得到答复调 hasn.owner.memory.contribute 写入、直到 5 维全 sufficient）；'
            'claimed=false 表示冷却期内已派过 → **别再派**，简报里留常驻卡片提醒即可（避免每天打扰主人）。'
            'owner 恒取自调用凭证。'
        )

    async def _do_claim(self, db, owner: str, cooldown_days: int) -> bool:  # noqa: ANN001
        from backend.app.hasn_plan.service.plan_app_service import plan_service

        return await plan_service.claim_profile_onboarding(db, owner=owner, cooldown_days=cooldown_days)


class OwnerGrowthClaimTool(_OwnerPeriodicClaimTool):
    """认领「派一次成长复盘/主动规划会话」的权（周期闸，画像完整后用）。"""

    _NAME = 'hasn.owner.growth.claim'
    _KIND = '成长复盘'

    @property
    def description(self) -> str:
        return (
            '「每日关注·了解主人」节奏闸：主人画像已完整（hasn.owner.coverage.get 返回 all_sufficient=true）时，'
            '调本工具认领「本轮是否该派一次陪主人成长的会话」。claimed=true 表示首次 / 距上次已超冷却期'
            '（默认 7 天）→ 你应真调 hasn.task.dispatch 派一个成长会话（会话里读主人记忆与现有目标现状、分析处境、'
            '给「如何提升自己 / 达成目标」的具体建议、用 hasn.session.ask 与主人沟通确认，主人确认后调 hasn.plan.* '
            '建/调目标·待办·排日程；已有目标就复盘调整，没有就先建初始规划）；claimed=false 表示本周期已派过 → **别再派**。'
            'owner 恒取自调用凭证。'
        )

    async def _do_claim(self, db, owner: str, cooldown_days: int) -> bool:  # noqa: ANN001
        from backend.app.hasn_plan.service.plan_app_service import plan_service

        return await plan_service.claim_growth_review(db, owner=owner, cooldown_days=cooldown_days)


OWNER_TOOLS: list[BaseTool] = [
    OwnerCoverageGetTool(),
    OwnerMemoryContributeTool(),
    OwnerOnboardingClaimTool(),
    OwnerGrowthClaimTool(),
]
