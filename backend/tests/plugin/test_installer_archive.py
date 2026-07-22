"""插件归档工具回归测试。"""

import io
import zipfile

import pytest

from backend.common.exception import errors
from backend.plugin.installer import _plugin_name_from_filename, zip_plugin


def test_plugin_name_from_filename_rejects_missing_or_invalid_names() -> None:
    """插件名仅接受上传文件名中合法的标识符前缀。"""
    assert _plugin_name_from_filename('demo_plugin-1.0.0.zip') == 'demo_plugin'

    with pytest.raises(errors.RequestError, match='文件名非法'):
        _plugin_name_from_filename(None)
    with pytest.raises(errors.RequestError, match='文件名非法'):
        _plugin_name_from_filename('---.zip')


def test_zip_plugin_accepts_pathlike_and_excludes_python_cache(tmp_path) -> None:
    """归档真实目录时支持 PathLike 输入且不包含 Python 字节码缓存。"""
    plugin_dir = tmp_path / 'demo_plugin'
    plugin_dir.mkdir()
    (plugin_dir / 'plugin.toml').write_text('[plugin]\nname = "demo_plugin"\n', encoding='utf-8')
    cache_dir = plugin_dir / '__pycache__'
    cache_dir.mkdir()
    (cache_dir / 'ignored.pyc').write_bytes(b'bytecode')

    target = io.BytesIO()
    zip_plugin(plugin_dir, target)

    with zipfile.ZipFile(io.BytesIO(target.getvalue())) as archive:
        assert archive.namelist() == ['demo_plugin/plugin.toml']
