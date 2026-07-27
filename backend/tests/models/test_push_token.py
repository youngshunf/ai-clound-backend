"""B3 + B10 - push_tokens model + migration 契约测试.

测试策略 (与 B2 `test_logout.py` 一致: 不依赖真实 Postgres / pytest-asyncio):
- 构造独立 SQLAlchemy metadata + in-memory SQLite 引擎 (sync), 手工建镜像表。
- 对 `PushChannel` / `PUSH_CHANNEL_VALUES` 做枚举契约断言 (M1 只有 'umeng_push')。
- 对 `PushToken` ORM 做字段 / 唯一约束存在性断言 (对齐 alembic 迁移契约)。
- 对 alembic 迁移模块做 revision 链断言 (upgrade / downgrade 可执行)。
- B10: `EncryptedToken` 加解密契约 + SQLAlchemy after_insert/after_update/
  after_delete 审计事件监听器, 通过 mirror 模式在 SQLite BLOB 列上完整跑通,
  断言 raw storage 是密文字节, 且每次行变更同事务内 push_token_audit 多 1 行。

覆盖 B3 acceptance:
1. PushChannel enum 只含 'umeng_push' (M1 固定)
2. PushToken model 含 hasn_id / device_id / channel / token / registered_at / last_seen_at
3. push_tokens 表在 SQLite 上 CREATE + INSERT + 违反复合唯一 → IntegrityError
4. alembic 迁移 revision id / down_revision 正确 (chain 到 B2)

覆盖 B10 acceptance:
1. alembic 迁移 20260421_b10_push_audit (down_revision=B8) 可 import + upgrade/downgrade callable
2. PushToken.token 的 SQLAlchemy 列类型是 `EncryptedToken` (自动加解密)
3. 加密 roundtrip: encrypt → decrypt 还原 + 密文 != 明文 bytes
4. SQLite 端到端: ORM 插入 token='secret-xyz' → raw SELECT bytes 不含明文子串
5. push_token_audit 表在 INSERT / UPDATE / DELETE 时各多 1 行, action 值正确
"""
from __future__ import annotations

import importlib

from collections.abc import Iterator
from typing import cast

import pytest
import sqlalchemy as sa

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.app.hasn_client.model._encryption import (
    EncryptedToken,
    decrypt_push_token,
    encrypt_push_token,
    reset_fernet_for_tests,
)
from backend.app.hasn_client.model.push_token import (
    PUSH_CHANNEL_VALUES,
    PushChannel,
    PushToken,
)
from backend.app.hasn_client.model.push_token_audit import register_audit_listeners

# ---------------------------------------------------------------------------
# 1. PushChannel 枚举契约
# ---------------------------------------------------------------------------


def test_push_channel_enum_only_contains_umeng_push() -> None:
    """M1 channel 枚举只能有 'umeng_push'; FCM / getui / jpush 等必须等到 M2.

    这是 PRD B3 的明确验收: 'PushChannel enum 只含 umeng_push 一个值（M1）'。
    """
    values = {c.value for c in PushChannel}
    assert values == {'umeng_push'}, (
        f'M1 PushChannel 必须仅含 umeng_push, 实际={values}. '
        f'若要加 FCM / 其他通道, 必须新开 M2 phase 评审 + 迁移。'
    )
    assert frozenset({'umeng_push'}) == PUSH_CHANNEL_VALUES


def test_push_channel_is_string_enum_for_db_storage() -> None:
    """channel 必须是 str 枚举 (DB 存字符串, 不存整数), 方便 alembic 与聚合服务对齐."""
    assert isinstance(PushChannel.UMENG_PUSH.value, str)
    assert PushChannel.UMENG_PUSH.value == 'umeng_push'


# ---------------------------------------------------------------------------
# 2. PushToken ORM 字段契约
# ---------------------------------------------------------------------------


def test_push_token_model_has_required_columns() -> None:
    """对齐 docs/架构设计/移动端/04 §5.3 + PRD B3 description."""
    table = PushToken.__table__
    col_names = {c.name for c in table.columns}

    required = {
        'id',
        'hasn_id',
        'device_id',
        'channel',
        'token',
        'registered_at',
        'last_seen_at',
    }
    missing = required - col_names
    assert not missing, f'push_tokens 缺字段: {missing}'


