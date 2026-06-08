"""
Search Service for Marketplace Skills

Provides search and browse functionality for skills.
"""
import json

from typing import Any

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.model.marketplace_category import MarketplaceCategory
from backend.app.marketplace.model.marketplace_skill import MarketplaceSkill
from backend.app.marketplace.model.marketplace_template import MarketplaceTemplate
from backend.app.marketplace.service.resource_id import PUBLIC_VISIBILITY, PUBLISHED_STATUS


class SearchService:
    """Search service for marketplace skills"""

    async def search_skills(
        self,
        db: AsyncSession,
        keyword: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        source_type: str | None = None,
        namespace: str | None = None,
        lang: str = 'zh',
        page: int = 1,
        page_size: int = 20,
        sort_by: str = 'popular',
    ) -> dict[str, Any]:
        """
        Search skills with filters

        Args:
            db: Database session
            keyword: Search keyword
            category: Category filter
            tags: Tag filters
            source_type: Source type filter
            namespace: Namespace filter
            lang: Language (zh/en)
            page: Page number (1-indexed)
            page_size: Items per page
            sort_by: Sort method (popular/latest/downloads/stars)

        Returns:
            Dict with skills list and pagination info
        """
        # Build query
        query = select(MarketplaceSkill).where(
            MarketplaceSkill.status == PUBLISHED_STATUS,
            MarketplaceSkill.visibility == PUBLIC_VISIBILITY,
        )

        query = self._apply_skill_filters(
            query,
            keyword=keyword,
            category=category,
            tags=tags,
            source_type=source_type,
            namespace=namespace,
            lang=lang,
        )

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        result = await db.execute(count_query)
        total = result.scalar() or 0

        query = self._order_skills(query, sort_by)

        # Pagination
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)

        # Execute query
        result = await db.execute(query)
        skills = result.scalars().all()

        # Format results
        items = [self._format_skill(skill, lang) for skill in skills]

        return {
            'items': items,
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': (total + page_size - 1) // page_size
        }

    def _apply_skill_filters(
        self,
        query: Any,
        *,
        keyword: str | None,
        category: str | None,
        tags: list[str] | None,
        source_type: str | None,
        namespace: str | None,
        lang: str,
    ) -> Any:
        if keyword:
            query = query.where(self._skill_keyword_clause(keyword, lang))
        if category:
            query = query.where(MarketplaceSkill.category == category)
        if source_type:
            query = query.where(MarketplaceSkill.source_type == source_type)
        if namespace:
            query = query.where(MarketplaceSkill.namespace == namespace)
        if tags:
            for tag in tags:
                query = query.where(or_(
                    MarketplaceSkill.tags.contains(tag),
                    MarketplaceSkill.tags_en.contains(tag),
                    MarketplaceSkill.tags_zh.contains(tag),
                ))
        return query

    def _skill_keyword_clause(self, keyword: str, lang: str) -> Any:
        localized_fields = (
            (MarketplaceSkill.name_zh, MarketplaceSkill.description_zh)
            if lang == 'zh'
            else (MarketplaceSkill.name_en, MarketplaceSkill.description_en)
        )
        return or_(
            localized_fields[0].ilike(f'%{keyword}%'),
            localized_fields[1].ilike(f'%{keyword}%'),
            MarketplaceSkill.name.ilike(f'%{keyword}%'),
            MarketplaceSkill.skill_id.ilike(f'%{keyword}%'),
            MarketplaceSkill.tags.ilike(f'%{keyword}%'),
            MarketplaceSkill.tags_en.ilike(f'%{keyword}%'),
            MarketplaceSkill.tags_zh.ilike(f'%{keyword}%'),
        )

    def _order_skills(self, query: Any, sort_by: str) -> Any:
        if sort_by == 'latest':
            return query.order_by(desc(MarketplaceSkill.created_time))
        if sort_by == 'downloads':
            return query.order_by(desc(MarketplaceSkill.download_count))
        if sort_by == 'stars':
            return query.order_by(desc(MarketplaceSkill.star_count))
        return query.order_by(
            desc(MarketplaceSkill.download_count + MarketplaceSkill.star_count * 10)
        )

    async def get_skill_detail(
        self,
        db: AsyncSession,
        skill_id: str,
        lang: str = 'zh'
    ) -> dict[str, Any] | None:
        """
        Get skill detail by namespaced ID or bare slug.

        Accepts either the full 'namespace/slug' ID (e.g.
        'clawhub/wscats/university-applications') or a bare slug
        (e.g. 'university-applications'). Skill bundles and template
        "内置能力" store member skills by bare slug (hermes member naming),
        so detail lookup must resolve those too — otherwise a real
        marketplace skill is wrongly shown as missing.

        Args:
            db: Database session
            skill_id: Full 'namespace/slug' ID or a bare slug
            lang: Language (zh/en)

        Returns:
            Skill detail dict or None
        """
        published_public = and_(
            MarketplaceSkill.status == PUBLISHED_STATUS,
            MarketplaceSkill.visibility == PUBLIC_VISIBILITY,
        )

        if '/' in skill_id:
            namespace, slug = skill_id.rsplit('/', 1)
            query = select(MarketplaceSkill).where(
                and_(
                    MarketplaceSkill.namespace == namespace,
                    MarketplaceSkill.slug == slug,
                    published_public,
                )
            )
        else:
            # 裸 slug：slug 基本唯一；偶有重名时按下载量取最热的一个，保证确定性。
            query = (
                select(MarketplaceSkill)
                .where(
                    and_(
                        MarketplaceSkill.slug == skill_id,
                        published_public,
                    )
                )
                .order_by(desc(MarketplaceSkill.download_count))
                .limit(1)
            )

        result = await db.execute(query)
        skill = result.scalar_one_or_none()

        if not skill:
            return None

        return self._format_skill(skill, lang, detailed=True)

    async def get_popular_skills(
        self,
        db: AsyncSession,
        lang: str = 'zh',
        limit: int = 10
    ) -> list[dict[str, Any]]:
        """
        Get popular skills

        Args:
            db: Database session
            lang: Language (zh/en)
            limit: Number of skills to return

        Returns:
            List of popular skills
        """
        query = (
            select(MarketplaceSkill)
            .where(
                MarketplaceSkill.status == PUBLISHED_STATUS,
                MarketplaceSkill.visibility == PUBLIC_VISIBILITY,
            )
            .order_by(desc(MarketplaceSkill.download_count + MarketplaceSkill.star_count * 10))
            .limit(limit)
        )

        result = await db.execute(query)
        skills = result.scalars().all()

        return [self._format_skill(skill, lang) for skill in skills]

    async def get_official_skills(
        self,
        db: AsyncSession,
        lang: str = 'zh',
        limit: int = 10
    ) -> list[dict[str, Any]]:
        """
        Get official skills

        Args:
            db: Database session
            lang: Language (zh/en)
            limit: Number of skills to return

        Returns:
            List of official skills
        """
        query = (
            select(MarketplaceSkill)
            .where(
                and_(
                    MarketplaceSkill.is_official,
                    MarketplaceSkill.status == PUBLISHED_STATUS,
                    MarketplaceSkill.visibility == PUBLIC_VISIBILITY,
                )
            )
            .order_by(desc(MarketplaceSkill.created_time))
            .limit(limit)
        )

        result = await db.execute(query)
        skills = result.scalars().all()

        return [self._format_skill(skill, lang) for skill in skills]

    async def get_categories(self, db: AsyncSession, lang: str = 'zh') -> list[dict[str, Any]]:
        """
        Get all categories with skill counts

        Args:
            db: Database session
            lang: Language (zh/en)

        Returns:
            List of categories
        """
        query = (
            select(
                MarketplaceSkill.category,
                func.count(MarketplaceSkill.id).label('count')
            )
            .where(
                MarketplaceSkill.status == PUBLISHED_STATUS,
                MarketplaceSkill.visibility == PUBLIC_VISIBILITY,
            )
            .group_by(MarketplaceSkill.category)
            .order_by(desc('count'))
        )

        result = await db.execute(query)
        categories = result.all()

        return [
            {
                'category': cat.category,
                'count': cat.count
            }
            for cat in categories
        ]

    async def get_marketplace_categories(self, db: AsyncSession) -> list[dict[str, Any]]:
        """权威分类目录（slug + 中文显示名 + emoji 图标）+ 已发布技能/模板计数。

        驱动 webui **统一分类选择器**（技能 / 技能包 / 分身模板的浏览筛选 + 创建分类）。
        返回**全量已定义分类**（即便暂无内容也列出，使创建可任选分类、各页选择器一致），
        并附内容上出现但目录表缺失的孤儿分类（显示名回退为 slug）。
        每项：`{category: slug, name, icon, skill_count, template_count}`。
        """
        skill_counts_result = await db.execute(
            select(
                MarketplaceSkill.category,
                func.count(MarketplaceSkill.id).label('count'),
            )
            .where(
                MarketplaceSkill.status == PUBLISHED_STATUS,
                MarketplaceSkill.visibility == PUBLIC_VISIBILITY,
                MarketplaceSkill.category.is_not(None),
            )
            .group_by(MarketplaceSkill.category)
        )
        template_counts_result = await db.execute(
            select(
                MarketplaceTemplate.category,
                func.count(MarketplaceTemplate.id).label('count'),
            )
            .where(
                MarketplaceTemplate.status == PUBLISHED_STATUS,
                MarketplaceTemplate.visibility == PUBLIC_VISIBILITY,
                MarketplaceTemplate.category.is_not(None),
            )
            .group_by(MarketplaceTemplate.category)
        )
        skill_counts = {row.category: row.count for row in skill_counts_result.all()}
        template_counts = {row.category: row.count for row in template_counts_result.all()}

        # 权威目录：按 sort_order 升序（策展顺序），显示名取目录表 name。
        taxonomy_result = await db.execute(
            select(
                MarketplaceCategory.slug,
                MarketplaceCategory.name,
                MarketplaceCategory.icon,
            ).order_by(MarketplaceCategory.sort_order, MarketplaceCategory.slug)
        )
        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in taxonomy_result.all():
            if not row.slug or row.slug in seen:
                continue
            seen.add(row.slug)
            ordered.append(
                {
                    'category': row.slug,
                    'name': row.name or row.slug,
                    'icon': row.icon,
                    'skill_count': skill_counts.get(row.slug, 0),
                    'template_count': template_counts.get(row.slug, 0),
                }
            )

        # 孤儿分类：内容上有但目录表没有，显示名回退 slug，按内容量降序附在后面。
        orphans = [
            {
                'category': slug,
                'name': slug,
                'icon': None,
                'skill_count': skill_counts.get(slug, 0),
                'template_count': template_counts.get(slug, 0),
            }
            for slug in (set(skill_counts) | set(template_counts)) - seen
        ]
        orphans.sort(key=lambda item: (item['skill_count'] + item['template_count'], item['category']), reverse=True)
        return ordered + orphans

    def _format_skill(
        self,
        skill: MarketplaceSkill,
        lang: str = 'zh',
        *,
        detailed: bool = False,
    ) -> dict[str, Any]:
        """
        Format skill model to dict

        Args:
            skill: Skill model instance
            lang: Language (zh/en)
            detailed: Include detailed info

        Returns:
            Formatted skill dict
        """
        # Choose language fields
        name = skill.name_zh if lang == 'zh' else skill.name_en
        description = skill.description_zh if lang == 'zh' else skill.description_en

        # Fallback to other language if not available
        if not name:
            name = (skill.name_en if lang == 'zh' else skill.name_zh) or skill.name
        if not description:
            description = skill.description_en if lang == 'zh' else skill.description_zh

        tags = skill.tags_zh if lang == 'zh' else skill.tags_en
        if not tags:
            tags = skill.tags

        result = {
            'skill_id': skill.skill_id,
            'namespace': skill.namespace,
            'slug': skill.slug,
            'name': name,
            'description': description,
            'icon_url': skill.icon_url,
            'emoji': skill.emoji,
            'author_name': skill.author_name,
            'category': skill.category,
            'tags': tags,
            'tags_en': skill.tags_en,
            'tags_zh': skill.tags_zh,
            'download_count': skill.download_count,
            'star_count': skill.star_count,
            'is_official': skill.is_official,
            'pricing_type': skill.pricing_type,
            'price': float(skill.price) if skill.price else 0,
            'source_type': skill.source_type,
            'status': skill.status,
            'visibility': skill.visibility,
            'user_id': skill.user_id,
            'hasn_id': skill.hasn_id,
            'created_time': skill.created_time.isoformat() if skill.created_time else None,
            'updated_time': skill.updated_time.isoformat() if skill.updated_time else None
        }

        if detailed:
            # 正文（readme）：按请求语言取，缺失回退另一语言；files 仅名称+大小。
            readme = skill.body_zh if lang == 'zh' else skill.body_en
            if not readme:
                readme = skill.body_en if lang == 'zh' else skill.body_zh
            files = self._parse_files(skill.files)
            result.update({
                'source_repo_url': skill.source_repo_url,
                'source_repo_path': skill.source_repo_path,
                'repo_path': skill.repo_path,
                'git_commit_hash': skill.git_commit_hash,
                'synced_at': skill.synced_at.isoformat() if skill.synced_at else None,
                'translated_at': skill.translated_at.isoformat() if skill.translated_at else None,
                'readme': readme,
                'readme_en': skill.body_en,
                'readme_zh': skill.body_zh,
                'files': files,
                'file_count': len(files),
            })

        return result

    @staticmethod
    def _parse_files(raw: str | None) -> list[dict[str, Any]]:
        """Parse the stored files JSON into a list of {path, size}; [] on any error.

        Only path + size are surfaced — file contents are never stored or returned.
        """
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        files: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict) and item.get('path'):
                files.append({'path': str(item['path']), 'size': item.get('size')})
        return files

    async def search_templates(
        self,
        db: AsyncSession,
        keyword: str | None = None,
        category: str | None = None,
        source_type: str | None = None,
        namespace: str | None = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = 'popular',
    ) -> dict[str, Any]:
        query = select(MarketplaceTemplate).where(
            MarketplaceTemplate.status == PUBLISHED_STATUS,
            MarketplaceTemplate.visibility == PUBLIC_VISIBILITY,
            # 技能包（skill_pack）也是 marketplace_template 行，但有独立浏览页（/skill-packs），
            # 不应混进「模板」浏览（实施/92-UI 修泄漏）。
            MarketplaceTemplate.template_type != 'skill_pack',
        )
        if keyword:
            pattern = f'%{keyword}%'
            query = query.where(
                or_(
                    MarketplaceTemplate.name.ilike(pattern),
                    MarketplaceTemplate.name_zh.ilike(pattern),
                    MarketplaceTemplate.name_en.ilike(pattern),
                    MarketplaceTemplate.description.ilike(pattern),
                    MarketplaceTemplate.description_zh.ilike(pattern),
                    MarketplaceTemplate.description_en.ilike(pattern),
                    MarketplaceTemplate.template_id.ilike(pattern),
                    MarketplaceTemplate.tags.ilike(pattern),
                    MarketplaceTemplate.category.ilike(pattern),
                )
            )
        if category:
            query = query.where(MarketplaceTemplate.category == category)
        if source_type:
            query = query.where(MarketplaceTemplate.source_type == source_type)
        if namespace:
            query = query.where(MarketplaceTemplate.namespace == namespace)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        if sort_by == 'latest':
            query = query.order_by(desc(MarketplaceTemplate.created_time))
        elif sort_by == 'downloads':
            query = query.order_by(desc(MarketplaceTemplate.download_count))
        else:
            query = query.order_by(desc(MarketplaceTemplate.download_count), desc(MarketplaceTemplate.id))

        result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
        templates = result.scalars().all()
        return {
            'items': [self._format_template(template) for template in templates],
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': (total + page_size - 1) // page_size,
        }

    async def get_trending_templates(self, db: AsyncSession, limit: int = 10) -> list[dict[str, Any]]:
        result = await db.execute(
            select(MarketplaceTemplate)
            .where(
                MarketplaceTemplate.status == PUBLISHED_STATUS,
                MarketplaceTemplate.visibility == PUBLIC_VISIBILITY,
            )
            .order_by(desc(MarketplaceTemplate.download_count), desc(MarketplaceTemplate.id))
            .limit(limit)
        )
        return [self._format_template(template) for template in result.scalars().all()]

    def _format_template(self, template: MarketplaceTemplate) -> dict[str, Any]:
        return {
            'template_id': template.template_id,
            'namespace': template.namespace,
            'slug': template.slug,
            'name': template.name or template.name_zh or template.name_en,
            'name_pool': [n.strip() for n in (template.name_pool or '').split(',') if n.strip()],
            'description': template.description or template.description_zh or template.description_en,
            'icon_url': template.icon_url,
            'emoji': template.emoji,
            'author_name': template.author_name,
            'category': template.category,
            'tags': template.tags,
            'download_count': template.download_count,
            'is_official': template.is_official,
            'pricing_type': template.pricing_type,
            'price': float(template.price) if template.price else 0,
            'source_type': template.source_type,
            'status': template.status,
            'visibility': template.visibility,
            'user_id': template.user_id,
            'hasn_id': template.hasn_id,
            'skill_dependencies': template.skill_dependencies,
            'created_time': template.created_time.isoformat() if template.created_time else None,
            'updated_time': template.updated_time.isoformat() if template.updated_time else None,
        }


# Singleton instance
search_service = SearchService()
