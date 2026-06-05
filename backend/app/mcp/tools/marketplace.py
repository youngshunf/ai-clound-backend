"""平台工具 · marketplace 域（15-技能市场/11-doc 权威实现）

Agent 自助操作「能力市场」的 MCP 投影——逛市场、看详情、给自己装/卸技能、把本地
打包好的技能/模板包发布为用户资源。补齐 11-doc §0.1 指出的「能力端到端存在、唯独缺
MCP 投影」缺口（G1）。

设计要点（与 11-doc 对齐）：
- **Agent-as-actor**：身份取自 Agent 凭证（`agent_hasn_id`/`owner_hasn_id`/`owner_id`=
  owner user_id），绝不用 `owner_user_id` 冒名或做隔离（[[project_identity_by_auth]]）。
- **绑服务层不绑裸 CRUD**：浏览绑 `search_service` + skill/template service；装卸绑
  `agent_profile_service.attach/detach_skill_cloud_first`（云端权威 → bump revision →
  同步 → daemon 下行 re-provision，G2：Agent 装到**自己**，直调 service 无需新 HTTP 路由）；
  发布绑 `marketplace_skill/template_service.upload_user_*`（系统 S3 + 版本表 + 状态机）。
- **两个正交维度**：维度① 能力授权（三态 allow/ask/deny）由 server.call_tool 统一判定，
  工具内不二次校验；维度② 资源可及性（published 才可装、只能发自己 `user/{hasn_id}` 资源）
  在 execute 内运行时判定并返回 reason，不静默成功（G7）。
- **跨执行位置**：浏览/装卸/发布 `execution_location=cloud`；打包（package_*）是
  `execution_location=local`，落 hasn-node `hasn-mcp`（本文件不含——见 11-doc §4）。
  发布跨位置桥接：本地打包产出 zip → base64 → 本文件 publish_* 云端消费 `content`。
"""

from __future__ import annotations

import base64

from typing import TYPE_CHECKING, Any

from backend.app.mcp.tools.base import BaseTool
from backend.common.exception import errors
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 20
# 单次 publish 上传包上限（防止把超大二进制塞进 MCP JSON 参数）。
_MAX_PACKAGE_BYTES = 8 * 1024 * 1024


def _normalize_installed_skill_ids(skills: Any) -> list[str]:
    """把 hasn_agents.skills（JSONB）归一成 skill_id 字符串清单（保序去重）。

    与 hasn_agents_service._normalize_skill_ids 同语义（纯归一无状态，不反向依赖 service）。
    兼容 list[str] / list[{skill_id|id}] / {skill_id: version}。
    """
    out: list[str] = []
    if isinstance(skills, list):
        for item in skills:
            sid: str | None = None
            if isinstance(item, str) and item.strip():
                sid = item.strip()
            elif isinstance(item, dict):
                raw = item.get('skill_id') or item.get('id')
                sid = str(raw) if raw else None
            if sid and sid not in out:
                out.append(sid)
    elif isinstance(skills, dict):
        for key in skills:
            if isinstance(key, str) and key.strip() and key.strip() not in out:
                out.append(key.strip())
    return out


def _skill_summary(item: dict[str, Any]) -> dict[str, Any]:
    """把 service 的完整技能 dict 投影成 Agent 浏览摘要（11-doc §2 A 组出参）。"""
    return {
        'skill_id': item.get('skill_id'),
        'namespace': item.get('namespace'),
        'slug': item.get('slug'),
        'name': item.get('name'),
        'description': item.get('description'),
        'source_type': item.get('source_type'),
        'is_official': item.get('is_official'),
        'status': item.get('status'),
    }


def _template_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        'template_id': item.get('template_id'),
        'namespace': item.get('namespace'),
        'slug': item.get('slug'),
        'name': item.get('name'),
        'description': item.get('description'),
        'template_type': item.get('template_type'),
        'status': item.get('status'),
    }


# ─────────────────────────── A 组 · 浏览（marketplace:read）───────────────────────────


