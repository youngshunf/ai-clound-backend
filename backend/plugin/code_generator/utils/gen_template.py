from collections.abc import Sequence

from jinja2 import Environment, FileSystemLoader, Template
from pydantic.alias_generators import to_pascal

from backend.core.conf import settings
from backend.plugin.code_generator.model import GenBusiness, GenColumn
from backend.plugin.code_generator.path_conf import JINJA2_TEMPLATE_DIR
from backend.plugin.code_generator.utils.type_conversion import sql_type_to_sqlalchemy_name
from backend.utils.snowflake import snowflake
from backend.utils.timezone import timezone

# SQLAlchemy ORM 保留字段名，需要重命名
SQLALCHEMY_RESERVED_NAMES = {
    'metadata': 'meta_data',
    'registry': 'registry_data',
    'query': 'query_data',
}

# API scope 中文标签映射
SCOPE_LABELS = {
    'admin': '管理端（JWT + RBAC）',
    'app': '用户端（仅 JWT）',
    'agent': 'Agent（Agent Key）',
    'open': '公开（无需认证）',
}

# API scope 对应的路由变量名映射
SCOPE_ROUTER_VAR = {
    'admin': 'v1',
    'app': 'app',
    'open': 'open_api',
    'agent': 'agent',
}


class GenTemplate:
    def __init__(self) -> None:
        """初始化模板生成器"""
        self.env = Environment(
            loader=FileSystemLoader(JINJA2_TEMPLATE_DIR),
            autoescape=False,  # 禁用自动转义，因为生成的是代码而非 HTML
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            enable_async=True,
        )
        self.env.filters['sqlalchemy_type'] = sql_type_to_sqlalchemy_name
        self.init_content = ''

    def get_template(self, jinja_file: str) -> Template:
        """
        获取 Jinja2 模板对象

        :param jinja_file: Jinja2 模板文件路径
        :return: Template 对象
        """
        return self.env.get_template(jinja_file)

    @staticmethod
    def _parse_scopes(business: GenBusiness) -> list[str]:
        """
        解析 business 的 api_scope 字段为 scope 列表

        :param business: 代码生成业务对象
        :return: scope 列表
        """
        api_scope = getattr(business, 'api_scope', None) or 'admin'
        return [s.strip() for s in api_scope.split(',') if s.strip()]

    @staticmethod
    def get_template_path_mapping(business: GenBusiness) -> dict[str, str]:
        """
        获取模板文件到生成文件的路径映射

        :param business: 代码生成业务对象
        :return: {模板路径: 生成文件路径}
        """
        app_name = business.app_name
        filename = business.filename
        api_version = business.api_version
        pk_suffix = '_snowflake' if settings.DATABASE_PK_MODE == 'snowflake' else ''
        scopes = GenTemplate._parse_scopes(business)

        mapping = {
            'python/router.jinja': f'{app_name}/api/router.py',
            'python/crud.jinja': f'{app_name}/crud/crud_{filename}.py',
            'python/model.jinja': f'{app_name}/model/{filename}.py',
            'python/schema.jinja': f'{app_name}/schema/{filename}.py',
            'python/service.jinja': f'{app_name}/service/{filename}_service.py',
            # G6 统一资源权限门·资源适配器骨架（doc33 S3-5）；生成器「文件已存在则跳过」，绝不覆盖既有 adapter。
            'python/resource_adapter.jinja': f'{app_name}/service/resource_adapter.py',
            f'sql/mysql/init{pk_suffix}.jinja': f'{app_name}/sql/mysql/init{pk_suffix}.sql',
            f'sql/postgresql/init{pk_suffix}.jinja': f'{app_name}/sql/postgresql/init{pk_suffix}.sql',
        }

        # 为每个 scope 生成对应的 API 文件
        for scope in scopes:
            template_name = f'python/api_{scope}.jinja'
            output_path = f'{app_name}/api/{api_version}/{scope}/{filename}.py'
            mapping[template_name] = output_path

        return mapping

    def get_init_files(self, business: GenBusiness) -> dict[str, str]:
        """
        获取需要生成的 __init__.py 文件及其内容

        :param business: 业务对象
        :return: {相对路径: 文件内容}
        """
        app_name = business.app_name
        table_name = business.table_name
        class_name = business.class_name or to_pascal(table_name)
        filename = business.filename
        scopes = self._parse_scopes(business)

        init_files = {
            f'{app_name}/__init__.py': self.init_content,
            f'{app_name}/api/__init__.py': self.init_content,
            f'{app_name}/api/{business.api_version}/__init__.py': self.init_content,
            f'{app_name}/crud/__init__.py': self.init_content,
            f'{app_name}/model/__init__.py': (
                f'{self.init_content}from backend.app.{app_name}.model.{filename} import {class_name} as {class_name}\n'
            ),
            f'{app_name}/schema/__init__.py': self.init_content,
            f'{app_name}/service/__init__.py': self.init_content,
        }

        # 为每个 scope 目录生成 __init__.py
        for scope in scopes:
            init_files[f'{app_name}/api/{business.api_version}/{scope}/__init__.py'] = self.init_content

        return init_files

    @staticmethod
    def _rename_reserved_fields(models: Sequence[GenColumn]) -> list[dict]:
        """
        将模型列转换为字典并重命名 SQLAlchemy 保留字段

        :param models: 代码生成模型对象列表
        :return: 转换后的字典列表
        """
        result = []
        for model in models:
            model_dict = {
                'name': model.name,
                'db_column': model.name,  # 原始数据库列名
                'comment': model.comment,
                'type': model.type,
                'pd_type': model.pd_type,
                'default': model.default,
                'sort': model.sort,
                'length': model.length,
                'is_pk': model.is_pk,
                'is_nullable': model.is_nullable,
            }
            # 重命名 SQLAlchemy 保留字段，保留原始列名用于映射
            if model.name in SQLALCHEMY_RESERVED_NAMES:
                model_dict['name'] = SQLALCHEMY_RESERVED_NAMES[model.name]
            result.append(model_dict)
        return result

    @staticmethod
    def get_vars(business: GenBusiness, models: Sequence[GenColumn]) -> dict[str, str | Sequence[GenColumn]]:
        """
        获取模板变量

        :param business: 代码生成业务对象
        :param models: 代码生成模型对象列表
        :return:
        """
        # 将 ORM 对象转换为字典，并重命名保留字段
        processed_models = GenTemplate._rename_reserved_fields(models)
        scopes = GenTemplate._parse_scopes(business)

        vars_dict = {
            'app_name': business.app_name,
            'table_name': business.table_name,
            'doc_comment': business.doc_comment,
            'table_comment': business.table_comment,
            'class_name': business.class_name,
            'schema_name': business.schema_name,
            'filename': business.filename,
            'datetime_mixin': business.datetime_mixin,
            'api_version': business.api_version,
            'tag': business.tag,
            'permission': business.table_name.replace('_', ':'),
            'database_type': settings.DATABASE_TYPE,
            'models': processed_models,
            'model_types': [model.type for model in models],
            'now': timezone.now,  # Keep as callable for templates
            # 多 scope 相关变量
            'api_scope': business.api_scope if hasattr(business, 'api_scope') else 'admin',
            'api_scopes': scopes,
            'scope_labels': SCOPE_LABELS,
            'scope_router_var': SCOPE_ROUTER_VAR,
        }

        if settings.DATABASE_PK_MODE == 'snowflake':
            vars_dict['parent_menu_id'] = snowflake.generate()
            vars_dict['button_ids'] = [snowflake.generate() for _ in range(4)]

        return vars_dict


gen_template: GenTemplate = GenTemplate()
