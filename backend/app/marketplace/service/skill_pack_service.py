"""技能包（skill_pack）服务层（实施/91 B2.2 + B2.4）。

把 skill_pack 的落库 upsert + hermes_yaml 结构校验/规范化集中到一处，供路由
（`api/v1/skill_pack.py`）、MCP 工具（`publish_skill_pack`）、hub 同步（`github_sync_service`）共用，
避免 raw SQL 与校验逻辑散落漂移。

权威载体：`marketplace_template(template_type='skill_pack')` + `marketplace_template_version`。
"""

from __future__ import annotations

import hashlib
import json
import re

from decimal import Decimal
from typing import Any

import sqlalchemy as sa
import yaml

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.schema.skill_pack import SkillPackCreateRequest
from backend.common.exception import errors


def normalize_slug(value: str) -> str:
    """与上游一致的 slug 归一化：小写、空格/下划线→连字符、去非法字符、压缩重复连字符。"""
    text = (value or '').strip().lower()
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'[^a-z0-9-]', '', text)
    text = re.sub(r'-{2,}', '-', text).strip('-')
    return text


def content_hash(value: str) -> str:
    return f'sha256:{hashlib.sha256(value.encode("utf-8")).hexdigest()}'


def template_id_for(namespace: str | None, bundle_slug: str) -> str:
    prefix = f'{namespace}/' if namespace else 'skill-pack/'
    return f'{prefix}{bundle_slug}'


def member_skill_ids(hermes_yaml: str) -> list[str]:
    """从规范化 hermes_yaml 解析成员技能 id 列表（保序去重）。"""
    spec = yaml.safe_load(hermes_yaml)
    if not isinstance(spec, dict):
        return []
    out: list[str] = []
    for item in spec.get('skills') or []:
        sid = item.strip() if isinstance(item, str) else None
        if sid and sid not in out:
            out.append(sid)
    return out


def validate_and_normalize_bundle(payload: SkillPackCreateRequest) -> tuple[str, str]:
    """校验 + 规范化 hermes_yaml（B2.2，堵透传盲区）。

    校验：hermes_yaml 可 safe_load 成 dict；skills 为非空 list；slug 自洽
    （归一化 name == bundle_slug，command_key == '/' + bundle_slug）。
    规范化：用 yaml.safe_dump 重新序列化为权威 hermes_yaml（不再透传调用方原文）。

    返回 `(normalized_hermes_yaml, content_hash)`。任一不合法抛 `RequestError`。
    """
    try:
        spec = yaml.safe_load(payload.hermes_yaml)
    except yaml.YAMLError as exc:  # noqa: BLE001
        raise errors.RequestError(msg=f'hermes_yaml 不是合法 YAML：{exc}')
    if not isinstance(spec, dict):
        raise errors.RequestError(msg='hermes_yaml 顶层必须是对象（dict）')

    skills = spec.get('skills')
    if not isinstance(skills, list) or not skills:
        raise errors.RequestError(msg='hermes_yaml.skills 必须是非空列表')
    if not all(isinstance(s, str) and s.strip() for s in skills):
        raise errors.RequestError(msg='hermes_yaml.skills 成员必须是非空字符串')

    expected_slug = normalize_slug(payload.bundle_slug)
    if not expected_slug:
        raise errors.RequestError(msg='bundle_slug 归一化后为空')
    name_slug = normalize_slug(str(spec.get('name') or payload.bundle_slug))
    if name_slug != expected_slug:
        raise errors.RequestError(msg=f'hermes_yaml.name 归一化({name_slug}) 与 bundle_slug({expected_slug}) 不一致')
    if payload.command_key != f'/{expected_slug}':
        raise errors.RequestError(msg=f'command_key 必须是 /{expected_slug}')

    # 规范化产出：稳定键序、允许 unicode；落库与 content_hash 都基于它。
    normalized = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False).strip() + '\n'
    return normalized, content_hash(normalized)


