from __future__ import annotations

import cappa
import pytest

from backend import __version__
from backend.cli import FbaCli


def test_root_cli_help_exposes_production_command_graph(capsys: pytest.CaptureFixture[str]) -> None:
    """根命令帮助必须能加载完整生产命令图，且不执行任何写操作。"""
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.parse(FbaCli, argv=['--help'], color=False, version=__version__)

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    for command in ('init', 'run', 'add', 'remove', 'format', 'celery', 'alembic', 'codegen', 'skill', 'category'):
        assert command in output