class SearchSkillsTool(BaseTool):
    """搜索公开技能（绑 search_service.search_skills，只返回 published/public）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def namespace(self) -> str:
        return 'hasn.marketplace'

    @property
    def name(self) -> str:
        return 'hasn.marketplace.search_skills'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def description(self) -> str:
        return '搜索能力市场的公开技能（按关键词/分类/来源），返回可安装技能摘要列表'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'q': {'type': 'string', 'description': '搜索关键词（名称/描述/标签）'},
                'category': {'type': 'string', 'description': '分类筛选'},
                'source_type': {
                    'type': 'string',
                    'enum': ['official', 'huanxing', 'clawhub', 'github', 'user', 'third_party'],
                    'description': '来源筛选',
                },
                'limit': {
                    'type': 'integer',
                    'minimum': 1,
                    'maximum': _MAX_LIMIT,
                    'description': f'返回上限（默认 {_DEFAULT_LIMIT}）',
                },
                'lang': {'type': 'string', 'enum': ['zh', 'en'], 'description': '语言（默认 zh）'},
            },
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['marketplace:read']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.app.marketplace.service.search_service import search_service

        limit = min(max(int(arguments.get('limit', _DEFAULT_LIMIT)), 1), _MAX_LIMIT)
        lang = arguments.get('lang') or 'zh'
        async with async_db_session() as db:
            result = await search_service.search_skills(
                db,
                keyword=(arguments.get('q') or None),
                category=(arguments.get('category') or None),
                source_type=(arguments.get('source_type') or None),
                lang=lang,
                page=1,
                page_size=limit,
            )
        return {
            'results': [_skill_summary(item) for item in result.get('items', [])],
            'total': result.get('total', 0),
        }


class GetSkillTool(BaseTool):
    """查看技能详情（绑 search_service.get_skill_detail，公开可见技能）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def namespace(self) -> str:
        return 'hasn.marketplace'

    @property
    def name(self) -> str:
        return 'hasn.marketplace.get_skill'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def description(self) -> str:
        return '查看某个公开技能的详情（名称/描述/来源/分类/统计等）'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'skill_id': {'type': 'string', 'description': '技能标识 {namespace}/{slug}'},
                'lang': {'type': 'string', 'enum': ['zh', 'en'], 'description': '语言（默认 zh）'},
            },
            'required': ['skill_id'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['marketplace:read']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.app.marketplace.service.search_service import search_service

        skill_id = str(arguments.get('skill_id', '')).strip()
        if not skill_id:
            return {'found': False, 'reason': 'skill_id 必填'}
        lang = arguments.get('lang') or 'zh'
        async with async_db_session() as db:
            detail = await search_service.get_skill_detail(db, skill_id, lang)
        # 维度②：不存在 / 未发布 / 不可见 → 如实返回 found=false，不静默造数据。
        if not detail:
            return {'found': False, 'reason': '技能不存在或未发布（不可见）'}
        return {'found': True, 'skill': detail}


class SearchTemplatesTool(BaseTool):
    """搜索公开模板（Agent 模板/技能包/SOP 包）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def namespace(self) -> str:
        return 'hasn.marketplace'

    @property
    def name(self) -> str:
        return 'hasn.marketplace.search_templates'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def description(self) -> str:
        return '搜索能力市场的公开分身模板（按关键词/类型），返回模板摘要列表'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'q': {'type': 'string', 'description': '搜索关键词'},
                'template_type': {'type': 'string', 'description': '模板类型筛选（如 agent_template）'},
                'limit': {
                    'type': 'integer',
                    'minimum': 1,
                    'maximum': _MAX_LIMIT,
                    'description': f'返回上限（默认 {_DEFAULT_LIMIT}）',
                },
            },
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['marketplace:read']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.app.marketplace.service.search_service import search_service

        limit = min(max(int(arguments.get('limit', _DEFAULT_LIMIT)), 1), _MAX_LIMIT)
        template_type = (arguments.get('template_type') or '').strip()
        async with async_db_session() as db:
            result = await search_service.search_templates(
                db,
                keyword=(arguments.get('q') or None),
                page=1,
                page_size=limit,
            )
        items = result.get('items', [])
        # search_templates 不带 template_type 过滤——按需在结果上做精确过滤（零 fake，真实字段）。
        if template_type:
            items = [it for it in items if it.get('template_type') == template_type]
        return {'results': [_template_summary(item) for item in items], 'total': result.get('total', 0)}


class GetTemplateTool(BaseTool):
    """查看模板详情（绑 marketplace_template_service.get_by_resource_id_public）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def namespace(self) -> str:
        return 'hasn.marketplace'

    @property
    def name(self) -> str:
        return 'hasn.marketplace.get_template'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def description(self) -> str:
        return '查看某个公开分身模板的详情'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'template_id': {'type': 'string', 'description': '模板标识 {namespace}/{slug}'},
            },
            'required': ['template_id'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['marketplace:read']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.app.marketplace.service.marketplace_template_service import marketplace_template_service

        template_id = str(arguments.get('template_id', '')).strip()
        if not template_id:
            return {'found': False, 'reason': 'template_id 必填'}
        async with async_db_session() as db:
            try:
                template = await marketplace_template_service.get_by_resource_id_public(db=db, resource_id=template_id)
            except errors.NotFoundError:
                return {'found': False, 'reason': '模板不存在或未发布（不可见）'}
            detail = marketplace_template_service.format_template(template)
        return {'found': True, 'template': detail}


class ListInstalledTool(BaseTool):
    """列出当前 Agent 已安装技能（自检"我有什么能力"，按凭证身份）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def namespace(self) -> str:
        return 'hasn.marketplace'

    @property
    def name(self) -> str:
        return 'hasn.marketplace.list_installed'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def description(self) -> str:
        return '列出当前分身已安装的技能（从云端权威 Profile 读取）'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}}

    @property
    def required_scopes(self) -> list[str]:
        return ['marketplace:read']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.app.hasn.crud.crud_hasn_agents import hasn_agents_dao
        from backend.app.marketplace.service.search_service import search_service

        async with async_db_session() as db:
            agent = await hasn_agents_dao.get_by_hasn_id(db, hasn_id=agent_context.agent_hasn_id)
            skill_ids = _normalize_installed_skill_ids(getattr(agent, 'skills', None)) if agent else []
            installed: list[dict[str, Any]] = []
            for sid in skill_ids:
                # 富信息：命中市场则带名称；技能已下架/不可见则只回 skill_id（零 fake，不伪造名称）。
                detail = await search_service.get_skill_detail(db, sid, 'zh')
                installed.append({
                    'skill_id': sid,
                    'name': (detail or {}).get('name'),
                    'available': detail is not None,
                })
        return {'installed': installed, 'total': len(installed)}


# ─────────────────────────── B 组 · 安装（marketplace:install）───────────────────────────


class InstallSkillTool(BaseTool):
    """把市场技能装到**当前 Agent**（云端权威 → bump revision → daemon re-provision）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def namespace(self) -> str:
        return 'hasn.marketplace'

    @property
    def name(self) -> str:
        return 'hasn.marketplace.install_skill'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def risk_level(self) -> str:
        return 'medium'

    @property
    def description(self) -> str:
        return '把某个市场技能安装到你自己（当前分身），安装后由运行时下载技能包生效'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'skill_id': {'type': 'string', 'description': '技能标识 {namespace}/{slug}'},
            },
            'required': ['skill_id'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['marketplace:install']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.app.hasn.service.hasn_agents_service import agent_profile_service

        skill_id = str(arguments.get('skill_id', '')).strip()
        if not skill_id:
            return {'skill_id': skill_id, 'installed': False, 'reachable': False, 'reason': 'skill_id 必填'}

        async with async_db_session() as db:
            try:
                # G2：Agent 装到自己——owner_id=owner_hasn_id、hasn_id=agent_hasn_id、user_id=owner user_id。
                # 维度②：attach 内部校验技能 published/public，未发布抛 NotFoundError。
                result = await agent_profile_service.attach_skill_cloud_first(
                    db,
                    owner_id=agent_context.owner_hasn_id,
                    hasn_id=agent_context.agent_hasn_id,
                    skill_id=skill_id,
                    user_id=agent_context.owner_id,
                )
                await db.commit()
            except errors.NotFoundError as exc:
                # 维度②不可达：技能不存在/未发布，如实返回 reason，不静默成功。
                return {'skill_id': skill_id, 'installed': False, 'reachable': False, 'reason': str(exc.msg)}
        revision = getattr(result.agent, 'profile_revision', None)
        return {'skill_id': skill_id, 'installed': True, 'reachable': True, 'revision': revision}


class UninstallSkillTool(BaseTool):
    """从当前 Agent 卸载技能（云端权威 → bump revision → re-provision）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def namespace(self) -> str:
        return 'hasn.marketplace'

    @property
    def name(self) -> str:
        return 'hasn.marketplace.uninstall_skill'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def risk_level(self) -> str:
        return 'medium'

    @property
    def description(self) -> str:
        return '从你自己（当前分身）卸载某个已安装技能'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'skill_id': {'type': 'string', 'description': '技能标识 {namespace}/{slug}'},
            },
            'required': ['skill_id'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['marketplace:install']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.app.hasn.service.hasn_agents_service import agent_profile_service

        skill_id = str(arguments.get('skill_id', '')).strip()
        if not skill_id:
            return {'skill_id': skill_id, 'uninstalled': False, 'reason': 'skill_id 必填'}
        async with async_db_session() as db:
            result = await agent_profile_service.detach_skill_cloud_first(
                db,
                owner_id=agent_context.owner_hasn_id,
                hasn_id=agent_context.agent_hasn_id,
                skill_id=skill_id,
                user_id=agent_context.owner_id,
            )
            await db.commit()
        revision = getattr(result.agent, 'profile_revision', None)
        return {'skill_id': skill_id, 'uninstalled': True, 'revision': revision}


# ─────────────────────────── C 组 · 创作与发布（marketplace:publish）───────────────────────────


def _decode_package(arguments: dict[str, Any]) -> bytes:
    """从入参取出技能/模板 zip 内容（base64 桥接本地打包产物）。校验大小，零 fake。"""
    raw = arguments.get('package_base64')
    if not raw or not isinstance(raw, str):
        raise errors.RequestError(msg='package_base64 必填（本地 package_* 工具产出的 zip base64）')
    try:
        content = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError) as exc:
        raise errors.RequestError(msg=f'package_base64 解码失败: {exc}')
    if not content:
        raise errors.RequestError(msg='package_base64 解码后为空')
    if len(content) > _MAX_PACKAGE_BYTES:
        raise errors.RequestError(msg=f'包过大（>{_MAX_PACKAGE_BYTES} 字节），请改用桌面端发布大包')
    return content


_PUBLISH_INPUT_SCHEMA: dict[str, Any] = {
    'type': 'object',
    'properties': {
        'package_base64': {'type': 'string', 'description': '本地 package_* 工具产出的 zip 包 base64 内容'},
        'filename': {'type': 'string', 'description': '原始文件名（可选）'},
        'slug': {'type': 'string', 'description': '资源 slug（可选，缺省从包元数据推导）'},
        'changelog': {'type': 'string', 'description': '版本说明（可选）'},
        'submit_review': {'type': 'boolean', 'description': '是否提交审核（默认 false，仅存草稿）'},
    },
    'required': ['package_base64'],
}


class PublishSkillTool(BaseTool):
    """发布技能包为**当前用户**资源（user/{owner_hasn_id}），默认 draft；可 submit_review。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def namespace(self) -> str:
        return 'hasn.marketplace'

    @property
    def name(self) -> str:
        return 'hasn.marketplace.publish_skill'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def risk_level(self) -> str:
        return 'high'

    @property
    def description(self) -> str:
        return '把打包好的技能 zip 发布为你（主人）的市场资源，默认存草稿；提交审核需主人确认'

    @property
    def input_schema(self) -> dict[str, Any]:
        return _PUBLISH_INPUT_SCHEMA

    @property
    def required_scopes(self) -> list[str]:
        return ['marketplace:publish']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.app.marketplace.service.marketplace_skill_service import marketplace_skill_service

        content = _decode_package(arguments)
        submit_review = bool(arguments.get('submit_review'))
        async with async_db_session() as db:
            # 维度②：归属由后端从凭证解析写入（user_id/hasn_id），不信入参身份。upload 内部 commit。
            skill = await marketplace_skill_service.upload_user_skill(
                db=db,
                user_id=agent_context.owner_id,
                hasn_id=agent_context.owner_hasn_id,
                content=content,
                filename=(arguments.get('filename') or None),
                slug=(arguments.get('slug') or None),
                changelog=(arguments.get('changelog') or None),
            )
            status = skill.status
            if submit_review:
                # 公开流程的主人确认通过工具三态（marketplace:publish=ask）闸门把控；这里推进状态机。
                reviewed = await marketplace_skill_service.submit_review(
                    db=db, resource_id=skill.skill_id, user_id=agent_context.owner_id
                )
                await db.commit()
                status = reviewed.status
        return {
            'skill_id': skill.skill_id,
            'namespace': skill.namespace,
            'slug': skill.slug,
            'status': status,
            'submitted_review': submit_review,
        }


class PublishTemplateTool(BaseTool):
    """发布模板包为当前用户资源，默认 draft；可 submit_review。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def namespace(self) -> str:
        return 'hasn.marketplace'

    @property
    def name(self) -> str:
        return 'hasn.marketplace.publish_template'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def risk_level(self) -> str:
        return 'high'

    @property
    def description(self) -> str:
        return '把打包好的分身模板 zip 发布为你（主人）的市场资源，默认存草稿；提交审核需主人确认'

    @property
    def input_schema(self) -> dict[str, Any]:
        return _PUBLISH_INPUT_SCHEMA

    @property
    def required_scopes(self) -> list[str]:
        return ['marketplace:publish']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.app.marketplace.service.marketplace_template_service import marketplace_template_service

        content = _decode_package(arguments)
        submit_review = bool(arguments.get('submit_review'))
        async with async_db_session() as db:
            template = await marketplace_template_service.upload_user_template(
                db=db,
                user_id=agent_context.owner_id,
                hasn_id=agent_context.owner_hasn_id,
                content=content,
                filename=(arguments.get('filename') or None),
                slug=(arguments.get('slug') or None),
                changelog=(arguments.get('changelog') or None),
            )
            status = template.status
            if submit_review:
                reviewed = await marketplace_template_service.submit_review(
                    db=db, resource_id=template.template_id, user_id=agent_context.owner_id
                )
                await db.commit()
                status = reviewed.status
        return {
            'template_id': template.template_id,
            'namespace': template.namespace,
            'slug': template.slug,
            'status': status,
            'submitted_review': submit_review,
        }


_PUBLISH_SKILL_PACK_INPUT_SCHEMA: dict[str, Any] = {
    'type': 'object',
    'properties': {
        'name': {'type': 'string', 'description': '技能包名称'},
        'bundle_slug': {'type': 'string', 'description': 'skill pack slug（小写连字符）'},
        'command_key': {'type': 'string', 'description': "Hermes 命令 key，必须是 '/' + bundle_slug"},
        'namespace': {'type': 'string', 'description': '命名空间（如 huanxing），可选'},
        'description': {'type': 'string', 'description': '技能包描述，可选'},
        'version': {'type': 'string', 'description': '语义化版本，默认 1.0.0'},
        'hermes_yaml': {
            'type': 'string',
            'description': 'Hermes 技能包 YAML（顶层 dict，含非空 skills 成员列表 + 可选 instruction）',
        },
        'hermes_bundle_json': {'type': 'object', 'description': 'Hermes bundle JSON（可选，结构化镜像）'},
        'is_private': {'type': 'boolean', 'description': '是否私有，默认 true'},
    },
    'required': ['name', 'bundle_slug', 'command_key', 'hermes_yaml'],
    'additionalProperties': False,
}


class PublishSkillPackTool(BaseTool):
    """发布技能包（skill_pack）为**当前用户**的市场资源（marketplace_template）。

    与 publish_template（zip 语义）不同：入参是结构化 hermes_yaml，落库前经
    skill_pack_service 校验+规范化（B2.2/B2.4），路由与本工具共用同一 upsert（DRY）。
    """

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def namespace(self) -> str:
        return 'hasn.marketplace'

    @property
    def name(self) -> str:
        return 'hasn.marketplace.publish_skill_pack'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def risk_level(self) -> str:
        return 'high'

    @property
    def description(self) -> str:
        return '把结构化技能包（hermes_yaml + 成员技能）发布为你（主人）的市场资源；落库前校验规范化'

    @property
    def input_schema(self) -> dict[str, Any]:
        return _PUBLISH_SKILL_PACK_INPUT_SCHEMA

    @property
    def required_scopes(self) -> list[str]:
        return ['marketplace:publish']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.app.marketplace.schema.skill_pack import SkillPackCreateRequest
        from backend.app.marketplace.service import skill_pack_service

        payload = SkillPackCreateRequest(
            name=arguments['name'],
            bundle_slug=arguments['bundle_slug'],
            command_key=arguments['command_key'],
            namespace=(arguments.get('namespace') or None),
            description=(arguments.get('description') or None),
            version=(arguments.get('version') or '1.0.0'),
            hermes_yaml=arguments['hermes_yaml'],
            hermes_bundle_json=(arguments.get('hermes_bundle_json') or {}),
            is_private=bool(arguments.get('is_private', True)),
            is_official=False,
        )
        async with async_db_session() as db:
            # 归属由后端从凭证解析（owner_id），不信入参身份。
            snapshot = await skill_pack_service.upsert_skill_pack(db, payload, author_id=agent_context.owner_id)
            await db.commit()
        return {
            'template_id': snapshot['template_id'],
            'version': snapshot['version'],
            'bundle_slug': snapshot['bundle_slug'],
            'command_key': snapshot['command_key'],
            'skill_ids': snapshot['skill_ids'],
            'content_hash': snapshot['content_hash'],
        }


# 注册清单（server._register_builtin_tools 消费）——3 组 10 个云端工具（package_* 在 hasn-mcp 本地）。
MARKETPLACE_TOOLS: list[type[BaseTool]] = [
    SearchSkillsTool,
    GetSkillTool,
    SearchTemplatesTool,
    GetTemplateTool,
    ListInstalledTool,
    InstallSkillTool,
    UninstallSkillTool,
    PublishSkillTool,
    PublishTemplateTool,
    PublishSkillPackTool,
]
