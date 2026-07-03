"""分类管理 CLI 命令

提供分类的查询和创建功能。
"""

from dataclasses import dataclass
from typing import Annotated

import cappa

from backend.cli_tools.cli.common import (
    console,
    print_error,
    print_header,
    print_info,
    print_success,
)
from backend.database.db import async_db_session


@cappa.command(name='list', help='列出所有分类', default_long=True)
@dataclass
class CategoryList:
    """列出所有分类"""

    async def __call__(self) -> None:
        from sqlalchemy import select

        from backend.app.marketplace.model.marketplace_category import MarketplaceCategory

        print_header('技能市场分类')

        async with async_db_session() as db:
            stmt = select(MarketplaceCategory).order_by(MarketplaceCategory.sort_order)
            result = await db.execute(stmt)
            categories = result.scalars().all()

        if not categories:
            print_info('暂无分类，请使用 fba category create 创建')
            return

        console.print()
        console.print(f'  {"slug":<20} {"名称":<15} {"图标":<5} {"父分类":<15}')
        console.print('  ' + '-' * 60)
        for cat in categories:
            icon = cat.icon or ''
            parent = cat.parent_slug or '-'
            console.print(f'  {cat.slug:<20} {cat.name:<15} {icon:<5} {parent:<15}')

        console.print()
        print_info(f'共 {len(categories)} 个分类')


@cappa.command(name='create', help='创建分类', default_long=True)
@dataclass
class CategoryCreate:
    """创建新分类"""

    slug: Annotated[
        str,
        cappa.Arg(help='分类标识（英文，如 content-creation）'),
    ]
    name: Annotated[
        str,
        cappa.Arg(help='分类名称（中文，如 内容创作）'),
    ]
    icon: Annotated[
        str | None,
        cappa.Arg(
            short='-i',
            help='emoji 图标（如 📝）',
        ),
    ] = None
    parent: Annotated[
        str | None,
        cappa.Arg(
            short='-p',
            help='父分类标识',
        ),
    ] = None
    order: Annotated[
        int,
        cappa.Arg(
            short='-o',
            default=0,
            help='排序顺序',
        ),
    ] = 0

    async def __call__(self) -> None:
        from sqlalchemy import select

        from backend.app.marketplace.model.marketplace_category import MarketplaceCategory

        print_header('创建分类')

        async with async_db_session.begin() as db:
            # 检查是否已存在
            stmt = select(MarketplaceCategory).where(MarketplaceCategory.slug == self.slug)
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                print_error(f'分类 {self.slug} 已存在')
                raise cappa.Exit(code=1)

            # 如果有父分类，验证父分类存在
            if self.parent:
                stmt = select(MarketplaceCategory).where(MarketplaceCategory.slug == self.parent)
                result = await db.execute(stmt)
                parent_cat = result.scalar_one_or_none()
                if not parent_cat:
                    print_error(f'父分类 {self.parent} 不存在')
                    raise cappa.Exit(code=1)

            # 创建分类
            category = MarketplaceCategory(
                slug=self.slug,
                name=self.name,
                icon=self.icon,
                parent_slug=self.parent,
                sort_order=self.order,
            )
            db.add(category)
            await db.flush()

        console.print()
        print_success('分类创建成功!')
        print_success(f'标识: {self.slug}')
        print_success(f'名称: {self.name}')
        if self.icon:
            print_success(f'图标: {self.icon}')


@cappa.command(name='get', help='获取分类信息', default_long=True)
@dataclass
class CategoryGet:
    """获取分类信息"""

    slug: Annotated[
        str,
        cappa.Arg(help='分类标识'),
    ]

    async def __call__(self) -> None:
        from sqlalchemy import select

        from backend.app.marketplace.model.marketplace_category import MarketplaceCategory

        async with async_db_session() as db:
            stmt = select(MarketplaceCategory).where(MarketplaceCategory.slug == self.slug)
            result = await db.execute(stmt)
            category = result.scalar_one_or_none()

        if not category:
            print_error(f'分类 {self.slug} 不存在')
            raise cappa.Exit(code=1)

        print_header('分类信息')
        console.print(f'  标识:   [cyan]{category.slug}[/]')
        console.print(f'  名称:   [cyan]{category.name}[/]')
        console.print(f'  图标:   [cyan]{category.icon or "-"}[/]')
        console.print(f'  父分类: [cyan]{category.parent_slug or "-"}[/]')
        console.print(f'  排序:   [cyan]{category.sort_order}[/]')


@cappa.command(help='分类管理命令')
@dataclass
class Category:
    """分类管理命令组"""

    subcmd: cappa.Subcommands[CategoryList | CategoryCreate | CategoryGet]
