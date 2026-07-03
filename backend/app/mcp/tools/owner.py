"""平台工具 · owner 域（主人画像完整度，「了解主人」模块 19）。

`hasn.owner.coverage.get`：采访分身（规划参谋）开工先读「主人 5 个画像维度还缺哪几维」，
据此定向采访（缺什么采访什么）——不一次性甩问卷。纯云端只读：直调云端权威
`OwnerProfileCoverageService.assess_if_stale`（owner_memory 版本领先时惰性重判，否则快读），
所以采访分身 contribute→合并（owner_memory.version+1）后再调本工具，即拿到重判后的最新缺口
（写入→合并→重判闭环落在分身定向采访前这一点，无需把 LLM 打分塞进 contribute 热路径）。

owner 身份由 Agent JWT/MCP Key 解析出的 `agent_context.owner_hasn_id` 强制，绝不入请求体
（CLAUDE.md hasn-mcp/BackendGateway 统一调用 + owner 隔离硬约束）。读类无 scope（只读自己主人数据）。

设计事实源：docs/hasn-node设计文档/19-规划与目标管理/03-了解主人：采访建档·完整度判定·主动规划闭环设计.md §5.3 / §6.1。
"""

from __future__ import annotations

from typing import Any

from backend.app.hasn_memory.service.owner_memory_service import owner_memory_service
from backend.app.hasn_memory.service.owner_profile_coverage_service import owner_profile_coverage_service
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool
from backend.common.log import log
from backend.database.db import async_db_session

NAMESPACE = 'hasn.owner'

_MERGE_ERROR_MAX = 200


def _short_merge_error(exc: Exception) -> str:
    """把合并异常收敛成给分身看的短摘要（截断，避免把整段 traceback/堆栈塞回工具返回）。"""
    msg = str(exc).strip() or exc.__class__.__name__
    return msg[:_MERGE_ERROR_MAX]


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
                db, owner_id=agent_context.owner_hasn_id
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
            '「主人近期目标是三个月内通过 PMP 认证」），落 contribution(pending) 并尽力触发一次 owner 级合并'
            '（合并进 owner_memory.content，version+1）。合并成功后再调 hasn.owner.coverage.get 即拿到重判后的'
            '最新缺口（写入→合并→重判闭环）。owner/agent 身份恒取自调用凭证，绝不入参；隐私克制：居住地址只写'
            '粗粒度（城市/城区级，不写门牌），主人未明说的别替他臆造。'
            '返回 {accepted, merged, version, merge_deferred, merge_error}：'
            'merged=true 表示已合并进 owner_memory（version 即新版本）；merged=false 且 merge_deferred=true 表示'
            '观察已收录但本次合并未成（merge_error 给原因），会自动重试——此时对主人**如实**说「已记下来了，'
            '正在合并」即可，**绝不能**编造「后台异步合并已完成/稍后翻 sufficient」之类系统里不存在的说法，'
            '也别声称已合并完成。'
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
            return {'accepted': False, 'merged': False, 'version': None, 'reason': 'empty_content'}
        owner_id = agent_context.owner_hasn_id
        # 复用既有 owner_memory_service（与 Agent REST /memory/contribute 同一服务、同一语义）：
        # 先落 contribution 并提交（即便合并失败也不丢观察），再尽力合并；合并失败如实延后、零 fake。
        async with async_db_session() as db:
            accepted = await owner_memory_service.contribute(
                db, owner_id=owner_id, agent_hasn_id=agent_context.hasn_id, content=content
            )
            if not accepted.get('accepted'):
                await db.rollback()
                return {'accepted': False, 'merged': False, 'version': None, 'reason': accepted.get('reason')}
            await db.commit()
            merged = False
            version: int | None = None
            merge_deferred = False
            merge_error: str | None = None
            try:
                outcome = await owner_memory_service.merge_owner_memory(db, owner_id=owner_id)
                merged = bool(outcome.get('merged'))
                version = outcome.get('version')
                await db.commit()
            except Exception as exc:
                await db.rollback()
                merge_deferred = True
                merge_error = _short_merge_error(exc)
                log.warning(f'owner memory merge deferred for {owner_id}: {exc}')
            return {
                'accepted': True,
                'merged': merged,
                'version': version,
                'merge_deferred': merge_deferred,
                'merge_error': merge_error,
                'contribution_id': accepted.get('contribution_id'),
            }


OWNER_TOOLS: list[BaseTool] = [OwnerCoverageGetTool(), OwnerMemoryContributeTool()]
