"""技能包成员命名归一存量回填（实施/92 D-NAMING）。

把存量里以「裸 slug」存储的技能包成员回填为完整 `namespace/slug` id：
  1) marketplace_template_version(skill_pack).hermes_yaml 的 `skills:` 列表；
  2) hasn_agents.skills（曾装包时并入的裸 slug 成员）。

归一规则（best-effort，零 fake，不猜测）：
  - 完整 id（含 '/'）：原样保留（不丢弃，即便当前查不到对应技能）。
  - 裸 slug：当且仅当市场里存在**唯一**「已发布 + 公开」技能时回填为完整 id；
    命中 0 个或多个（重名）→ 保留裸 slug（由运行期边界兜底，安装期硬报错）。

幂等：完整 id 不动，仅裸 slug 可能变化，可重复执行。hermes_yaml 变化重算
content_hash（触发 daemon re-provision）；hasn_agents.skills 变化 bump profile_revision。

> 注：hub 公共包在下次 ClawHub 同步时会自动按完整 id 重写（upsert_skill_pack 已归一），
> 用户包在重新发布时自动归一；本脚本用于一次性收敛存量、避免等待自然同步。

运行：
    cd hasn-cloud-backend && uv run python backend/sql/marketplace/migrations/2026-06-06-skill-bundle-member-full-id-backfill.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import asyncpg
import yaml

# 本地开发库；生产执行时按 117.72.92.229:5432 等调整。
DB_HOST = '127.0.0.1'
DB_PORT = 15432
DB_USER = 'mac'
DB_NAME = 'huanxing'


def _content_hash(value: str) -> str:
    return f'sha256:{hashlib.sha256(value.encode("utf-8")).hexdigest()}'


async def _resolve_member(conn: asyncpg.Connection, member: str) -> str:
    """裸 slug → 唯一已发布公开技能的完整 id；完整 id / 不可唯一解析 → 原样返回。"""
    member = (member or '').strip().strip('/')
    if not member or '/' in member:
        return member
    rows = await conn.fetch(
        """SELECT DISTINCT namespace FROM marketplace_skill
            WHERE slug = $1 AND namespace IS NOT NULL
              AND status = 'published' AND visibility = 'public'""",
        member,
    )
    if len(rows) == 1:
        return f"{rows[0]['namespace']}/{member}"
    return member  # 0 个或重名 → 保留裸 slug（零 fake，不猜测）


def _dedup_preserve(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


async def _backfill_hermes_yaml(conn: asyncpg.Connection) -> int:
    rows = await conn.fetch(
        """SELECT v.id, v.hermes_yaml
             FROM marketplace_template_version v
             JOIN marketplace_template t ON t.template_id = v.template_id
            WHERE t.template_type = 'skill_pack' AND v.hermes_yaml IS NOT NULL"""
    )
    changed = 0
    for r in rows:
        try:
            spec = yaml.safe_load(r['hermes_yaml'])
        except yaml.YAMLError:
            continue
        if not isinstance(spec, dict) or not isinstance(spec.get('skills'), list):
            continue
        original = [s for s in spec['skills'] if isinstance(s, str)]
        if len(original) != len(spec['skills']):
            continue  # 含非字符串成员，跳过不破坏
        resolved = _dedup_preserve([await _resolve_member(conn, s) for s in original])
        if resolved == original:
            continue
        spec['skills'] = resolved
        normalized = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False).strip() + '\n'
        h = _content_hash(normalized)
        await conn.execute(
            """UPDATE marketplace_template_version
                  SET hermes_yaml = $1, content_hash = $2, file_hash = $3, updated_time = now()
                WHERE id = $4""",
            normalized, h, h.removeprefix('sha256:'), r['id'],
        )
        changed += 1
    return changed


async def _backfill_agent_skills(conn: asyncpg.Connection) -> int:
    rows = await conn.fetch(
        "SELECT id, skills FROM hasn_agents WHERE skills IS NOT NULL"
    )
    changed = 0
    for r in rows:
        raw = r['skills']
        skills = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(skills, list):
            continue
        original = [s for s in skills if isinstance(s, str)]
        if len(original) != len(skills):
            continue
        resolved = _dedup_preserve([await _resolve_member(conn, s) for s in original])
        if resolved == original:
            continue
        await conn.execute(
            """UPDATE hasn_agents
                  SET skills = $1::jsonb,
                      profile_revision = COALESCE(profile_revision, 1) + 1,
                      updated_time = now()
                WHERE id = $2""",
            json.dumps(resolved, ensure_ascii=False), r['id'],
        )
        changed += 1
    return changed


async def main() -> None:
    conn = await asyncpg.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, database=DB_NAME)
    try:
        packs = await _backfill_hermes_yaml(conn)
        agents = await _backfill_agent_skills(conn)
    finally:
        await conn.close()
    print(f'backfill done: skill_pack_versions_rewritten={packs} agents_skills_rewritten={agents}')


if __name__ == '__main__':
    asyncio.run(main())
