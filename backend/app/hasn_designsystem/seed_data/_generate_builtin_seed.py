"""DS-P6 官方内置设计系统 seed 生成器（一次性开发工具，产物 builtin_design_systems.json 入库）。

从父仓 `external/open-design/design-systems/`（Apache-2.0，NOTICE 保留）精选若干高分（评分均
≥ good/80、契约合规）、品类多样的成熟品牌设计系统，把每套**预先算好**的四层 token 契约 bundle
（tokens.css 真源 + 派生 design-tokens.json/tailwind-v4.css + 评分报告 + 组件样例 + 中文 DESIGN.md）
读出来，落成一份**已提交**的 seed JSON。云端 `builtin_seeding_service.seed_builtin_design_systems`
读这份 JSON 做 INSERT-only 幂等播种（owner='system'、is_builtin=True、source_kind='seed'）。

> 为什么读预算产物而非重跑 Rust 契约引擎：open-design 每个目录已含 design-tokens.json /
> token-contract.report.json（评分 100/excellent），与 daemon 本地 `hasn-designsystem-core`
> 同口径四层契约。seed 是「官方打底库」，直接采用其权威产物即可，无需重算。

用法（在本机一次性运行，产物提交进仓库）：
    python backend/app/hasn_designsystem/seed_data/_generate_builtin_seed.py \
        --open-design /Users/mac/openclaw-workspace/huanxing/huanxing-project/external/open-design/design-systems
"""

from __future__ import annotations

import argparse
import json
import operator

from pathlib import Path
from typing import Any

# 精选 15 套：品类覆盖 ai/saas/developer/minimal/fintech/ecommerce/media/creative/social。
# (open-design 目录 slug, 唤星归一分类)。全部评分 100/excellent、含 DESIGN-zh.md。
CURATED: list[tuple[str, str]] = [
    ('openai', 'ai'),
    ('cohere', 'ai'),
    ('notion', 'saas'),
    ('slack', 'saas'),
    ('github', 'developer'),
    ('vercel', 'minimal'),
    ('stripe', 'fintech'),
    ('coinbase', 'fintech'),
    ('airbnb', 'ecommerce'),
    ('shopify', 'ecommerce'),
    ('spotify', 'media'),
    ('apple', 'media'),
    ('figma', 'creative'),
    ('airtable', 'creative'),
    ('discord', 'social'),
]

# 评分准入闸（doc12 P6 验收：评分均 ≥ good/80）。
MIN_SCORE = 80


def _read_text(path: Path) -> str | None:
    return path.read_text(encoding='utf-8') if path.exists() else None


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def _design_md(ds_dir: Path) -> str | None:
    """优先中文 DESIGN-zh.md，回落 DESIGN.md（创意说明部分）。"""
    return _read_text(ds_dir / 'DESIGN-zh.md') or _read_text(ds_dir / 'DESIGN.md')


def build_entry(ds_dir: Path, slug: str, category: str) -> dict[str, Any]:
    manifest = _read_json(ds_dir / 'manifest.json') or {}
    design_tokens = _read_json(ds_dir / 'design-tokens.json') or {}
    report = _read_json(ds_dir / 'source' / 'token-contract.report.json') or {}
    summary = design_tokens.get('summary') or {}

    score = summary.get('score')
    grade = summary.get('grade')
    if not isinstance(score, int) or score < MIN_SCORE:
        raise ValueError(f'{slug}: 评分 {score!r} 未达准入闸 {MIN_SCORE}')

    content = {
        'tokens_css': _read_text(ds_dir / 'tokens.css'),
        'design_tokens_json': design_tokens or None,
        'tailwind_css': _read_text(ds_dir / 'tailwind-v4.css'),
        'design_md': _design_md(ds_dir),
        'components_html': _read_text(ds_dir / 'components.html'),
        'components_manifest_json': _read_json(ds_dir / 'components.manifest.json'),
        'token_contract_report_json': report or None,
    }
    return {
        'slug': slug,
        'name': manifest.get('name') or slug.title(),
        'category': category,
        'source_kind': 'seed',
        'score': score,
        'grade': grade,
        'recommend_rebuild': bool(summary.get('recommendRebuild', False)),
        'content': content,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='生成官方内置设计系统 seed JSON')
    parser.add_argument('--open-design', required=True, help='open-design/design-systems 目录路径')
    parser.add_argument(
        '--out',
        default=str(Path(__file__).parent / 'builtin_design_systems.json'),
        help='输出 JSON 路径（默认同目录 builtin_design_systems.json）',
    )
    args = parser.parse_args()

    root = Path(args.open_design)
    entries: list[dict[str, Any]] = []
    for slug, category in CURATED:
        ds_dir = root / slug
        if not ds_dir.is_dir():
            print(f'[skip] 目录不存在: {slug}')
            continue
        entries.append(build_entry(ds_dir, slug, category))
        print(f'[ok]   {slug:12s} {category:10s} score={entries[-1]["score"]}')

    entries.sort(key=operator.itemgetter('slug'))
    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=False) + '\n',
        encoding='utf-8',
    )
    cats = sorted({e['category'] for e in entries})
    print(f'\n生成 {len(entries)} 套 → {out_path}')
    print(f'分类覆盖({len(cats)}): {", ".join(cats)}')


if __name__ == '__main__':
    main()
