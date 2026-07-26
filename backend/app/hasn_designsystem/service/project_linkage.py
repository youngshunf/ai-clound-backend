"""设计系统根容器的平台项目挂靠 adapter（doc38 层2）。"""

from backend.app.hasn_designsystem.model.design_system import DesignSystem
from backend.app.hasn_project.service.project_linkage_registry import LinkageAdapter, project_linkage_registry


project_linkage_registry.register(
    LinkageAdapter(
        domain='designsystem',
        model=DesignSystem,
        id_column='id',
        owner_column='owner_hasn_id',
        attach_column='platform_project_id',
        id_is_uuid=False,
        is_container=True,
        app_id='designsystem',
        kind='designsystem.spec',
        title_column='name',
        deleted_column='deleted_time',
        sync_kind='designsystem',
        sync_scope='global',
    )
)
