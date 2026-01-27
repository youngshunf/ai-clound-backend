"""Frontend code generator - main orchestrator."""

import re
from pathlib import Path

from pydantic.alias_generators import to_pascal
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.path_conf import BASE_PATH
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

    async def generate_from_db(
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
        Generate frontend code from database introspection.

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
        views_dir = src_dir / 'views' / app / module  # 使用 app/module 作为目录结构
        api_file = src_dir / 'api' / f'{app}.ts'
        route_file = src_dir / 'router' / 'routes' / 'modules' / f'{app}.ts'

        # Check for existing files (behavior controlled by config/force flag)
        if not force:
            existing_files = []
            if (views_dir / 'index.vue').exists():
                existing_files.append(str(views_dir / 'index.vue'))
            if (views_dir / 'data.ts').exists():
                existing_files.append(str(views_dir / 'data.ts'))

            if existing_files:
                console.print(f'[yellow]以下文件已存在（跳过）:[/yellow]')
                for f in existing_files:
                    console.print(f'  [dim]- {f}[/dim]')
                console.print('[dim]如需覆盖，请在配置文件中设置 existing_file_behavior = "overwrite"[/dim]')
                return

        # Create directories
        views_dir.mkdir(parents=True, exist_ok=True)

        # Generate Vue component
        console.print(f'  Creating [cyan]{views_dir / "index.vue"}[/cyan]')
        vue_template = self.template_env.get_template('vue/index.vue.jinja')
        vue_content = await vue_template.render_async(**vars_dict)
        (views_dir / 'index.vue').write_text(vue_content, encoding='utf-8')

        # Generate data.ts
        console.print(f'  Creating [cyan]{views_dir / "data.ts"}[/cyan]')
        data_template = self.template_env.get_template('vue/data.ts.jinja')
        data_content = await data_template.render_async(**vars_dict)
        (views_dir / 'data.ts').write_text(data_content, encoding='utf-8')

        # Generate or update API file
        console.print(f'  Updating [cyan]{api_file}[/cyan]')
        await self._generate_or_update_api(api_file, vars_dict)

        # Generate or update route file
        console.print(f'  Updating [cyan]{route_file}[/cyan]')
        await self._generate_or_update_route(route_file, vars_dict)

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
            col_meta = {
                'name': col.name,
                'type': col.type,
                'ts_type': sql_to_typescript(col.type),
                'comment': col.comment or col.name,
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
            'api_path': f'/api/v1/{app}',
            'route_path': f'/{app}',
            'component_path': f'#/views/{app}/index.vue',
            'menu_icon': 'lucide:list',
            'menu_order': 1,
            'permission_prefix': table_info.name.replace('_', ':'),
        }

    async def _generate_or_update_api(self, api_file: Path, vars_dict: dict) -> None:
        """Generate or update API file."""
        api_template = self.template_env.get_template('typescript/api.ts.jinja')
        new_api_content = await api_template.render_async(**vars_dict)

        if api_file.exists():
            # Append to existing file
            existing_content = api_file.read_text(encoding='utf-8')
            # Check if this API already exists
            if f"export interface {vars_dict['class_name']}" in existing_content:
                console.print(f'    [yellow]API for {vars_dict["class_name"]} already exists, skipping[/yellow]')
                return
            # Append new content
            api_file.write_text(existing_content + '\n\n' + new_api_content, encoding='utf-8')
        else:
            # Create new file
            api_file.parent.mkdir(parents=True, exist_ok=True)
            api_file.write_text(new_api_content, encoding='utf-8')

    async def _generate_or_update_route(self, route_file: Path, vars_dict: dict) -> None:
        """Generate or update route file."""
        route_template = self.template_env.get_template('typescript/route.ts.jinja')
        new_route_content = await route_template.render_async(**vars_dict)

        if route_file.exists():
            # Append to existing file
            existing_content = route_file.read_text(encoding='utf-8')
            # Check if this route already exists
            if f"name: '{vars_dict['class_name']}'" in existing_content:
                console.print(f'    [yellow]Route for {vars_dict["class_name"]} already exists, skipping[/yellow]')
                return
            # Append new route to routes array
            # Find the last route and insert before the closing bracket
            if 'export default' in existing_content:
                # Insert before the last closing bracket
                last_bracket = existing_content.rfind(']')
                if last_bracket != -1:
                    # Add comma if needed
                    before_bracket = existing_content[:last_bracket].rstrip()
                    if not before_bracket.endswith(','):
                        before_bracket += ','
                    updated_content = before_bracket + '\n' + new_route_content + '\n' + existing_content[last_bracket:]
                    route_file.write_text(updated_content, encoding='utf-8')
                else:
                    console.print('[yellow]    Could not find routes array, appending to end[/yellow]')
                    route_file.write_text(existing_content + '\n\n' + new_route_content, encoding='utf-8')
            else:
                # File doesn't have proper structure, append
                route_file.write_text(existing_content + '\n\n' + new_route_content, encoding='utf-8')
        else:
            # Create new file with proper structure
            route_file.parent.mkdir(parents=True, exist_ok=True)
            full_content = f"""import type {{ RouteRecordRaw }} from 'vue-router';

const routes: RouteRecordRaw[] = [
{new_route_content}
];

export default routes;
"""
            route_file.write_text(full_content, encoding='utf-8')

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