def test_push_token_table_has_composite_unique_constraint() -> None:
    """B3 核心约束: (hasn_id, device_id, channel) 复合唯一."""
    constraints = cast(sa.Table, PushToken.__table__).constraints
    uniques = [c for c in constraints if isinstance(c, sa.UniqueConstraint)]
    assert uniques, 'PushToken 必须至少有 1 个 UniqueConstraint'

    cols_per_uq = [
        sorted(col.name for col in uq.columns) for uq in uniques
    ]
    expected = sorted(['hasn_id', 'device_id', 'channel'])
    assert expected in cols_per_uq, (
        f'(hasn_id, device_id, channel) 复合唯一约束缺失; 实际={cols_per_uq}'
    )


def test_push_token_table_has_hasn_id_index() -> None:
    """hasn_id 必须有独立索引 (按 owner 查活跃 token 是推送热路径)."""
    index_cols = [
        sorted(col.name for col in ix.columns)
        for ix in cast(sa.Table, PushToken.__table__).indexes
    ]
    assert ['hasn_id'] in index_cols, f'hasn_id 索引缺失; 实际={index_cols}'


# ---------------------------------------------------------------------------
# 3. SQLite 端到端: CREATE + INSERT + 唯一冲突
# ---------------------------------------------------------------------------


class _IsolatedBase(DeclarativeBase):
    """孤立 declarative base, 避免污染生产 metadata."""


class _PushTokenMirror(_IsolatedBase):
    """与 PushToken 字段对齐的 SQLite 友好镜像.

    生产 Base 带 dataclass + TimeZone TypeDecorator, 直接在 SQLite 上跑会
    引入无关耦合; 此镜像只测"复合唯一索引是否生效"这一 DDL 语义契约。
    """

    __tablename__ = 'push_tokens'
    __table_args__ = (
        sa.UniqueConstraint(
            'hasn_id', 'device_id', 'channel',
            name='uq_push_tokens_hasn_device_channel',
        ),
    )

    id: Mapped[int] = mapped_column(
        sa.Integer, primary_key=True, autoincrement=True
    )
    hasn_id: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    device_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    channel: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default='umeng_push'
    )
    token: Mapped[str] = mapped_column(sa.String(512), nullable=False, default='')


@pytest.fixture
def sqlite_session() -> Iterator[sa.orm.Session]:
    engine = sa.create_engine('sqlite://')
    _IsolatedBase.metadata.create_all(engine)
    session_factory = sa.orm.sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    yield session
    session.close()
    engine.dispose()


def test_push_token_insert_then_duplicate_triggers_unique_violation(sqlite_session: sa.orm.Session) -> None:
    """同一 (hasn_id, device_id, channel) 插两次 → 第二次 IntegrityError."""
    sqlite_session.add(
        _PushTokenMirror(
            hasn_id='h_test_001',
            device_id='device-aaa',
            channel='umeng_push',
            token='first-token',
        )
    )
    sqlite_session.commit()

    sqlite_session.add(
        _PushTokenMirror(
            hasn_id='h_test_001',
            device_id='device-aaa',
            channel='umeng_push',
            token='second-token-should-collide',
        )
    )
    with pytest.raises(IntegrityError):
        sqlite_session.commit()
    sqlite_session.rollback()


def test_push_token_different_device_allowed_for_same_hasn(sqlite_session: sa.orm.Session) -> None:
    """同一 hasn_id 下不同 device_id → 允许 (一个用户多设备)."""
    sqlite_session.add_all(
        [
            _PushTokenMirror(
                hasn_id='h_test_001',
                device_id='device-aaa',
                channel='umeng_push',
                token='tok-a',
            ),
            _PushTokenMirror(
                hasn_id='h_test_001',
                device_id='device-bbb',
                channel='umeng_push',
                token='tok-b',
            ),
        ]
    )
    sqlite_session.commit()

    count = sqlite_session.scalar(
        sa.select(sa.func.count()).select_from(_PushTokenMirror)
    )
    assert count == 2


# ---------------------------------------------------------------------------
# 4. Alembic 迁移模块契约
# ---------------------------------------------------------------------------


def test_b3_migration_module_chain() -> None:
    """B3 迁移 revision 链到 B2, upgrade/downgrade 可调用."""
    mod = importlib.import_module(
        'backend.alembic.versions.20260421_b3_create_push_tokens'
    )
    assert mod.revision == '20260421_b3_push_tokens'
    assert mod.down_revision == '20260421_b2_jwt_revoc', (
        'B3 必须 chain 在 B2 后面, 否则 alembic upgrade head 会丢新表'
    )
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# 5. B10 - EncryptedToken 加解密契约
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_roundtrip_returns_plaintext() -> None:
    """B10: encrypt 后 decrypt 得回原明文; 且加密输出不等于明文编码."""
    reset_fernet_for_tests()
    plaintext = 'umeng-device-token-abcdef-1234567890'
    ciphertext = encrypt_push_token(plaintext)

    assert isinstance(ciphertext, bytes), 'encrypt_push_token 必须返回 bytes (BYTEA/BLOB 兼容)'
    assert plaintext.encode('utf-8') not in ciphertext, (
        '密文 bytes 里不应出现明文子串 — 证明真的加密了而非编码'
    )
    assert decrypt_push_token(ciphertext) == plaintext


