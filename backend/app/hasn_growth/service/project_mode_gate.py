"""获客项目化归属范围的服务端硬门禁。"""

from __future__ import annotations

from backend.common.exception import errors
from backend.core.conf import settings

ENTERPRISE_MODE_DISABLED = 'GROWTH_ENTERPRISE_PROJECT_MODE_DISABLED'
INVALID_OWNER_SCOPE = 'GROWTH_OWNER_SCOPE_INVALID'


def assert_project_scope_enabled(owner_scope: str) -> None:
    """校验获客项目化归属范围，企业身份未对齐时确定拒绝。

    该门禁只约束 v4 项目创建、挂靠、改绑与迁移入口，不影响旧版企业获客读写。
    所有新增项目化入口必须在任何业务写之前调用本函数。
    """
    scope = str(owner_scope or '').strip().lower()
    if scope == 'personal':
        return
    if scope == 'enterprise':
        if settings.GROWTH_PROJECT_V4_ENTERPRISE_ENABLED:
            return
        raise errors.ConflictError(
            msg='企业获客项目模式尚未开放',
            data={'error_code': ENTERPRISE_MODE_DISABLED},
        )
    raise errors.RequestError(
        msg='owner_scope 仅支持 personal 或 enterprise',
        data={'error_code': INVALID_OWNER_SCOPE},
    )
