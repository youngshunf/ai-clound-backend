from __future__ import annotations

import hashlib

from typing import TYPE_CHECKING, Any

from backend.app.hasn.crud.crud_hasn_ai_native_app_manifest import hasn_ai_native_app_manifest_dao
from backend.app.hasn.model import HasnAiNativeAppManifest
from backend.app.hasn.schema.resource_descriptor import ResourceDescriptor, ResourceDomainInfo, ResourceRoute
from backend.app.hasn.service.ai_native_knowledge_manifest import KNOWLEDGE_AI_NATIVE_MANIFEST
from backend.app.hasn.service.app_catalog_registry import AppCatalogRegistry, app_catalog_registry
from backend.app.hasn.service.authz.resource_registry import resource_kind_registry
from backend.app.hasn_community.service.ai_native_manifest import COMMUNITY_AI_NATIVE_MANIFEST
from backend.app.hasn_computer_use.manifest import COMPUTER_USE_AI_NATIVE_MANIFEST

# G6 资源类型适配器注册（doc33 S2-6）：import 即把各应用 adapter 注册进平台注册表。必须在
# manifest 校验（validate_manifest 查 registered_types）之前完成，否则新增 resource_access 声明会
# 被误判「type 未注册」。各应用自注册模块见 `<app>/service/resource_adapter.py`。
from backend.app.hasn_copilot.service import resource_adapter as _copilot_resource_adapter  # noqa: F401
from backend.app.hasn_creator.manifest import CREATOR_AI_NATIVE_MANIFEST
from backend.app.hasn_deck.manifest import DECK_AI_NATIVE_MANIFEST
from backend.app.hasn_deck.service import resource_adapter as _deck_resource_adapter  # noqa: F401
from backend.app.hasn_design.manifest import DESIGN_AI_NATIVE_MANIFEST
from backend.app.hasn_design.service import resource_adapter as _design_resource_adapter  # noqa: F401
from backend.app.hasn_designsystem.manifest import DESIGNSYSTEM_AI_NATIVE_MANIFEST
from backend.app.hasn_designsystem.service import resource_adapter as _designsystem_resource_adapter  # noqa: F401
from backend.app.hasn_film.manifest import FILM_AI_NATIVE_MANIFEST
from backend.app.hasn_finance.manifest import FINANCE_AI_NATIVE_MANIFEST
from backend.app.hasn_finance.service import project_linkage as _finance_project_linkage  # noqa: F401
from backend.app.hasn_growth.manifest import GROWTH_AI_NATIVE_MANIFEST
from backend.app.hasn_imagelab.manifest import IMAGELAB_AI_NATIVE_MANIFEST
from backend.app.hasn_knowledge.service import resource_adapter as _knowledge_resource_adapter  # noqa: F401
from backend.app.hasn_plan.manifest import PLAN_AI_NATIVE_MANIFEST
from backend.app.hasn_project.manifest import PROJECT_AI_NATIVE_MANIFEST
from backend.app.hasn_plan.service import resource_adapter as _plan_resource_adapter  # noqa: F401
from backend.app.hasn_publish.manifest import PUBLISH_AI_NATIVE_MANIFEST
from backend.app.hasn_quant.manifest import QUANT_AI_NATIVE_MANIFEST
from backend.app.hasn_reel.manifest import REEL_AI_NATIVE_MANIFEST
from backend.app.hasn_studio.manifest import STUDIO_AI_NATIVE_MANIFEST
from backend.app.hasn_studio.service import resource_adapter as _studio_resource_adapter  # noqa: F401
from backend.app.hasn_task.service.ai_native_manifest import HASN_TASK_AI_NATIVE_MANIFEST
from backend.common.exception import errors
from backend.common.pagination import paging_data
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ManifestValidationResult:
    def __init__(self, *, valid: bool, errors: list[str], manifest_hash: str = '') -> None:
        self.valid = valid
        self.errors = errors
        self.manifest_hash = manifest_hash