def test_encrypt_same_plaintext_produces_different_ciphertexts() -> None:
    """Fernet 每次加密使用新 IV, 同明文 → 不同密文 (已知属性, 防退化)."""
    reset_fernet_for_tests()
    ct1 = encrypt_push_token('same-token')
    ct2 = encrypt_push_token('same-token')
    assert ct1 != ct2, 'Fernet 必须每次使用新 IV'
    assert decrypt_push_token(ct1) == 'same-token'
    assert decrypt_push_token(ct2) == 'same-token'


def test_push_token_column_type_is_encrypted_token() -> None:
    """B10: PushToken.token 列类型必须是 EncryptedToken (自动加解密契约)."""
    col = PushToken.__table__.c.token
    assert isinstance(col.type, EncryptedToken), (
        f'PushToken.token 必须是 EncryptedToken 以自动加解密; 实际={type(col.type).__name__}'
    )


# ---------------------------------------------------------------------------
# 6. B10 - 端到端: SQLite BLOB 存密文 + 审计事件监听器
# ---------------------------------------------------------------------------


class _B10Base(DeclarativeBase):
    """独立 declarative base (与 _IsolatedBase 分离), 专供 B10 mirror 测试."""


class _PushTokenEncryptedMirror(_B10Base):
    """与 PushToken 对齐的 SQLite mirror, 但 token 列使用 EncryptedToken.

    这样 SQLite 底层就是 BLOB 存 Fernet 密文, 契约与生产 Postgres BYTEA 一致.
    """

    __tablename__ = 'push_tokens'
    __table_args__ = (
        sa.UniqueConstraint(
            'hasn_id', 'device_id', 'channel',
            name='uq_push_tokens_hasn_device_channel',
        ),
    )

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    hasn_id: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    device_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    channel: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default='umeng_push'
    )
    token: Mapped[str] = mapped_column(EncryptedToken(), nullable=False, default='')


class _PushTokenAuditMirror(_B10Base):
    """审计 mirror (push_token_audit). 无 FK, 与生产同 schema 子集."""

    __tablename__ = 'push_token_audit'

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    push_token_id: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    hasn_id: Mapped[str] = mapped_column(sa.String(40), nullable=False, default='')
    device_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, default='')
    channel: Mapped[str] = mapped_column(sa.String(16), nullable=False, default='umeng_push')
    action: Mapped[str] = mapped_column(sa.String(16), nullable=False, default='INSERT')
    occurred_at: Mapped[str] = mapped_column(sa.String(64), nullable=False, default='')


# 注册一次事件监听器到 mirror 类 + mirror audit 表 (与生产同结构的 wiring).
register_audit_listeners(_PushTokenEncryptedMirror, cast(sa.Table, _PushTokenAuditMirror.__table__))


@pytest.fixture
def b10_session() -> Iterator[sa.orm.Session]:
    """SQLite in-memory 会话, 两张 mirror 表 DDL 就绪."""
    reset_fernet_for_tests()
    engine = sa.create_engine('sqlite://')
    _B10Base.metadata.create_all(engine)
    session_factory = sa.orm.sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    yield session
    session.close()
    engine.dispose()


def test_push_token_raw_storage_is_ciphertext_not_plaintext(b10_session: sa.orm.Session) -> None:
    """B10 核心验收: ORM 插入 token='secret-xyz' → raw SELECT 是 bytes 不含明文子串."""
    plaintext = 'secret-umeng-device-token-DEAD-BEEF'
    b10_session.add(
        _PushTokenEncryptedMirror(
            hasn_id='h_b10_raw_001',
            device_id='dev-raw-001',
            channel='umeng_push',
            token=plaintext,
        )
    )
    b10_session.commit()

    raw_row = b10_session.execute(
        sa.text(
            'SELECT token FROM push_tokens WHERE hasn_id = :hid AND device_id = :did'
        ),
        {'hid': 'h_b10_raw_001', 'did': 'dev-raw-001'},
    ).one()
    raw_value = raw_row[0]

    assert isinstance(raw_value, (bytes, memoryview)), (
        f'底层存储必须是 bytes/memoryview (BYTEA); 实际类型={type(raw_value).__name__}'
    )
    if isinstance(raw_value, memoryview):
        raw_value = bytes(raw_value)
    assert plaintext.encode('utf-8') not in raw_value, (
        f'raw BLOB 不应含明文子串, 否则加密失败; 前 40 bytes={raw_value[:40]!r}'
    )

    # ORM 读回自动解密.
    fetched = b10_session.execute(
        sa.select(_PushTokenEncryptedMirror).where(
            _PushTokenEncryptedMirror.hasn_id == 'h_b10_raw_001'
        )
    ).scalar_one()
    assert fetched.token == plaintext


