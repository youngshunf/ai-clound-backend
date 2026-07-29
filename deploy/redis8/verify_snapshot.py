#!/usr/bin/env python3
"""只读比较 Redis 快照迁移前后的键、类型、内容和 TTL。"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import sys

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import SplitResult, urlsplit, urlunsplit

from redis import Redis

SUPPORTED_TYPES = {'string', 'list', 'set', 'zset', 'hash', 'stream'}
DATABASES = range(16)
TTL_TOLERANCE_MS = 15_000


@dataclass(frozen=True)
class KeySnapshot:
    redis_type: str
    digest: str
    pttl: int


def _database_url(base_url: str, database: int) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {'redis', 'rediss'} or not parsed.hostname:
        raise ValueError('Redis URL 格式无效')
    replaced = SplitResult(
        scheme=parsed.scheme,
        netloc=parsed.netloc,
        path=f'/{database}',
        query=parsed.query,
        fragment='',
    )
    return urlunsplit(replaced)


def _client(base_url: str, database: int) -> Redis:
    return Redis.from_url(
        _database_url(base_url, database),
        decode_responses=False,
        socket_connect_timeout=5,
        socket_timeout=30,
        health_check_interval=15,
    )


def _write_bytes(hasher: Any, value: bytes) -> None:
    hasher.update(struct.pack('>Q', len(value)))
    hasher.update(value)


def _write_sequence(hasher: Any, values: Iterable[bytes]) -> None:
    for value in values:
        _write_bytes(hasher, value)


def _digest_string(client: Redis, key: bytes, hasher: Any) -> None:
    value = cast('bytes | None', client.get(key))
    if value is None:
        raise RuntimeError('键在读取期间消失')
    _write_bytes(hasher, value)


def _digest_list(client: Redis, key: bytes, hasher: Any) -> None:
    values = cast('list[bytes]', client.lrange(key, 0, -1))
    _write_sequence(hasher, values)


def _digest_set(client: Redis, key: bytes, hasher: Any) -> None:
    values = cast('set[bytes]', client.smembers(key))
    _write_sequence(hasher, sorted(values))


def _digest_zset(client: Redis, key: bytes, hasher: Any) -> None:
    members = cast(
        'list[tuple[bytes, float]]',
        client.zrange(key, 0, -1, withscores=True),
    )
    for member, score in members:
        _write_bytes(hasher, member)
        _write_bytes(hasher, float(score).hex().encode('ascii'))


def _digest_hash(client: Redis, key: bytes, hasher: Any) -> None:
    values = cast('dict[bytes, bytes]', client.hgetall(key))
    for field, value in sorted(values.items()):
        _write_bytes(hasher, field)
        _write_bytes(hasher, value)


def _digest_stream(client: Redis, key: bytes, hasher: Any) -> None:
    entries = cast(
        'list[tuple[bytes, dict[bytes, bytes]]]',
        client.xrange(key, min='-', max='+'),
    )
    for entry_id, fields in entries:
        _write_bytes(hasher, entry_id)
        for field, value in sorted(fields.items()):
            _write_bytes(hasher, field)
            _write_bytes(hasher, value)


DIGEST_WRITERS = {
    'string': _digest_string,
    'list': _digest_list,
    'set': _digest_set,
    'zset': _digest_zset,
    'hash': _digest_hash,
    'stream': _digest_stream,
}


def _digest_value(client: Redis, key: bytes, redis_type: str) -> str:
    writer = DIGEST_WRITERS.get(redis_type)
    if writer is None:
        raise RuntimeError(f'不支持的 Redis 类型：{redis_type}')

    hasher = hashlib.sha256()
    _write_bytes(hasher, redis_type.encode('ascii'))
    writer(client, key, hasher)
    return hasher.hexdigest()


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def _snapshot_key(client: Redis, key: bytes) -> KeySnapshot | None:
    initial_ttl = client.pttl(key)
    if 0 <= initial_ttl <= 60_000:
        return None

    raw_type = client.type(key)
    if not isinstance(raw_type, bytes):
        raise TypeError('Redis 返回了无法识别的类型')
    redis_type = raw_type.decode('ascii')
    if redis_type == 'none':
        return None
    if redis_type not in SUPPORTED_TYPES:
        raise RuntimeError(f'不支持的 Redis 类型：{redis_type}')

    digest = _digest_value(client, key, redis_type)
    final_ttl = client.pttl(key)
    if final_ttl == -2:
        return None
    if 0 <= final_ttl <= 60_000:
        return None

    return KeySnapshot(redis_type=redis_type, digest=digest, pttl=final_ttl)


def _snapshot_database(client: Redis) -> dict[bytes, KeySnapshot]:
    result: dict[bytes, KeySnapshot] = {}
    keys = cast('Iterable[bytes]', client.scan_iter(count=1_000))
    for key in keys:
        snapshot = _snapshot_key(client, key)
        if snapshot is not None:
            result[key] = snapshot
    return result


def _memory_summary(client: Redis) -> dict[str, int | float]:
    stats = client.memory_stats()
    summary: dict[str, int | float] = {}
    for raw_key, value in stats.items():
        key = raw_key.decode('utf-8', errors='replace') if isinstance(raw_key, bytes) else str(raw_key)
        if isinstance(value, (int, float)):
            summary[key] = value
    return summary


def _ttl_matches(source_ttl: int, target_ttl: int) -> bool:
    if source_ttl == -1 or target_ttl == -1:
        return source_ttl == target_ttl
    return abs(source_ttl - target_ttl) <= TTL_TOLERANCE_MS


def _compare_database(
    database: int,
    source: Redis,
    target: Redis,
) -> tuple[dict[str, Any], list[str]]:
    source_size = source.dbsize()
    target_size = target.dbsize()
    source_snapshot = _snapshot_database(source)
    target_snapshot = _snapshot_database(target)

    source_keys = set(source_snapshot)
    target_keys = set(target_snapshot)
    errors: list[str] = [f'db={database} 目标缺少键 id={_key_id(key)}' for key in sorted(source_keys - target_keys)]
    errors.extend(f'db={database} 目标存在额外键 id={_key_id(key)}' for key in sorted(target_keys - source_keys))

    for key in sorted(source_keys & target_keys):
        source_value = source_snapshot[key]
        target_value = target_snapshot[key]
        key_id = _key_id(key)
        if source_value.redis_type != target_value.redis_type:
            errors.append(f'db={database} 类型不一致 id={key_id}')
            continue
        if source_value.digest != target_value.digest:
            errors.append(f'db={database} 内容不一致 id={key_id}')
        if not _ttl_matches(source_value.pttl, target_value.pttl):
            errors.append(f'db={database} TTL 不一致 id={key_id}')

    if source_size != target_size:
        errors.append(f'db={database} dbsize 不一致 source={source_size} target={target_size}')

    summary = {
        'database': database,
        'source_dbsize': source_size,
        'target_dbsize': target_size,
        'compared_keys': len(source_keys & target_keys),
        'source_memory': _memory_summary(source),
        'target_memory': _memory_summary(target),
        'errors': len(errors),
    }
    return summary, errors


def _required_url(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f'必须设置 {name}')
    return value


def main() -> int:
    source_url = _required_url('SOURCE_REDIS_URL')
    target_url = _required_url('TARGET_REDIS_URL')
    summaries: list[dict[str, Any]] = []
    errors: list[str] = []

    for database in DATABASES:
        source = _client(source_url, database)
        target = _client(target_url, database)
        try:
            summary, database_errors = _compare_database(
                database,
                source,
                target,
            )
            summaries.append(summary)
            errors.extend(database_errors)
        finally:
            source.close()
            target.close()

    print(json.dumps({'databases': summaries, 'error_count': len(errors)}, ensure_ascii=False))
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        print(f'快照校验失败：{exc}', file=sys.stderr)
        raise SystemExit(2) from None
    except Exception as exc:
        print(f'快照校验失败：{type(exc).__name__}', file=sys.stderr)
        raise SystemExit(2) from None