class AINativeAppRegistry:
    def __init__(self, *, catalog_registry: AppCatalogRegistry | None = None) -> None:
        self.catalog_registry = catalog_registry or app_catalog_registry
        self._builtin_manifests: dict[str, dict[str, Any]] = {
            'knowledge': KNOWLEDGE_AI_NATIVE_MANIFEST,
            'community': COMMUNITY_AI_NATIVE_MANIFEST,
            'deck': DECK_AI_NATIVE_MANIFEST,
            'hasn_task': HASN_TASK_AI_NATIVE_MANIFEST,
            'publish': PUBLISH_AI_NATIVE_MANIFEST,
            # 获客（app_id=growth，模块/schema hasn_growth）。键用 app_id（de-prefixed，community 先例）。
            'growth': GROWTH_AI_NATIVE_MANIFEST,
            # 创作运营（app_id=creator，模块/schema hasn_creator）。键用 app_id（de-prefixed）。
            'creator': CREATOR_AI_NATIVE_MANIFEST,
            # 自研设计系统生成应用（app_id=designsystem，模块 14/20；local_tool，DS-P7 铸 scope）。
            'designsystem': DESIGNSYSTEM_AI_NATIVE_MANIFEST,
            # 视频生成应用（app_id=film，源自 VideoClaw，模块 14/实施 10；local_tool，VC-P4 铸 scope）。
            'film': FILM_AI_NATIVE_MANIFEST,
            # 短视频合成应用（app_id=reel，源自 MoneyPrinterTurbo，模块 14 doc19；local_tool，reel-P4 铸 scope）。
            'reel': REEL_AI_NATIVE_MANIFEST,
            # 图像处理应用（app_id=imagelab，图坊，自研本地引擎，模块 14 doc30；local_tool，P3 铸 scope。
            # hasn.imagelab.* 工具在本地 hasn-mcp（imagelab.rs，P3 待落）经 ImageLabBroker → 自研引擎处理图片，
            # 云端 tools[]/capabilities[] 为发现/权限控制面记录，方案 A 工具不进 tools[]）。
            'imagelab': IMAGELAB_AI_NATIVE_MANIFEST,
            # 规划与目标管理应用（app_id=plan，模块/schema hasn_plan，模块 19；local_tool，PLAN-P1 铸 scope）。
            'plan': PLAN_AI_NATIVE_MANIFEST,
            # 项目管理应用（app_id=project，模块/schema hasn_project，模块 14 doc38；cloud 承载，PJ U3 铸 scope。
            # 「平台项目·联邦挂靠」——第三条轴「为了哪件事」：跨应用产物流并集读 + 里程碑 + hasn_artifacts.project_id
            # 挂靠列。工具面 hasn.project.* 走云端 MCP platform tool（mcp/tools/project.py），非 local_tool）。
            'project': PROJECT_AI_NATIVE_MANIFEST,
            # 金融数据（app_id=finance，模块/schema hasn_finance，模块 24；纯云端只读数据应用，
            # 工具全走 gateway_internal → finance_provider → finance-data-service，唯一接触 akshare 处隔离独立服务）。
            'finance': FINANCE_AI_NATIVE_MANIFEST,
            # 量化交易（app_id=quant，模块/schema hasn_quant，模块 14 doc23；cloud-brokered 业务应用，
            # 5 个回测线工具走 gateway_internal → quant_service → quant_engine_provider → quant-engine-service，
            # 唯一接触 NautilusTrader 处隔离独立服务。实盘线 P6+ 受 P0-闸1 硬闸，本期不在 manifest 暴露）。
            'quant': QUANT_AI_NATIVE_MANIFEST,
            # 统一视频引擎（app_id=studio，模块/schema hasn_studio，模块 14 doc22；cloud-brokered 业务应用，
            # 工具面随 P3 落地经 gateway_internal → studio_service → montage_engine_provider → montage-engine-service。
            # 本期 P2 manifest 骨架不暴露 tools/capabilities，待 P3 接 service + 云端 handler 后再补）。
            'studio': STUDIO_AI_NATIVE_MANIFEST,
            # 矢量设计（app_id=design，源自 OpenPencil，模块 14 doc27；local_tool 本地 sidecar 应用，
            # hasn.design.* 工具在本地 hasn-mcp（design.rs，OP-P3-A 待落）经 DesignBroker → pen-mcp 出图，
            # 云端 tools[]/capabilities[] 为发现/权限控制面记录，方案 A 工具不进 tools[]）。
            'design': DESIGN_AI_NATIVE_MANIFEST,
            # 桌面控制（app_id=computer_use，模块 23 V2；local_tool 能力型应用，CU-P4 铸三 scope。
            # hasn.computer.* 工具在本地 hasn-mcp（computer/tools.rs，CU-P2a 已落）经 daemon 托管 cua-driver 落
            # 真实桌面，云端 tools[]/capabilities[] 为发现/权限控制面记录，方案 A 工具不进 tools[]）。
            'computer_use': COMPUTER_USE_AI_NATIVE_MANIFEST,
        }

    def list_builtin_apps(self) -> list[dict[str, Any]]:
        return list(self._builtin_manifests.values())

    def get_builtin_manifest(self, app_id: str) -> dict[str, Any]:
        try:
            return self._builtin_manifests[app_id]
        except KeyError as exc:
            raise errors.NotFoundError(msg='AI-Native 应用不存在') from exc

    def validate_manifest(self, manifest: dict[str, Any]) -> ManifestValidationResult:
        errors_list: list[str] = []
        app_id = str(manifest.get('app_id') or '')
        version = str(manifest.get('version') or '')
        workspace_scope = list(manifest.get('workspace_scope') or [])
        collaboration_mode = str(manifest.get('collaboration_mode') or 'none')
        project_aware = manifest.get('project_aware', False)
        project_required = manifest.get('project_required', False)

        if not app_id:
            errors_list.append('app_id_required')
        if not version:
            errors_list.append('version_required')
        if not isinstance(project_aware, bool):
            errors_list.append('project_aware_must_be_boolean')
        if not isinstance(project_required, bool):
            errors_list.append('project_required_must_be_boolean')
        if project_required is True and project_aware is not True:
            errors_list.append('project_required_requires_project_aware')

        try:
            registered_app = self.catalog_registry.get(app_id)
        except KeyError:
            errors_list.append('workbench_app_not_found')
            registered_app = None

        if registered_app is not None:
            if any(scope not in registered_app.scope for scope in workspace_scope):
                errors_list.append('workspace_scope_exceeds_workbench_scope')
            if collaboration_mode != registered_app.collaboration_mode:
                errors_list.append('collaboration_mode_mismatch')
            if isinstance(project_aware, bool) and project_aware != registered_app.project_aware:
                errors_list.append('project_aware_mismatch')
            if isinstance(project_required, bool) and project_required != registered_app.project_required:
                errors_list.append('project_required_mismatch')

        # 资源描述符校验（doc31 §2.1，RC-P0）：manifest.resources[] 若声明，逐项按 ResourceDescriptor
        # 校验（uri_domain 非空/无 scheme、open.mode 三枚举、route_template 含 :id、card.verb/action_label…）。
        resources = manifest.get('resources')
        if resources is not None:
            if not isinstance(resources, list):
                errors_list.append('resources_must_be_list')
            else:
                errors_list.extend(
                    err for idx, resource in enumerate(resources) if (err := _resource_descriptor_error(idx, resource))
                )

        # G6 资源权限门声明校验（doc33 S2-5·doc32 §12.2 随门即校验）：逐工具校验 resource_access 形状，
        # 声明写错（type 未注册 / need 非法 / param 不在 input_schema）在注册期炸，不静默直通致运行时漏判权。
        # 循环挪进 helper，避免撑高 validate_manifest 圈复杂度（C901）。
        errors_list.extend(_manifest_resource_access_errors(manifest))

        manifest_hash = _manifest_hash(manifest)
        return ManifestValidationResult(valid=not errors_list, errors=errors_list, manifest_hash=manifest_hash)

    async def publish_builtin(self, db: AsyncSession | None, app_id: str) -> dict[str, Any]:
        manifest = self.get_builtin_manifest(app_id)
        validation = self.validate_manifest(manifest)
        if not validation.valid:
            raise errors.RequestError(msg='manifest_validation_failed', data={'errors': validation.errors})
        row = HasnAiNativeAppManifest(
            app_id=manifest['app_id'],
            version=manifest['version'],
            status='published',
            workspace_scope=list(manifest.get('workspace_scope') or []),
            collaboration_mode=str(manifest.get('collaboration_mode') or 'none'),
            manifest_json=manifest,
            manifest_hash=validation.manifest_hash,
            published_at=timezone.now(),
        )
        if db is not None:
            db.add(row)
            await db.flush()
            return _manifest_payload(row)
        return _builtin_manifest_payload(manifest, manifest_hash=validation.manifest_hash)

    async def ensure_builtin_published(self, db: AsyncSession, app_id: str) -> dict[str, Any]:
        published = await self.get_published_manifest(db, app_id=app_id)
        if published:
            # builtin app 以代码 manifest 为权威源：hash 变化即就地刷新已发布行，
            # 自愈陈旧 manifest（如新增工具后旧发布行仍是旧工具集）。
            if app_id in self._builtin_manifests:
                current_hash = _manifest_hash(self.get_builtin_manifest(app_id))
                if published.get('manifest_hash') != current_hash:
                    return await self._refresh_builtin_published(db, app_id=app_id)
            return published
        return await self.publish_builtin(db, app_id)

    async def _refresh_builtin_published(self, db: AsyncSession, *, app_id: str) -> dict[str, Any]:
        """builtin manifest hash 变化时，就地更新已发布行（单行、不新增 status、无唯一约束风险）。"""
        manifest = self.get_builtin_manifest(app_id)
        validation = self.validate_manifest(manifest)
        if not validation.valid:
            raise errors.RequestError(msg='manifest_validation_failed', data={'errors': validation.errors})
        stmt = await hasn_ai_native_app_manifest_dao.get_select()
        stmt = stmt.where(
            HasnAiNativeAppManifest.app_id == app_id,
            HasnAiNativeAppManifest.status == 'published',
        )
        row = (await db.execute(stmt)).scalars().first()
        if row is None:
            return await self.publish_builtin(db, app_id)
        row.version = manifest['version']
        row.workspace_scope = list(manifest.get('workspace_scope') or [])
        row.collaboration_mode = str(manifest.get('collaboration_mode') or 'none')
        row.manifest_json = manifest
        row.manifest_hash = validation.manifest_hash
        row.published_at = timezone.now()
        await db.flush()
        return _manifest_payload(row)

    async def get_published_manifest(self, db: AsyncSession, *, app_id: str) -> dict[str, Any] | None:
        stmt = await hasn_ai_native_app_manifest_dao.get_select()
        stmt = stmt.where(
            HasnAiNativeAppManifest.app_id == app_id,
            HasnAiNativeAppManifest.status == 'published',
        )
        row = (await db.execute(stmt)).scalars().first()
        if row is None:
            return None
        return _manifest_payload(row)

    async def list_manifests(self, db: AsyncSession) -> dict[str, Any]:
        return await paging_data(db, await hasn_ai_native_app_manifest_dao.get_select())

    async def list_published_manifests(self, db: AsyncSession) -> list[dict[str, Any]]:
        stmt = await hasn_ai_native_app_manifest_dao.get_select()
        stmt = stmt.where(HasnAiNativeAppManifest.status == 'published')
        rows = (await db.execute(stmt)).scalars().all()
        manifests = {row.app_id: _manifest_payload(row) for row in rows}
        # builtin app 以代码 manifest 为权威源：DB 行缺失或 hash 陈旧时，
        # 返回当前代码 manifest（只读不写；DB 行的持久化自愈交给 ensure_builtin_published）。
        for app_id, manifest in self._builtin_manifests.items():
            current_hash = _manifest_hash(manifest)
            existing = manifests.get(app_id)
            if existing is None or existing.get('manifest_hash') != current_hash:
                manifests[app_id] = _builtin_manifest_payload(manifest, manifest_hash=current_hash)
        return list(manifests.values())

    async def get(self, db: AsyncSession, app_id: str) -> dict[str, Any]:
        published = await self.get_published_manifest(db, app_id=app_id)
        if published:
            return published
        if app_id in self._builtin_manifests:
            return _builtin_manifest_payload(self._builtin_manifests[app_id])
        raise errors.NotFoundError(msg='AI-Native 应用不存在')

    async def get_emit_declaration(self, db: AsyncSession, app_id: str) -> dict[str, Any] | None:
        """读 App 已发布 manifest 的 `notifications.emit` 声明（统一通知设计 §7 / P5）。

        返回 {categories: [...], card_message: bool, display_name: str} 或 None
        （App 不存在 / 未发布 / 未声明 notifications.emit → None，即无权发通知）。
        builtin app 走 ensure_builtin_published 自愈，第三方 app 走已发布行。
        """
        manifest: dict[str, Any] | None = None
        if app_id in self._builtin_manifests:
            manifest = await self.ensure_builtin_published(db, app_id)
        else:
            manifest = await self.get_published_manifest(db, app_id=app_id)
        if not manifest:
            return None
        emit = ((manifest.get('manifest_json') or {}).get('notifications') or {}).get('emit')
        return emit if isinstance(emit, dict) else None

    def resource_descriptor(self, app_id: str, resource_kind: str | None = None) -> ResourceDescriptor | None:
        """查某应用某类资源的 descriptor（doc31 §2，RC-P0）。

        当前全部 AI-Native 应用均为 builtin，descriptor 权威在代码 manifest 的 `resources[]`，
        故同步读 `_builtin_manifests`（无 db）。`resource_kind` 缺省取该 app 首个资源
        （多数应用一类主资源；deck 唯一 deck.presentation）。声明缺失/非法 → None。
        """
        manifest = self._builtin_manifests.get(app_id)
        if not manifest:
            return None
        resources = manifest.get('resources')
        if not isinstance(resources, list) or not resources:
            return None
        chosen: dict[str, Any] | None = None
        if resource_kind:
            chosen = next((r for r in resources if r.get('resource_kind') == resource_kind), None)
        if chosen is None:
            chosen = resources[0]
        try:
            return ResourceDescriptor.model_validate(chosen)
        except Exception:
            return None

    def resolve_resource_descriptor(self, app_id: str, local_ref: str) -> tuple[ResourceDescriptor | None, str | None]:
        """据 origin_ref 的 local_ref 段选中 descriptor 并算出 uri_id（doc31-A·多资源 opt-in）。

        - **单资源模式**（app 的 resources[] 均未声明 `ref_type`，如 deck/reel/design/knowledge）：
          用首个 descriptor，整段 `local_ref` 即云端资源 id（历史行为，design 的 `proj:v2` 不受影响）。
        - **多资源模式**（app 声明了带 `ref_type` 的 descriptor，如 plan：goal/plan）：`local_ref`
          形如 `{ref_type}:{id}`，据 ref_type 段选中对应 descriptor、剥前缀取 id；子类无匹配（如 plan
          的 `onboarding` 采访会话，无资源 id）→ 返回 `(None, None)`，调用方回落通用工作会话卡、不登记应用资源。

        返回 `(descriptor, uri_id)`；无法解析 → `(None, None)`。
        """
        manifest = self._builtin_manifests.get(app_id)
        if not manifest:
            return None, None
        resources = manifest.get('resources')
        if not isinstance(resources, list) or not resources:
            return None, None
        has_ref_typed = any(isinstance(r, dict) and r.get('ref_type') for r in resources)
        if has_ref_typed:
            # 多资源模式：必须据 local_ref 的子类段精确匹配，无匹配即不出资源卡（不猜、零 fake）。
            ref_type, sep, sub_id = local_ref.partition(':')
            if not sep or not sub_id:
                return None, None
            chosen = next((r for r in resources if isinstance(r, dict) and r.get('ref_type') == ref_type), None)
            if chosen is None:
                return None, None
            try:
                return ResourceDescriptor.model_validate(chosen), sub_id
            except Exception:
                return None, None
        # 单资源模式：首个 descriptor，整段 local_ref 作 id（deck/reel/design/knowledge 现状）。
        try:
            return ResourceDescriptor.model_validate(resources[0]), local_ref
        except Exception:
            return None, None

    def known_resource_kinds(self) -> frozenset[str]:
        """全部 builtin 应用声明过的 `resource_kind` 集合（doc35 B1，`output_spec` 校验用）。

        产出要求里写 `resource_kind: knowledge.bass`（拼错）必须在**发布期**就被拒——否则闸在
        运行期永远比不中，表现成「分身干完了却过不了闸」，查起来极难。权威只有一处：各 app
        manifest 的 `resources[]` 声明，这里如实投影，不另立一张会漂移的手写白名单。
        """
        kinds: set[str] = set()
        for manifest in self._builtin_manifests.values():
            resources = manifest.get('resources')
            if not isinstance(resources, list):
                continue
            for resource in resources:
                if isinstance(resource, dict) and resource.get('resource_kind'):
                    kinds.add(str(resource['resource_kind']))
        return frozenset(kinds)

    def resource_kind_labels(self) -> dict[str, str]:
        """`resource_kind` → 人话展示名（取 `descriptor.card.verb`，如 deck.presentation → 演示文稿）。

        产出闸拒绝主人置完成时，得说「尚未交付演示文稿」而不是把 `deck.presentation` 甩到脸上——
        主人不认识内部键。展示名的权威也在 manifest（完成卡标题同源），此处如实投影。
        """
        labels: dict[str, str] = {}
        for manifest in self._builtin_manifests.values():
            resources = manifest.get('resources')
            if not isinstance(resources, list):
                continue
            for resource in resources:
                if not isinstance(resource, dict):
                    continue
                kind = resource.get('resource_kind')
                verb = (resource.get('card') or {}).get('verb') if isinstance(resource.get('card'), dict) else None
                if kind and verb:
                    labels[str(kind)] = str(verb)
        return labels

    def resource_domains(self) -> list[ResourceDomainInfo]:
        """投影全部 builtin 应用 `resources[]` 为**给分身看**的资源域目录（doc36 §5.2）。

        与 `resource_routes()` **同源不同投影**：那份给 webui 寻址（路由模板/窗口/单入口），
        这份给分身认知（有哪些资源类型、URI 什么形状、归哪个应用）。同源保证 manifest 仍是
        唯一权威——绝不为分身另手写一份会漂移的域清单。非法声明跳过（与 routes 一致）。
        """
        domains: list[ResourceDomainInfo] = []
        for app_id, manifest in self._builtin_manifests.items():
            resources = manifest.get('resources')
            if not isinstance(resources, list):
                continue
            for resource in resources:
                try:
                    descriptor = ResourceDescriptor.model_validate(resource)
                except Exception:
                    continue
                domains.append(ResourceDomainInfo.from_descriptor(app_id, descriptor))
        return domains

    def resource_routes(self) -> list[ResourceRoute]:
        """投影全部 builtin 应用 `resources[]` 为扁平资源路由读模型（doc31 §2.4，RC-P0）。

        随 catalog 同步下发 daemon/webui；webui `registerResourceRoutes` 据此把
        `hasn://{uri_domain}/{id}` 解析到内部路由/独立窗口/单入口。非法声明跳过（不阻塞下发）。
        """
        routes: list[ResourceRoute] = []
        for app_id, manifest in self._builtin_manifests.items():
            resources = manifest.get('resources')
            if not isinstance(resources, list):
                continue
            for resource in resources:
                try:
                    descriptor = ResourceDescriptor.model_validate(resource)
                except Exception:
                    continue
                routes.append(ResourceRoute.from_descriptor(app_id, descriptor))
        return routes