def test_push_token_insert_writes_audit_row(b10_session: sa.orm.Session) -> None:
    """B10 验收: INSERT push_tokens → push_token_audit 多 1 行 (action='INSERT')."""
    before_count = b10_session.scalar(
        sa.select(sa.func.count()).select_from(_PushTokenAuditMirror)
    )
    b10_session.add(
        _PushTokenEncryptedMirror(
            hasn_id='h_b10_ins_001',
            device_id='dev-ins-001',
            channel='umeng_push',
            token='token-to-audit',
        )
    )
    b10_session.commit()

    after_count = b10_session.scalar(
        sa.select(sa.func.count()).select_from(_PushTokenAuditMirror)
    )
    assert before_count is not None and after_count is not None
    assert after_count == before_count + 1, 'INSERT 必须触发 1 行审计'

    audit = b10_session.execute(
        sa.select(_PushTokenAuditMirror).where(
            _PushTokenAuditMirror.hasn_id == 'h_b10_ins_001'
        )
    ).scalar_one()
    assert audit.action == 'INSERT'
    assert audit.device_id == 'dev-ins-001'
    assert audit.channel == 'umeng_push'
    assert audit.push_token_id is not None, '审计必须关联到 push_tokens.id'


def test_push_token_update_writes_audit_row(b10_session: sa.orm.Session) -> None:
    """B10: UPDATE 触发 action='UPDATE' 审计行 (跟 B4 upsert 路径一致)."""
    row = _PushTokenEncryptedMirror(
        hasn_id='h_b10_upd_001',
        device_id='dev-upd-001',
        channel='umeng_push',
        token='original',
    )
    b10_session.add(row)
    b10_session.commit()

    before_count = b10_session.scalar(
        sa.select(sa.func.count()).select_from(_PushTokenAuditMirror)
    )

    row.token = 'rotated-token'
    b10_session.commit()

    after_count = b10_session.scalar(
        sa.select(sa.func.count()).select_from(_PushTokenAuditMirror)
    )
    assert before_count is not None and after_count is not None
    assert after_count == before_count + 1, 'UPDATE 必须触发 1 行审计'

    last_audit = b10_session.execute(
        sa.select(_PushTokenAuditMirror)
        .where(_PushTokenAuditMirror.hasn_id == 'h_b10_upd_001')
        .order_by(_PushTokenAuditMirror.id.desc())
    ).scalars().first()
    assert last_audit is not None
    assert last_audit.action == 'UPDATE'


def test_push_token_delete_writes_audit_row(b10_session: sa.orm.Session) -> None:
    """B10: DELETE 触发 action='DELETE' 审计行 (登出清理路径)."""
    row = _PushTokenEncryptedMirror(
        hasn_id='h_b10_del_001',
        device_id='dev-del-001',
        channel='umeng_push',
        token='to-be-deleted',
    )
    b10_session.add(row)
    b10_session.commit()

    before_count = b10_session.scalar(
        sa.select(sa.func.count()).select_from(_PushTokenAuditMirror)
    )

    b10_session.delete(row)
    b10_session.commit()

    after_count = b10_session.scalar(
        sa.select(sa.func.count()).select_from(_PushTokenAuditMirror)
    )
    assert before_count is not None and after_count is not None
    assert after_count == before_count + 1, 'DELETE 必须触发 1 行审计'

    last_audit = b10_session.execute(
        sa.select(_PushTokenAuditMirror)
        .where(_PushTokenAuditMirror.hasn_id == 'h_b10_del_001')
        .order_by(_PushTokenAuditMirror.id.desc())
    ).scalars().first()
    assert last_audit is not None
    assert last_audit.action == 'DELETE'


def test_b10_migration_module_chain() -> None:
    """B10 迁移 revision 链到 B8, upgrade/downgrade callable (对齐 B3 契约测试)."""
    mod = importlib.import_module(
        'backend.alembic.versions.20260421_b10_push_token_encryption_audit'
    )
    assert mod.revision == '20260421_b10_push_audit'
    assert mod.down_revision == '20260421_b8_telemetry_events', (
        'B10 必须 chain 在 B8 后面, 保证 alembic upgrade head 正确应用加密迁移'
    )
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)
