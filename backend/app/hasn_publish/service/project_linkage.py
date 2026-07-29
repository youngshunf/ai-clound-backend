"""Publish 站点的平台项目联邦挂靠声明。"""

from backend.app.hasn_project.service.project_linkage_registry import (
    LinkageAdapter,
    project_linkage_registry,
)
from backend.app.hasn_publish.model.site import Site


project_linkage_registry.register(
    LinkageAdapter(
        domain='publish/sites',
        model=Site,
        id_column='id',
        owner_column='owner_id',
        attach_column='platform_project_id',
        id_is_uuid=False,
        is_container=True,
        app_id='publish',
        kind='publish.site',
        title_column='title',
        deleted_column='deleted_time',
    )
)