def _json_text(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


async def upsert_skill_pack(
    db: AsyncSession,
    payload: SkillPackCreateRequest,
    *,
    author_id: int | None,
    is_common: bool | None = None,
) -> dict[str, Any]:
    """落库 skill_pack（template + version upsert），落库前先 B2.2 校验+规范化。

    路由 / MCP 工具 / hub 同步共用此入口（DRY）。返回落库后的关键字段快照。
    """
    normalized_yaml, hash_value = validate_and_normalize_bundle(payload)
    template_id = payload.template_id or template_id_for(payload.namespace, payload.bundle_slug)

    template_params: dict[str, Any] = {
        'template_id': template_id,
        'namespace': payload.namespace,
        'slug': payload.bundle_slug,
        'name': payload.name,
        'description': payload.description,
        'author_id': author_id,
        'price': Decimal('0'),
        'is_private': payload.is_private,
        'is_official': payload.is_official,
    }
    # is_common 仅在显式给出时写入/更新（hub 同步用），否则保持 DB 现值。
    is_common_set = 'is_common = :is_common,' if is_common is not None else ''
    is_common_insert_col = ', is_common' if is_common is not None else ''
    is_common_insert_val = ', :is_common' if is_common is not None else ''
    if is_common is not None:
        template_params['is_common'] = is_common

    await db.execute(
        sa.text(
            f'''
            INSERT INTO public.marketplace_template (
                template_id, namespace, slug, template_type, name, description,
                author_id, pricing_type, price, is_private, is_official,
                download_count, source_type, created_time, updated_time{is_common_insert_col}
            ) VALUES (
                :template_id, :namespace, :slug, 'skill_pack', :name, :description,
                :author_id, 'free', :price, :is_private, :is_official,
                0, 'local', now(), now(){is_common_insert_val}
            )
            ON CONFLICT (template_id) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                is_private = EXCLUDED.is_private,
                is_official = EXCLUDED.is_official,
                {is_common_set}
                updated_time = now()
            '''
        ),
        template_params,
    )
    await db.execute(
        sa.text(
            '''
            UPDATE public.marketplace_template_version
            SET is_latest = false, updated_time = now()
            WHERE template_id = :template_id
            '''
        ),
        {'template_id': template_id},
    )
    await db.execute(
        sa.text(
            '''
            INSERT INTO public.marketplace_template_version (
                template_id, version, changelog, skill_dependencies_versioned,
                bundle_slug, command_key, hermes_bundle_json, hermes_yaml,
                content_hash, file_hash, is_latest, published_at, created_time, updated_time
            ) VALUES (
                :template_id, :version, NULL, CAST(:skill_dependencies_versioned AS jsonb),
                :bundle_slug, :command_key, CAST(:hermes_bundle_json AS jsonb), :hermes_yaml,
                :content_hash, :file_hash, true, now(), now(), now()
            )
            ON CONFLICT (template_id, version) DO UPDATE SET
                skill_dependencies_versioned = EXCLUDED.skill_dependencies_versioned,
                bundle_slug = EXCLUDED.bundle_slug,
                command_key = EXCLUDED.command_key,
                hermes_bundle_json = EXCLUDED.hermes_bundle_json,
                hermes_yaml = EXCLUDED.hermes_yaml,
                content_hash = EXCLUDED.content_hash,
                file_hash = EXCLUDED.file_hash,
                is_latest = true,
                published_at = EXCLUDED.published_at,
                updated_time = now()
            '''
        ),
        {
            'template_id': template_id,
            'version': payload.version,
            'skill_dependencies_versioned': _json_text(payload.skill_dependencies_versioned),
            'bundle_slug': payload.bundle_slug,
            'command_key': payload.command_key,
            'hermes_bundle_json': _json_text(payload.hermes_bundle_json),
            'hermes_yaml': normalized_yaml,
            # content_hash 含 'sha256:' 前缀（列宽 128）；file_hash 列宽仅 64，写裸 64-hex 摘要。
            'content_hash': hash_value,
            'file_hash': hash_value.removeprefix('sha256:'),
        },
    )
    return {
        'template_id': template_id,
        'version': payload.version,
        'name': payload.name,
        'description': payload.description,
        'bundle_slug': payload.bundle_slug,
        'command_key': payload.command_key,
        'hermes_bundle_json': payload.hermes_bundle_json,
        'hermes_yaml': normalized_yaml,
        'content_hash': hash_value,
        'file_hash': hash_value.removeprefix('sha256:'),
        'skill_ids': member_skill_ids(normalized_yaml),
    }