def _manifest_payload(row: HasnAiNativeAppManifest | dict[str, Any]) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    return {
        'id': row.id,
        'app_id': row.app_id,
        'version': row.version,
        'status': row.status,
        'workspace_scope': row.workspace_scope,
        'collaboration_mode': row.collaboration_mode,
        'manifest_json': row.manifest_json,
        'manifest_hash': row.manifest_hash,
        'published_at': row.published_at,
    }


def _builtin_manifest_payload(manifest: dict[str, Any], *, manifest_hash: str | None = None) -> dict[str, Any]:
    return {
        'id': None,
        'app_id': manifest['app_id'],
        'version': manifest['version'],
        'status': 'published',
        'workspace_scope': list(manifest.get('workspace_scope') or []),
        'collaboration_mode': str(manifest.get('collaboration_mode') or 'none'),
        'manifest_json': manifest,
        'manifest_hash': manifest_hash or _manifest_hash(manifest),
        'published_at': timezone.now(),
    }


def _manifest_hash(manifest: dict[str, Any]) -> str:
    payload = repr(manifest).encode('utf-8')
    return f'sha256:{hashlib.sha256(payload).hexdigest()}'


def _resource_descriptor_error(idx: int, resource: Any) -> str | None:
    """校验单条资源描述符，越界返回错误码字符串，合法返回 None（PERF203：try 移出循环体）。"""
    try:
        ResourceDescriptor.model_validate(resource)
    except Exception as exc:
        return f'resource_descriptor_invalid[{idx}]:{exc.__class__.__name__}'
    return None


