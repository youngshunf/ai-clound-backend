"""Frontend code generator - main orchestrator."""

import re
from pathlib import Path

from pydantic.alias_generators import to_pascal
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.path_conf import BASE_PATH
from backend.database.db import async_db_session
from backend.plugin.code_generator.crud.crud_business import gen_business_dao
from backend.plugin.code_generator.crud.crud_column import gen_column_dao
from backend.plugin.code_generator.crud.crud_gen import gen_dao
from backend.plugin.code_generator.frontend.component_selector import (
    select_form_component,
    select_search_component,
    select_table_renderer,
    should_display_in_table,
    should_include_in_form,
    should_include_in_search,
)
from backend.plugin.code_generator.frontend.type_mapper import sql_to_typescript
from backend.plugin.code_generator.model import GenBusiness, GenColumn
from backend.plugin.code_generator.parser.sql_parser import ColumnInfo, TableInfo, sql_parser
from backend.plugin.code_generator.utils.gen_template import gen_template
from backend.utils.console import console


class FrontendGenerator:
    """Frontend code generator."""

    def __init__(self):
        """Initialize generator."""
        self.template_env = gen_template.env

    async def generate_from_sql(
        self,
        sql_file: Path,
        app: str,
        module: str | None = None,
        output_dir: Path | None = None,
        force: bool = False,
    ) -> None:
        """
        Generate frontend code from SQL file.

        :param sql_file: Path to SQL file
        :param app: Application name
        :param module: Module name (optional)
        :param output_dir: Output directory (auto-detected if None)
        :param force: Force overwrite existing files
        """
        # Read and parse SQL file
        sql_content = sql_file.read_text(encoding='utf-8')
        table_info = sql_parser.parse(sql_content)

        # Use module name or derive from table name
        if not module:
            module = table_info.name.replace('_', '-')

        # Detect frontend directory
        if not output_dir:
            output_dir = self._detect_frontend_dir()

        # Generate code
        await self._generate_code(table_info, app, module, output_dir, force)

    async def generate_from_table_info(
        self,
        table_info: TableInfo,
        app: str,
        module: str | None = None,
        output_dir: Path | None = None,
        force: bool = False,
    ) -> None:
        """
        Generate frontend code from TableInfo object.

        :param table_info: TableInfo object from parser
        :param app: Application name
        :param module: Module name (optional)
        :param output_dir: Output directory (auto-detected if None)
        :param force: Force overwrite existing files
        """
        # Use module name or derive from table name
        if not module:
            module = table_info.name.replace('_', '-')

        # Detect frontend directory
        if not output_dir:
            output_dir = self._detect_frontend_dir()

        # Generate code
        await self._generate_code(table_info, app, module, output_dir, force)

    async def generate_from_db(
        self,
        business_id: int,
        app: str,
        module: str | None = None,
        output_dir: Path | None = None,
        force: bool = False,
    ) -> None:
        """
        Generate frontend code from gen_business/gen_column tables.

        :param business_id: GenBusiness ID
        :param app: Application name
        :param module: Module name (optional)
        :param output_dir: Output directory (auto-detected if None)
        :param force: Force overwrite existing files
        """
        # Query gen_business and gen_column
        async with async_db_session() as db:
            business = await gen_business_dao.get(db, business_id)
            if not business:
                raise ValueError(f'GenBusiness not found: id={business_id}')
            
            columns = await gen_column_dao.get_all_by_business(db, business_id)

        # Convert to TableInfo
        table_info = self._convert_gen_to_table_info(business, list(columns))

        # Use module name or derive from table name
        if not module:
            module = business.table_name.replace('_', '-')

        # Detect frontend directory
        if not output_dir:
            output_dir = self._detect_frontend_dir()

        # Generate code
        await self._generate_code(table_info, app, module, output_dir, force)

    async def generate_from_db_introspection(
        self,
        table: str,
        db_schema: str,
        app: str,
        module: str | None = None,
        output_dir: Path | None = None,
        force: bool = False,
        db: AsyncSession | None = None,
    ) -> None:
        """
        Generate frontend code from database introspection (direct DB metadata).

        :param table: Table name
        :param db_schema: Database schema name
        :param app: Application name
        :param module: Module name (optional)
        :param output_dir: Output directory (auto-detected if None)
        :param force: Force overwrite existing files
        :param db: Database session
        """
        if not db:
            raise ValueError('Database session is required for DB introspection')

        # Query database metadata
        table_data = await gen_dao.get_table(db, db_schema, table)
        if not table_data:
            raise ValueError(f"Table '{table}' not found in schema '{db_schema}'")

        columns_data = await gen_dao.get_all_columns(db, db_schema, table)

        # Convert to TableInfo
        table_info = self._convert_db_to_table_info(table_data, columns_data)

        # Use module name or derive from table name
        if not module:
            module = table.replace('_', '-')

        # Detect frontend directory
        if not output_dir:
            output_dir = self._detect_frontend_dir()

        # Generate code
        await self._generate_code(table_info, app, module, output_dir, force)

    def _convert_gen_to_table_info(self, business: GenBusiness, columns: list[GenColumn]) -> TableInfo:
        """
        Convert GenBusiness/GenColumn to TableInfo.

        :param business: GenBusiness object
        :param columns: List of GenColumn objects
        :return: TableInfo object
        """
        # Create ColumnInfo list
        column_infos = []
        for col in columns:
            column_info = ColumnInfo(
                name=col.name,
                type=col.type.upper(),  # SQLA type -> uppercase for consistency
                length=col.length if col.length > 0 else None,
                nullable=col.is_nullable,
                comment=col.comment,
                is_primary_key=col.is_pk,
            )
            column_infos.append(column_info)

        return TableInfo(
            name=business.table_name,
            comment=business.table_comment or business.doc_comment,
            columns=column_infos,
        )

    def _convert_db_to_table_info(self, table_data: dict, columns_data: list[dict]) -> TableInfo:
        """
        Convert database metadata to TableInfo.

        :param table_data: Table metadata from database
        :param columns_data: Columns metadata from database
        :return: TableInfo object
        """
        # Create columns
        columns = []
        for col_data in columns_data:
            column = ColumnInfo(
                name=col_data['column_name'],
                type=col_data['column_type'].split('(')[0].upper(),  # Extract base type
                length=self._extract_length(col_data['column_type']),
                nullable=bool(col_data['is_nullable']),
                comment=col_data.get('column_comment'),
                is_primary_key=bool(col_data.get('is_pk', 0)),
            )
            columns.append(column)

        return TableInfo(
            name=table_data['table_name'],
            comment=table_data.get('table_comment'),
            columns=columns,
        )

    def _extract_length(self, column_type: str) -> int | None:
        """Extract length from column type string."""
        match = re.search(r'\((\d+)\)', column_type)
        return int(match.group(1)) if match else None

    async def _generate_code(
        self,
        table_info: TableInfo,
        app: str,
        module: str,
        output_dir: Path,
        force: bool,
    ) -> None:
        """
        Core generation logic.

        :param table_info: TableInfo object
        :param app: Application name
        :param module: Module name
        :param output_dir: Output directory (project-level, will be normalized to absolute path)
        :param force: Force overwrite
        """
        # 统一将输出目录转换为绝对路径，避免依赖当前工作目录
        # BASE_PATH 是后端 backend 根目录，其父级是 clound-backend 项目根
        if not output_dir.is_absolute():
            output_dir = (BASE_PATH.parent / output_dir).resolve()

        console.print(
            f"Generating frontend code for [cyan]{table_info.name}[/cyan] "
            f"into [magenta]{output_dir}[/magenta]...",
            style='white',
        )

        # Prepare template variables
        vars_dict = self._prepare_template_vars(table_info, app, module)

        # Define output paths（约定 output_dir 为 clound-frontend 根目录）
        src_dir = output_dir / 'apps' / 'web-antd' / 'src'
        views_dir = src_dir / 'views' / app / module  # views/app/module/
        api_dir = src_dir / 'api' / app  # api/app/
        api_file = api_dir / f'{module}.ts'  # api/app/module.ts

        # Create directories
        views_dir.mkdir(parents=True, exist_ok=True)
        api_dir.mkdir(parents=True, exist_ok=True)

        # Generate Vue component
        if force or not (views_dir / 'index.vue').exists():
            console.print(f'  Creating [cyan]{views_dir / "index.vue"}[/cyan]')
            vue_template = self.template_env.get_template('vue/index.vue.jinja')
            vue_content = await vue_template.render_async(**vars_dict)
            (views_dir / 'index.vue').write_text(vue_content, encoding='utf-8')
        else:
            console.print(f'  [yellow]Skipping[/yellow] [dim]{views_dir / "index.vue"}[/dim] (already exists)')

        # Generate data.ts
        if force or not (views_dir / 'data.ts').exists():
            console.print(f'  Creating [cyan]{views_dir / "data.ts"}[/cyan]')
            data_template = self.template_env.get_template('vue/data.ts.jinja')
            data_content = await data_template.render_async(**vars_dict)
            (views_dir / 'data.ts').write_text(data_content, encoding='utf-8')
        else:
            console.print(f'  [yellow]Skipping[/yellow] [dim]{views_dir / "data.ts"}[/dim] (already exists)')

        # Generate API file
        if force or not api_file.exists():
            console.print(f'  Creating [cyan]{api_file}[/cyan]')
            await self._generate_or_update_api(api_file, vars_dict, force=True)
        else:
            console.print(f'  [yellow]Skipping[/yellow] [dim]{api_file}[/dim] (already exists)')

        # 注：不再生成前端路由文件，菜单由后端 sys_menu 表动态控制
        # 前端通过 import.meta.glob('../views/**/*.vue') 自动扫描组件

        console.print('[green]Frontend code generated successfully![/green]')

    def _prepare_template_vars(self, table_info: TableInfo, app: str, module: str) -> dict:
        """
        Prepare template variables.

        :param table_info: TableInfo object
        :param app: Application name
        :param module: Module name
        :return: Template variables dictionary
        """
        class_name = to_pascal(table_info.name)

        # Filter columns for different purposes
        display_columns = [col for col in table_info.columns if should_display_in_table(col)]
        form_columns = [col for col in table_info.columns if should_include_in_form(col)]
        search_columns = [col for col in table_info.columns if should_include_in_search(col)]

        # Prepare column metadata
        columns_meta = []
        for col in table_info.columns:
            # 简化字段标签：去掉括号中的说明部分
            raw_comment = col.comment or col.name
            simple_label = re.split(r'[\(\uff08]', raw_comment)[0].strip()
            
            col_meta = {
                'name': col.name,
                'type': col.type,
                'ts_type': sql_to_typescript(col.type),
                'comment': simple_label,
                'full_comment': raw_comment,  # 保留完整注释供其他用途
                'nullable': col.nullable,
                'is_primary_key': col.is_primary_key,
                'form_component': select_form_component(col),
                'table_renderer': select_table_renderer(col),
                'search_component': select_search_component(col),
                'display_in_table': should_display_in_table(col),
                'include_in_form': should_include_in_form(col),
                'include_in_search': should_include_in_search(col),
            }
            columns_meta.append(col_meta)

        return {
            'app_name': app,
            'module_name': module,
            'table_name': table_info.name,
            'class_name': class_name,
            'schema_name': class_name,
            'doc_comment': table_info.comment or class_name,
            'table_comment': table_info.comment or f'{class_name} Table',
            'columns': columns_meta,
            'display_columns': [c for c in columns_meta if c['display_in_table']],
            'form_columns': [c for c in columns_meta if c['include_in_form']],
            'search_columns': [c for c in columns_meta if c['include_in_search']],
            'api_path': f'/api/v1/{app}/{module.replace("_", "/")}s',
            'permission_prefix': table_info.name.replace('_', ':'),
        }

    async def _generate_or_update_api(self, api_file: Path, vars_dict: dict, force: bool = False) -> None:
        """
        Generate API file.
        
        每个模块生成独立的 API 文件: api/app/module.ts
        """
        api_template = self.template_env.get_template('typescript/api.ts.jinja')
        new_api_content = await api_template.render_async(**vars_dict)

        if api_file.exists() and not force:
            console.print(f'    [yellow]API file already exists, skipping: {api_file}[/yellow]')
            return
        
        # Create directory and write file
        api_file.parent.mkdir(parents=True, exist_ok=True)
        api_file.write_text(new_api_content, encoding='utf-8')

    def _detect_frontend_dir(self) -> Path:
        """
        Auto-detect clound-frontend directory using absolute paths.

        优先基于后端 BASE_PATH（backend 根目录）向上推导，以避免依赖运行时 CWD。

        :return: Frontend directory path
        """
        backend_root = BASE_PATH  # e.g. /.../clound-backend/backend
        project_root = backend_root.parent  # /.../clound-backend

        candidates = [
            project_root.parent / 'clound-frontend',  # monorepo 根下的 clound-frontend
            project_root / 'clound-frontend',        # 备用：同项目下的 clound-frontend
        ]

        for candidate in candidates:
            candidate = candidate.resolve()
            if candidate.exists() and (candidate / 'apps' / 'web-antd' / 'src').exists():
                return candidate

        raise ValueError(
            'Could not auto-detect clound-frontend directory. '
            'Please specify --output-dir explicitly.'
        )


# Singleton instance
frontend_generator = FrontendGenerator()
