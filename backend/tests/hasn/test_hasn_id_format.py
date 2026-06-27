"""C2 安全修复回归守卫：生成的 hasn_id 必须符合 Core/02 §2.1 正则 ^[ha]_[a-z0-9]{16,22}$。

旧实现用 f'h_{uuid.uuid4()}'（带连字符共 36 字符）违反正则，与本地 hasn-core
identity.rs newtype `^[ha]_[a-z0-9]{16,22}$` 必然不匹配。本测试守卫生成式不再回退裸 uuid4()。
"""
from __future__ import annotations

import re
import uuid

from pathlib import Path

_CORE02_RE = re.compile(r'^[ha]_[a-z0-9]{16,22}$')


def test_generated_hasn_id_matches_core02_regex() -> None:
    # 复刻 hasn_auth.py 的生成式，断言 100 次随机生成都符合 Core/02 正则
    for _ in range(100):
        assert _CORE02_RE.match(f'h_{uuid.uuid4().hex[:20]}')
        assert _CORE02_RE.match(f'a_{uuid.uuid4().hex[:20]}')


def test_hasn_auth_source_uses_truncated_hex_not_bare_uuid() -> None:
    """源码守卫：hasn_auth.py 不得回退到裸 uuid.uuid4()（带连字符违反正则）。"""
    src = Path(__file__).resolve().parents[2] / 'app' / 'hasn' / 'service' / 'hasn_auth.py'
    text = src.read_text(encoding='utf-8')
    assert "f'h_{uuid.uuid4()}'" not in text
    assert "f'a_{uuid.uuid4()}'" not in text
    assert 'uuid.uuid4().hex[:20]' in text