# G6 声明合法档位（doc32 §5·doc33 S2-5）
_RESOURCE_ACCESS_NEEDS = ('viewer', 'editor', 'manager')


def _resource_access_errors(tool_idx: int, tool: Any, props: dict[str, Any]) -> list[str]:
    """校验单个工具的 `resource_access` 声明（doc33 S2-5·doc32 §12.2）——声明写错在注册期炸，不静默直通。

    元素形状 `{param, type, need, required?}`：`type` 须已注册 G6 adapter、`need ∈ {viewer,editor,manager}`、
    `param` 非空字符串**且须存在于该工具 capability 的 input_schema.properties**（防拼错参数名静默直通致漏判权）。
    `props` 由调用方从**关联 capability** 的 input_schema 解出——gateway_internal 工具的入参 schema 只挂在
    capability 上、tool 条目本身没有（doc33 S2-5「param 存在于 capability input_schema」）。
    未声明 `resource_access` 的工具零校验（返回空列表）。
    """
    if not isinstance(tool, dict):
        return []
    declarations = tool.get('resource_access')
    if declarations is None:
        return []
    tool_id = str(tool.get('tool_id') or f'#{tool_idx}')
    if not isinstance(declarations, list):
        return [f'resource_access_must_be_list[{tool_id}]']
    registered = resource_kind_registry.registered_types()
    errs: list[str] = []
    for i, decl in enumerate(declarations):
        if not isinstance(decl, dict):
            errs.append(f'resource_access_element_must_be_object[{tool_id}][{i}]')
            continue
        param = decl.get('param')
        if not param or not isinstance(param, str):
            errs.append(f'resource_access_param_required[{tool_id}][{i}]')
        elif param not in props:
            # 声明的参数名不在工具入参 schema 里 → 运行时永远取不到、静默漏判权，注册期即拦
            errs.append(f'resource_access_param_not_in_input_schema[{tool_id}][{i}]:{param}')
        if decl.get('type') not in registered:
            errs.append(f'resource_access_type_not_registered[{tool_id}][{i}]:{decl.get("type")}')
        if decl.get('need') not in _RESOURCE_ACCESS_NEEDS:
            errs.append(f'resource_access_need_invalid[{tool_id}][{i}]:{decl.get("need")}')
    return errs


