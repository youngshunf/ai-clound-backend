"""图坊云端产品文案与事实源链接契约。"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RETIRED_ARCHITECTURE = '30-图像处理AI-Native应用(自研引擎·图坊)架构设计.md'
_CURRENT_ARCHITECTURE = '30-图坊/01-架构设计.md'


def _active_imagelab_sources() -> list[Path]:
    app_sources = (_REPO_ROOT / 'backend/app').rglob('*.py')
    sql_sources = [_REPO_ROOT / 'backend/sql/hasn/imagelab_project.sql']
    return [
        path
        for path in [*app_sources, *sql_sources]
        if 'imagelab' in path.name.lower() or 'imagelab' in path.read_text(encoding='utf-8').lower()
    ]


def test_active_cloud_sources_do_not_link_retired_imagelab_architecture() -> None:
    """活跃云端代码与 SQL 事实源不得继续链接已归档的单文件架构。"""
    sources = _active_imagelab_sources()
    assert sources

    retired_links = [
        str(path.relative_to(_REPO_ROOT))
        for path in sources
        if _RETIRED_ARCHITECTURE in path.read_text(encoding='utf-8')
    ]
    assert retired_links == []

    current_links = [path for path in sources if _CURRENT_ARCHITECTURE in path.read_text(encoding='utf-8')]
    assert current_links
