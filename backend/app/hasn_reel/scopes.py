"""reel（MoneyPrinterTurbo 短视频合成应用，模块 14 doc19）scope 展示元数据。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/19-MoneyPrinterTurbo短视频合成应用接入设计.md §7；
16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，由 `app/mcp/scopes.py` 聚合）。
判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据。

落地真相（hasn-node `crates/hasn-mcp/src/reel.rs`，reel-P4 待落；本表是云端侧契约源，
reel.rs `capability_scopes()` 须与之对齐——同 film 的跨仓零漂移守卫）：
- 读类工具（task.list/get、material.search）统一 `reel:read`（出厂 Allow，只读/查素材不下载）；
- 写类工具（generate/script.draft/preview/compose）统一 `reel:write`
  （出厂 Ask——合成长任务/消耗主人配额或本地资源，owner 三态可覆盖）；
- 导出/上传类（artifact.upload，把本地成片显式传云端私有桶）统一 `reel:export`（出厂 Ask）。

注：reel 只提供「合成」能力；项目/素材库/内容归属用 creator 的 `creator:*` scope（doc19 §5.5），
本应用不重造项目/素材 scope。
"""

from __future__ import annotations

REEL_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'reel:read': {
        'label_zh': '查看短视频任务',
        'label_en': 'View short-video tasks',
        'domain': 'reel',
        'risk': 'low',
        'description': '以 Agent 身份读取短视频合成任务、产物与状态，按词搜库存素材（只读/不下载，owner 隔离）',
        'description_en': 'Read short-video composition tasks, artifacts, and status, and search stock material by keyword as the agent (read-only, no download, owner-isolated)',
    },
    'reel:write': {
        'label_zh': '生成短视频',
        'label_en': 'Generate short videos',
        'domain': 'reel',
        'risk': 'medium',
        'default_mode': 'allow',
        'description': '生成短视频（文案/配音/字幕/素材合成）：一把梭出片、文案审稿、半成品预览、确认合成（生成类，2026-07-05 放开出厂 Allow，主人可改 Ask 把关配额）',
        'description_en': 'Generate short videos (script/voiceover/subtitles/material composition): one-shot rendering, script review, draft preview, and confirmed composition (consumes quota or local compositing resources; confirmation required by default)',
    },
    'reel:export': {
        'label_zh': '上传/分享短视频成片',
        'label_en': 'Upload/share finished reels',
        'domain': 'reel',
        'risk': 'medium',
        'default_mode': 'ask',
        'description': '把本地短视频成片显式上传到云端私有桶以分享/跨设备查看（本地权威，重资产默认需主人确认）',
        'description_en': 'Explicitly upload finished local reels to a cloud private bucket for sharing or cross-device viewing (local-authoritative; heavy assets need owner confirmation by default)',
    },
}
