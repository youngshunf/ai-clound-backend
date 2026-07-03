"""技能源内容指纹 compute_skill_content_hash 单元测试（doc14 §A1）。

驱动 common_skills_revision 的指纹必须：确定（同内容同 hash，否则 revision 抖动反复重拉）、
对正文/附带文件改动敏感（否则改了不触发更新）、对纯空白/换行差异不敏感（否则 CRLF/行尾空格误触发）。
纯函数，不依赖 DB。
"""

from __future__ import annotations

from pathlib import Path

from backend.app.marketplace.service.skill_content_extractor import compute_skill_content_hash

_SKILL = '---\nname: x\ndescription: y\n---\n# Body\n正文 A\n'


def _write(tmp_path: Path, text: str = _SKILL) -> Path:
    (tmp_path / 'SKILL.md').write_text(text, encoding='utf-8')
    return tmp_path


def test_deterministic_same_content_same_hash(tmp_path) -> None:
    d = _write(tmp_path)
    text = (d / 'SKILL.md').read_text()
    assert compute_skill_content_hash(d, text) == compute_skill_content_hash(d, text)


def test_whitespace_and_newline_insensitive(tmp_path) -> None:
    d = _write(tmp_path)
    base = compute_skill_content_hash(d, _SKILL)
    # 行尾空格 + CRLF → 规范化后等价 → 同 hash（否则平台差异误触发重拉）。
    crlf = _SKILL.replace('\n', '\r\n').replace('正文 A', '正文 A   ')
    assert compute_skill_content_hash(d, crlf) == base


def test_body_change_changes_hash(tmp_path) -> None:
    d = _write(tmp_path)
    h1 = compute_skill_content_hash(d, _SKILL)
    h2 = compute_skill_content_hash(d, _SKILL.replace('正文 A', '正文 B'))
    assert h1 != h2


def test_attached_file_change_changes_hash(tmp_path) -> None:
    d = _write(tmp_path)
    (d / 'ref.py').write_text('print(1)\n', encoding='utf-8')
    h1 = compute_skill_content_hash(d, _SKILL)
    (d / 'ref.py').write_text('print(2)\n', encoding='utf-8')
    h2 = compute_skill_content_hash(d, _SKILL)
    assert h1 != h2


def test_hidden_and_pyc_files_ignored(tmp_path) -> None:
    d = _write(tmp_path)
    h1 = compute_skill_content_hash(d, _SKILL)
    (d / '.DS_Store').write_text('junk', encoding='utf-8')
    (d / 'mod.pyc').write_text('bytecode', encoding='utf-8')
    cache = d / '__pycache__'
    cache.mkdir()
    (cache / 'x.pyc').write_text('bc', encoding='utf-8')
    assert compute_skill_content_hash(d, _SKILL) == h1  # 被忽略文件不影响指纹


def test_hash_is_short_stable_hex(tmp_path) -> None:
    d = _write(tmp_path)
    h = compute_skill_content_hash(d, _SKILL)
    assert len(h) == 16
    assert all(c in '0123456789abcdef' for c in h)
