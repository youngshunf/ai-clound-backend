"""GitHub sync service for marketplace templates."""

from __future__ import annotations

import os

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from git import Repo

from backend.app.hasn_task.service.workflow_template_service import workflow_template_service
from backend.app.marketplace.crud.crud_marketplace_sync_log import marketplace_sync_log_dao
from backend.app.marketplace.crud.crud_marketplace_template import marketplace_template_dao
from backend.app.marketplace.crud.crud_marketplace_template_version import marketplace_template_version_dao
from backend.app.marketplace.schema.marketplace_sync_log import (
    CreateMarketplaceSyncLogParam,
    UpdateMarketplaceSyncLogParam,
)
from backend.app.marketplace.schema.marketplace_template import (
    CreateMarketplaceTemplateParam,
    UpdateMarketplaceTemplateParam,
)
from backend.app.marketplace.schema.marketplace_template_version import (
    CreateMarketplaceTemplateVersionParam,
    UpdateMarketplaceTemplateVersionParam,
)
from backend.app.marketplace.service.app_package_service import app_package_service
from backend.app.marketplace.service.package_validation import normalize_tags
from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.common.log import log
from backend.core.conf import settings
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class GitHubAppSyncService:
    """Sync huanxing-hub templates into marketplace_template."""

    def __init__(self) -> None:
        self.repo_url = getattr(settings, 'HUANXING_HUB_REPO_URL', 'https://github.com/youngshunf/huanxing-hub.git')
        self.local_path = getattr(settings, 'HUANXING_HUB_LOCAL_PATH', '/tmp/huanxing-hub')
        self.repo: Repo | None = None

    async def sync_from_github(self, db: AsyncSession, force: bool = False) -> dict[str, Any]:  # noqa: FBT001, FBT002
        sync_log_id = None
        try:
            sync_log = await marketplace_sync_log_dao.create(
                db,
                CreateMarketplaceSyncLogParam(
                    sync_type='github',
                    status='in_progress',
                    started_at=timezone.now(),
                ),
            )
            await db.flush()
            sync_log_id = sync_log.id

            old_commit = await self._update_repository()
            new_commit = self.repo.head.commit.hexsha if self.repo else None
            templates_data = await self._scan_templates()

            synced_count = 0
            failed_count = 0
            errors = []
            for template_data in templates_data:
                try:
                    await self._sync_template(db, template_data)
                    synced_count += 1
                except Exception as exc:  # noqa: PERF203
                    failed_count += 1
                    errors.append(f'{template_data.get("template_id", "unknown")}: {exc}')
                    log.error(f'Failed to sync template {template_data.get("template_id")}: {exc}')

            # 顺带下发 hub 内置工作流模板（workflow-templates/ → hasn_task.workflow_template）。
            # 复用本 service 已 checkout 的同一个 hub 仓根（workflow-templates/ 与 templates/ 同仓）+ 已开事务；
            # 扫描/解析/upsert 归 hasn_task 域自持（本处只提供 repo_root 与触发，不耦合工作流模板 schema）。
            # 该下发失败不应拖垮 marketplace 模板同步 → 记 error 不抛（单模板可恢复失败已在下发内部记 warning 跳过）。
            try:
                wf_seed = await workflow_template_service.sync_builtin_workflow_templates(
                    db, repo_root=self.local_path
                )
                log.info(f'内置工作流模板下发完成: {wf_seed}')
            except Exception as wf_exc:
                log.error(f'内置工作流模板下发失败（不影响 marketplace 模板同步）: {wf_exc}')

            await marketplace_sync_log_dao.update(
                db,
                sync_log_id,
                UpdateMarketplaceSyncLogParam(
                    status='success' if failed_count == 0 else 'partial',
                    items_synced=synced_count,
                    items_failed=failed_count,
                    error_message='\n'.join(errors) if errors else None,
                    git_commit_before=old_commit,
                    git_commit_after=new_commit,
                    completed_at=timezone.now(),
                ),
            )

            return {'success': True, 'synced': synced_count, 'failed': failed_count, 'errors': errors}  # noqa: TRY300
        except Exception as exc:
            log.error(f'GitHub template sync failed: {exc}')
            if sync_log_id:
                await marketplace_sync_log_dao.update(
                    db,
                    sync_log_id,
                    UpdateMarketplaceSyncLogParam(
                        status='failed',
                        error_message=str(exc),
                        completed_at=timezone.now(),
                    ),
                )
            return {'success': False, 'error': str(exc)}

    async def _update_repository(self) -> str | None:
        old_commit = None
        if os.path.exists(self.local_path):  # noqa: ASYNC240
            self.repo = Repo(self.local_path)
            old_commit = self.repo.head.commit.hexsha
            self.repo.remotes.origin.pull()
        else:
            self.repo = Repo.clone_from(self.repo_url, self.local_path)
        return old_commit

    async def _scan_templates(self) -> list[dict[str, Any]]:
        templates = []
        templates_root = Path(self.local_path) / 'templates'
        if not templates_root.exists():
            log.warning(f'Templates directory not found: {templates_root}')
            return templates

        for template_yaml in templates_root.glob('*/*/template.yaml'):
            try:
                templates.append(await self._parse_template_yaml(template_yaml))
            except Exception as exc:  # noqa: PERF203
                log.error(f'Failed to parse {template_yaml}: {exc}')
        return templates

    async def _parse_template_yaml(self, template_path: Path) -> dict[str, Any]:
        relative = template_path.parent.relative_to(Path(self.local_path))
        parts = relative.parts
        if len(parts) != 3 or parts[0] != 'templates':
            raise ValueError(f'Unexpected template path: {relative}')

        _, category, slug = parts
        data = yaml.safe_load(template_path.read_text(encoding='utf-8')) or {}  # noqa: ASYNC240
        if not isinstance(data, dict):
            raise TypeError(f'template.yaml must be a mapping: {template_path}')

        # 模板卡片标题取 YAML `name`（领域专家头衔，如「金融专家」）。历史上这里优先取
        # `display_name`（人名「明远」），把领域名吃掉了——分身命名体系重构后掰正：name=专家头衔。
        template_name = data.get('name') or data.get('display_name') or slug
        # 候选人名池（仅 Agent 模板有）：YAML name_pool 数组 → 逗号拼接入库；缺省回退 display_name。
        raw_pool = data.get('name_pool')
        if isinstance(raw_pool, list):
            name_pool_items = [str(item).strip() for item in raw_pool if str(item).strip()]
        else:
            name_pool_items = []
        if not name_pool_items and data.get('display_name'):
            name_pool_items = [str(data['display_name']).strip()]
        name_pool_csv = ','.join(name_pool_items) or None
        description = data.get('description') or ''
        version = str(data.get('version') or '1.0.0')
        namespace = f'huanxing/{category}'
        template_id = f'{namespace}/{slug}'
        skill_deps = data.get('skills') or data.get('skill_dependencies') or []
        sop_deps = data.get('sops') or data.get('sop_dependencies') or []
        tags = normalize_tags(data.get('tags'))
        template_dir = template_path.parent
        icon_path = self._find_local_icon(template_dir)
        # Agent Profile 内容（云端权威化）：把模板目录里的三份文档抽取入库，
        # 创建 Agent 时云端据此物化进 hasn_agents.{soul_md,agents_md,user_md}。
        soul_md = self._read_optional_text(template_dir / 'SOUL.md')
        # HASN 公民行为机制：全模板共享一份权威源 templates/_platform/HASN.md，
        # sync 时 prepend 到每个模板的 soul_md。它落进 hermes always-loaded 的 stable
        # 身份槽（SOUL.md），让任意分身（本地/云端）都知道自己运行在 HASN 网络、如何用
        # 渐进式 MCP 行动（看联系人/发消息/建任务/卡住装技能）、以及对主人透明等边界。
        # 占位符（{{owner_nickname}} 等）在 register_hasn_agent 渲染。模板 SOUL.md 保持纯
        # 人格、不重复机制——更新机制只改这一份文件并重新 sync。
        hasn_block = self._read_optional_text(
            Path(self.local_path) / 'templates' / '_platform' / 'HASN.md'
        )
        soul_md = self._compose_soul_md(hasn_block, soul_md)
        # AGENTS.md 已退役（2026-07-12）：它本是「工作目录/项目规范」文件，被误当 persona 用；
        # 上游 hermes 从 profile 根读不到 agent cwd 下的 AGENTS.md（落点错位 + 仅顶层不递归），
        # runtime 自始至终从不消费它。故不再从模板读取，源头即恒 None（下游 str|None 自然承接）；
        # 人格统一收进 SOUL.md。hasn_agents.agents_md / marketplace_template.agents_md 列保留为惰性死列。
        agents_md = None
        # USER.md 是 owner 维度（描述主人，与 agent persona 无关），全模板共用一份权威源
        # templates/USER.md；若某模板自带 USER.md 则按模板覆盖（当前约定无 per-template）。
        user_md = self._read_optional_text(template_dir / 'USER.md') or self._read_optional_text(
            Path(self.local_path) / 'templates' / 'USER.md'
        )
        # MEMORY.md 是 agent 维度（Agent 长期/自我演化记忆种子，§ 记录格式），同样全模板共用
        # templates/MEMORY.md；per-template 自带则覆盖。provision 首次缺省时种子、已有非空不覆盖。
        memory_md = self._read_optional_text(template_dir / 'MEMORY.md') or self._read_optional_text(
            Path(self.local_path) / 'templates' / 'MEMORY.md'
        )

        return {
            'template_id': template_id,
            'namespace': namespace,
            'slug': slug,
            'template_type': data.get('template_type') or 'agent_template',
            'name': str(template_name),
            'name_en': str(template_name),
            'name_zh': str(template_name),
            'name_pool': name_pool_csv,
            'description': str(description),
            'description_en': str(description),
            'description_zh': str(description),
            'source_language': 'zh' if any(ord(c) > 127 for c in str(template_name)) else 'en',
            'icon_url': data.get('icon_url') or data.get('icon_s3_url') or data.get('icon_cdn_url'),
            'icon_path': icon_path,
            'emoji': data.get('emoji'),
            'author_name': data.get('author') or 'huanxing',
            'pricing_type': (data.get('pricing') or {}).get('tier', data.get('pricing_type', 'free')),
            'price': (data.get('pricing') or {}).get('price', data.get('price', 0)),
            'is_private': False,
            'is_official': True,
            # 内置 agent 标志（内置定时任务体系 §4.2）：builtin=true 注册时自动创建；
            # builtin_key 是内置任务 target_agent_type 的匹配纽带。仅 builtin=true 时取 builtin_key。
            'builtin': bool(data.get('builtin', False)),
            'builtin_key': (str(data['builtin_key']).strip() if data.get('builtin') and data.get('builtin_key') else None),
            'download_count': 0,
            'category': data.get('category') or category,
            'tags': ','.join(tags),
            'source_type': 'official',
            'source_repo_path': f'templates/{category}/{slug}',
            'skill_dependencies': ','.join(skill_deps) if isinstance(skill_deps, list) else str(skill_deps),
            'sop_dependencies': ','.join(sop_deps) if isinstance(sop_deps, list) else str(sop_deps),
            'soul_md': soul_md,
            'agents_md': agents_md,
            'user_md': user_md,
            'memory_md': memory_md,
            'repo_path': f'templates/{category}/{slug}',
            'git_commit_hash': self.repo.head.commit.hexsha if self.repo else None,
            'synced_at': timezone.now(),
            'translated_at': timezone.now(),
            'status': 'published',
            'visibility': 'public',
            'version': version,
            'skill_dependencies_versioned': dict.fromkeys(skill_deps, '*') if isinstance(skill_deps, list) else None,
        }

    @staticmethod
    def _find_local_icon(template_dir: Path) -> Path | None:
        for icon_name in ('icon.svg', 'icon.png', 'icon.jpg', 'icon.jpeg'):
            icon_path = template_dir / icon_name
            if icon_path.exists():
                return icon_path
        return None

    @staticmethod
    def _read_optional_text(path: Path) -> str | None:
        """读取模板目录下的可选文本文件（SOUL/AGENTS/USER.md），缺失返回 None。"""
        if not path.exists():
            return None
        return path.read_text(encoding='utf-8')

    @staticmethod
    def _compose_soul_md(hasn_block: str | None, persona_soul: str | None) -> str | None:
        """把共享 HASN 公民块 prepend 到模板人格 SOUL.md。

        - 共享块缺失 → 原样返回人格（零侵入、不阻断 sync）。
        - 人格缺失 → 仅返回共享块（至少保证分身知道自己在 HASN 网络）。
        - 两者都在 → `HASN 块\n\n人格`，块尾已自带 `---` 分隔。
        幂等：每次从 hub 文件重新读取纯人格 SOUL.md，绝不把合成结果写回 hub，故无双重前缀。
        """
        block = (hasn_block or '').strip()
        persona = (persona_soul or '').strip()
        if not block:
            return persona_soul
        if not persona:
            return f'{block}\n'
        return f'{block}\n\n{persona}\n'

    async def _sync_template(self, db: AsyncSession, template_data: dict[str, Any]) -> None:
        template_id = template_data['template_id']
        version = template_data.pop('version')
        icon_path = template_data.pop('icon_path', None)
        skill_dependencies_versioned = template_data.pop('skill_dependencies_versioned', None)

        existing_template = await marketplace_template_dao.get_by_id(db, template_id)

        # 图标上传非致命：S3/CDN 写入抖动或凭据问题不应阻断整个模板同步——
        # persona(soul_md)/技能/定价等内容字段远比图标重要。失败则记告警、保留 DB 中
        # 已有 icon_url（首次同步无则置 None），继续把内容字段写库。
        if icon_path and not template_data.get('icon_url'):
            try:
                template_data['icon_url'] = await marketplace_storage_service.upload_icon(
                    db=db,
                    item_type='template',
                    item_id=template_id,
                    content=Path(icon_path).read_bytes(),  # noqa: ASYNC240
                    filename=Path(icon_path).name,
                )
            except Exception as exc:
                log.warning('icon upload failed for template %s, keeping existing icon: %s', template_id, exc)
                template_data['icon_url'] = existing_template.icon_url if existing_template else None

        if existing_template:
            await marketplace_template_dao.update(
                db,
                existing_template.id,
                UpdateMarketplaceTemplateParam(**template_data),
            )
        else:
            await marketplace_template_dao.create(db, CreateMarketplaceTemplateParam(**template_data))

        package_info = await app_package_service.build_template_package(template_id, version)
        existing_version = await marketplace_template_version_dao.get_by_template_and_version(db, template_id, version)
        version_data = {
            'template_id': template_id,
            'version': version,
            'changelog': f'Version {version}',
            'skill_dependencies_versioned': skill_dependencies_versioned,
            'package_url': package_info['package_path'],
            'file_hash': package_info['file_hash'],
            'file_size': package_info['file_size'],
            'is_latest': True,
            'published_at': timezone.now(),
        }
        if existing_version:
            await marketplace_template_version_dao.update(
                db,
                existing_version.id,
                UpdateMarketplaceTemplateVersionParam(**version_data),
            )
        else:
            await marketplace_template_version_dao.mark_all_not_latest(db, template_id)
            await marketplace_template_version_dao.create(db, CreateMarketplaceTemplateVersionParam(**version_data))


github_app_sync_service = GitHubAppSyncService()
