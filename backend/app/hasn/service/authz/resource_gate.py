"""G6 统一资源权限门本体（doc32 §5 / doc33 S2-2）。

工具声明「我碰哪个资源、要什么档位」，门在统一派发管线里判权，判过把已判权资源经 ContextVar
送达 handler。判权内核 `resolve_effective_permission` 语义不动——门只是把「每处手写 authorize_*
+ kb.owner_id 委托」这条纪律从「各应用自己写」变成「平台代劳一次」。

判权流程（doc32 §5.3）：
    adapter.load_meta → None 即 404（存在性隐藏，不上溯）
    → resolve_effective_permission 得自身档位
    → 行存在且声明 resolve_parent：解析父资源再判，eff = max(自身, 父)（复刻 _effective_doc_permission）
    → 维度②域限制叠加（仅 agent 主体 + owner 自有资源，经 adapter 钩子，doc32 §7.4）
    → rank(eff)==0 → 404；rank(eff)<rank(need) → 403；否则 AuthorizedResource

层次纪律（doc32 §3）：本模块属平台层，**禁止 import 任何 `app/hasn_*` 应用模块**（维度②经
adapter 钩子回调，依赖方向不反转）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.app.hasn.service.authz.resource_registry import (
    ResourceKindAdapter,
    ResourceMeta,
    resource_kind_registry,
)
from backend.app.hasn.service.resource_share_service import (
    PermissionResolutionCache,
    ResourceShareService,
    rank,
)
from backend.common.exception import errors
from backend.common.response.response_code import StandardResponseCode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn.service.authz.subject import Subject

# 合法档位（need 声明与判定档位）
_VALID_NEEDS = ('viewer', 'editor', 'manager')


@dataclass(frozen=True)
class AuthorizedResource:
    """已判权资源：门判过、handler 直接消费（owner key 来自这里，分身主人 id 永不再当资源 owner key）。"""

    meta: ResourceMeta
    permission: str  # 判定得到的有效档位 viewer|editor|manager（>= need）

    @property
    def owner_hasn_id(self) -> str:
        """资源 owner 的 hasn_id——handler 委托 owner-keyed 私有方法时用它，不用调用者身份。"""
        return self.meta.owner_hasn_id

    @property
    def resource_id(self) -> str:
        return self.meta.resource_id

    @property
    def row(self) -> Any:
        """原始 ORM 行只读快照（doc32 §4.1；勿 lazy load / update）。"""
        return self.meta.row


def _max_perm(a: str, b: str) -> str:
    """取两档位中的高者（复刻 resource_share_service._max_perm，取并语义）。"""
    return a if rank(a) >= rank(b) else b


async def _resolve(
    db: AsyncSession,
    subject: Subject,
    *,
    resource_type: str,
    meta: ResourceMeta,
    perm_cache: PermissionResolutionCache,
) -> str:
    """把 (subject, meta) 映射到判权内核入参，返回有效档位字符串。"""
    return await ResourceShareService.resolve_effective_permission(
        db,
        subject_hasn_id=subject.hasn_id,
        subject_kind=subject.kind,
        subject_owner_hasn_id=subject.owner_hasn_id,
        resource_type=resource_type,
        resource_id=meta.resource_id,
        resource_owner_hasn_id=meta.owner_hasn_id,
        resource_owner_scope=meta.owner_scope,
        resource_enterprise_id=meta.enterprise_id,
        resource_visibility=meta.visibility,
        perm_cache=perm_cache,
    )


async def _apply_domain_restriction(
    db: AsyncSession,
    adapter: ResourceKindAdapter,
    subject: Subject,
    meta: ResourceMeta,
    eff: str,
) -> str:
    """维度②「owner→分身 资源域限制」叠加（doc32 §7.4 一期承载：adapter 可选钩子）。

    仅当主体是分身、且资源是其**主人自有**（域限制只约束「主人限定分身可动我哪些资源」）时生效。
    adapter 未实现钩子视为 inherit（语义零变化）。denied→无权；restricted 且资源不在白名单→无权。
    """
    if subject.kind != 'agent' or meta.owner_hasn_id != subject.owner_hasn_id:
        return eff
    domain_hook = getattr(adapter, 'agent_domain_grant', None)
    if domain_hook is None:
        return eff
    mode, allow_ids = await domain_hook(db, subject.owner_hasn_id, subject.hasn_id)
    if mode == 'denied':
        return 'none'
    if mode == 'restricted' and str(meta.resource_id) not in {str(x) for x in allow_ids}:
        return 'none'
    # inherit / all（或其它）不改动继承来的档位
    return eff


async def _effective_with_parent(
    db: AsyncSession,
    adapter: ResourceKindAdapter,
    subject: Subject,
    *,
    resource_type: str,
    resource_id: str,
    meta: ResourceMeta,
    perm_cache: PermissionResolutionCache,
) -> str:
    """自身档位 + 父链上溯取并（doc32 §5.3 / doc33 S2-2 步骤 3）。

    自身档位：无父链、或自身也有 share 行时才值得查；纯子资源（无自身 share）自身恒 none，
    借 has_own_shares=False 省一次自身判权查询。父链：行存在才上溯，恒取并 max（对齐
    `_effective_doc_permission`「库级 ∪ 文档级取高者」）。
    """
    has_own_shares = bool(getattr(adapter, 'has_own_shares', False))
    resolve_parent = getattr(adapter, 'resolve_parent', None)

    if resolve_parent is None or has_own_shares:
        eff = await _resolve(db, subject, resource_type=resource_type, meta=meta, perm_cache=perm_cache)
    else:
        eff = 'none'

    if resolve_parent is None:
        return eff
    parent = await resolve_parent(db, resource_id)
    if parent is None:
        return eff
    parent_type, parent_id = parent
    parent_adapter = resource_kind_registry.get(parent_type)
    if parent_adapter is None:
        raise errors.ServerError(msg=f'父资源类型 {parent_type!r} 未注册 G6 adapter')
    parent_meta = await parent_adapter.load_meta(db, parent_id)
    if parent_meta is None:
        return eff
    parent_eff = await _resolve(db, subject, resource_type=parent_type, meta=parent_meta, perm_cache=perm_cache)
    return _max_perm(eff, parent_eff)


async def require(
    db: AsyncSession,
    subject: Subject,
    *,
    resource_type: str,
    resource_id: str,
    need: str,
    perm_cache: PermissionResolutionCache | None = None,
) -> AuthorizedResource:
    """判权 + 取行一体。通过返回 AuthorizedResource；不通过抛结构化异常（404/403/500）。

    - `perm_cache` 缺省时内部自建（父链复判共享）；多声明工具由 `enforce_declaration` 传入同一份
      跨声明共享（doc32 §5.3 性能条目：memberships/role 解析 per-request 记忆化）。
    """
    if need not in _VALID_NEEDS:
        # 声明写错（need 非法）属配置错误，如实报 500 不吞（守卫应已拦，doc32 §5.5）
        raise errors.ServerError(msg=f'G6 声明档位非法：need={need!r}')

    adapter = resource_kind_registry.get(resource_type)
    if adapter is None:
        # 无 adapter → 500 配置错误（守卫测试应已拦，运行时如实报不降级放行，doc32 §5.3/§10）
        raise errors.ServerError(msg=f'资源类型 {resource_type!r} 未注册 G6 adapter')

    meta = await adapter.load_meta(db, resource_id)
    if meta is None:
        # 行不存在/已删/id 畸形 → 直接 404，**不上溯**：父 id 只能从子行读出（doc32 §5.3 评审修订）
        raise errors.NotFoundError(msg='资源不存在')

    cache = perm_cache if perm_cache is not None else PermissionResolutionCache()
    eff = await _effective_with_parent(
        db, adapter, subject, resource_type=resource_type, resource_id=resource_id, meta=meta, perm_cache=cache
    )
    # 维度②域限制叠加（仅 agent + owner 自有资源）
    eff = await _apply_domain_restriction(db, adapter, subject, meta, eff)

    if rank(eff) == 0:
        # 存在性隐藏：无权一律按不存在，不泄露资源存在（与 authorize_kb 现行、G1/G4 隐身门一致）
        raise errors.NotFoundError(msg='资源不存在')
    if rank(eff) < rank(need):
        raise errors.ForbiddenError(msg='没有该操作权限')
    return AuthorizedResource(meta=meta, permission=eff)


async def enforce_declaration(
    db: AsyncSession,
    subject: Subject,
    declarations: Sequence[dict[str, Any]],
    arguments: dict[str, Any],
) -> dict[str, AuthorizedResource]:
    """按工具的 resource_access 声明列表逐条判权，聚合返回 `{param → AuthorizedResource}`。

    声明元素形状：`{param, type, need, required?}`（required 缺省 True）。缺参：required → 422；
    required=False 且缺省 → 跳过该条。多声明与父链复判共享同一 per-request 记忆化缓存。
    """
    perm_cache = PermissionResolutionCache()
    authorized: dict[str, AuthorizedResource] = {}
    for decl in declarations:
        param = decl['param']
        rid = arguments.get(param)
        required = decl.get('required', True)
        if rid in (None, ''):
            if required:
                raise errors.RequestError(code=StandardResponseCode.HTTP_422, msg=f'缺少必填资源参数：{param}')
            continue  # 可选参数缺省 → 跳过该条声明
        authorized[param] = await require(
            db,
            subject,
            resource_type=decl['type'],
            resource_id=str(rid),
            need=decl['need'],
            perm_cache=perm_cache,
        )
    return authorized