def _capability_props_by_tool_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """建 `tool_id → capability.input_schema.properties` 映射（gateway_internal 工具的入参 schema 只在
    capability 上，tool 条目没有；resource_access 声明的 param 须据此校验）。"""
    result: dict[str, dict[str, Any]] = {}
    caps = manifest.get('capabilities')
    if not isinstance(caps, list):
        return result
    for cap in caps:
        if not isinstance(cap, dict):
            continue
        tool_id = cap.get('tool_id')
        if not isinstance(tool_id, str):
            continue
        result[tool_id] = ((cap.get('input_schema') or {}).get('properties')) or {}
    return result


def _manifest_resource_access_errors(manifest: dict[str, Any]) -> list[str]:
    """逐工具汇总 manifest 的 `resource_access` 声明错误（把校验循环挪出 `validate_manifest` 以控圈复杂度）。

    每个工具的入参 properties 优先取**关联 capability**（按 tool_id 匹配）的 input_schema；capability 缺失时
    回落到 tool 条目自带的 input_schema（兼容将来可能内联 schema 的工具）。
    """
    tools = manifest.get('tools')
    if not isinstance(tools, list):
        return []
    cap_props = _capability_props_by_tool_id(manifest)
    errs: list[str] = []
    for idx, tool in enumerate(tools):
        tool_id = tool.get('tool_id') if isinstance(tool, dict) else None
        props = cap_props.get(tool_id) if isinstance(tool_id, str) else None
        if props is None:
            props = ((tool.get('input_schema') or {}).get('properties')) or {} if isinstance(tool, dict) else {}
        errs.extend(_resource_access_errors(idx, tool, props))
    return errs


ai_native_app_registry: AINativeAppRegistry = AINativeAppRegistry()
