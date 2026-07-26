"""Deck 容器的平台项目挂靠 adapter（doc38 U11c）。

import 即把 ``hasn://deck/{server_id}`` 注册为容器级挂靠点。项目只提供跨应用归集视角，
Deck 的读取与编辑权限仍完全由既有 owner/ACL 判权决定。
"""

from backend.app.hasn_deck.model import Deck
from backend.app.hasn_project.service.project_linkage_registry import (
    LinkageAdapter,
    project_linkage_registry,
)


project_linkage_registry.register(
    LinkageAdapter(
        domain='deck',
        model=Deck,
        id_column='id',
        owner_column='owner_id',
        attach_column='platform_project_id',
        id_is_uuid=False,
        is_container=True,
        app_id='deck',
        kind='presentation',
        title_column='title',
        revision_column='rev',
        sync_kind='decks',
    )
)
