from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]

# 采集子域收编（设计 07 §5.0）：app/lead_automation → app/hasn_growth，
# 10 表 SET SCHEMA hasn_growth + 去前缀；Python 文件名/类名保留 lead_*（churn 控制，表名才是隔离边界）。
# canonical 路由前缀 /api/v1/growth/*；旧 /api/v1/lead-automation/* 薄转发过渡（M8 退役）。
#
# 【admin 面断言已整体反转 —— 防回归守卫】
# 运营面不再由 codegen 按表生成，见运营管理面设计：AI-Native 应用移出平台归属，每个应用经 SDK
# 接入、自选语言、**自建业务运营面**，云端后台不再承载应用的 admin CRUD。原本断言
# `api/v1/admin/{table}.py` **必须存在**的约 20 余条，现已反转为**必须不存在**——以后谁再跑
# codegen 把这些文件生成回来，本测试直接红，逼其删除而不是悄悄合入。
# 唯一例外是 `api/v1/admin/business.py`：它是 GDPR/DSR 合规面（按 email/手机号删除联系人、审计
# 日志、保留期延长、来源黑名单），属平台义务而非应用运营面，单独立项，相关断言保持原样不反转。


def test_codegen_generated_all_crud_model_schema_api_files() -> None:
    app_root = ROOT / 'backend/app/hasn_growth'
    tables = (
        'lead_source_config',
        'lead_collection_job',
        'lead_firecrawl_request',
        'lead_raw_record',
        'lead_contact',
        'lead_contact_source',
        'lead_rejected_record',
        'lead_export_batch',
        'lead_export_item',
        'lead_audit_log',
    )
    owner_scoped_generated_routes = {
        'lead_collection_job',
        'lead_export_batch',
    }

    for table in tables:
        assert (app_root / f'model/{table}.py').exists()
        assert (app_root / f'schema/{table}.py').exists()
        assert (app_root / f'crud/crud_{table}.py').exists()
        assert (app_root / f'service/{table}_service.py').exists()
        # 反转断言：codegen admin CRUD 已整体删除，运营面不再由 codegen 按表生成
        # （见运营管理面设计；应用自建运营面）。再次生成回来即视为回归。
        assert not (app_root / f'api/v1/admin/{table}.py').exists(), table
        # 用户与 Agent 业务面已收口到 growth.py；仅仍具 owner 隔离语义的后台任务/导出批次
        # 保留生成路由，其余采集流水表禁止重新暴露通用写面。
        for scope in ('app', 'agent'):
            route = app_root / f'api/v1/{scope}/{table}.py'
            assert route.exists() is (table in owner_scoped_generated_routes)
        # 匿名只读历史面仍保留；公共线索池本体 lead_contact 不公开通用详情路由。
        if table != 'lead_contact':
            assert (app_root / f'api/v1/open/{table}.py').exists()
    # lead_contact 三 scope 文件确已删除（防回归再生成）
    for scope in ('app', 'agent', 'open'):
        assert not (app_root / f'api/v1/{scope}/lead_contact.py').exists(), scope

    # 收编完成后旧目录删除，不留双中心
    assert not (ROOT / 'backend/app/lead_automation').exists()
    assert not (ROOT / 'backend/plugin/lead_automation').exists()


def test_lead_automation_sql_contains_required_indexes_and_retention_default() -> None:
    # 001 历史建表脚本随目录迁入 backend/sql/hasn_growth/（内容保留 lead_* 表名，由迁移脚本 SET SCHEMA + 去前缀）
    sql = (ROOT / 'backend/sql/hasn_growth/001_create_lead_automation_tables.sql').read_text(encoding='utf-8')

    for table in (
        'lead_source_config',
        'lead_collection_job',
        'lead_firecrawl_request',
        'lead_raw_record',
        'lead_contact',
        'lead_contact_source',
        'lead_rejected_record',
        'lead_export_batch',
        'lead_export_item',
        'lead_audit_log',
    ):
        assert f'CREATE TABLE IF NOT EXISTS {table}' in sql
        assert f'COMMENT ON TABLE {table}' in sql
    assert 'uq_lead_contact_email' in sql
    assert 'WHERE dedupe_key_email IS NOT NULL' in sql
    assert 'uq_lead_contact_phone' in sql
    assert 'WHERE dedupe_key_phone IS NOT NULL' in sql
    assert 'uq_lead_contact_domain' in sql
    assert 'WHERE dedupe_key_domain IS NOT NULL' in sql
    assert "interval '18 months'" in sql
    assert 'idx_lead_audit_log_event_type' in sql


def test_absorb_migration_sets_schema_and_renames_de_prefixed() -> None:
    """收编迁移脚本：10 表 SET SCHEMA hasn_growth + 去前缀，幂等。"""
    mig = (ROOT / 'backend/sql/hasn_growth/migrations/2026-06-12-absorb-lead-automation.sql').read_text(
        encoding='utf-8'
    )
    assert 'CREATE SCHEMA IF NOT EXISTS hasn_growth' in mig
    assert 'SET SCHEMA hasn_growth' in mig
    for old_name, new_name in (
        ('lead_source_config', 'source_config'),
        ('lead_collection_job', 'collection_job'),
        ('lead_contact', 'contact'),
        ('lead_audit_log', 'audit_log'),
    ):
        assert old_name in mig
        assert f"'{new_name}'" in mig


# ============================ M2：7 张新业务表 + 应用注册 ============================

_GROWTH_TABLES = (
    'customer',
    'opportunity',
    'outreach_message',
    'activity',
    'playbook',
    'form_submission',
    'optout_record',
)


def test_m2_seven_new_tables_codegen_artifacts_exist() -> None:
    """7 张新表 codegen 产物：只保留 model/schema/crud/service（数据层仍被业务面复用）。

    admin API 断言已反转为「必须不存在」：运营面不再由 codegen 按表生成，见运营管理面设计
    ——AI-Native 应用移出平台归属，每个应用经 SDK 接入、自选语言、自建业务运营面。
    """
    app_root = ROOT / 'backend/app/hasn_growth'
    for table in _GROWTH_TABLES:
        assert (app_root / f'model/{table}.py').exists(), table
        assert (app_root / f'schema/{table}.py').exists(), table
        assert (app_root / f'crud/crud_{table}.py').exists(), table
        assert (app_root / f'service/{table}_service.py').exists(), table
        assert not (app_root / f'api/v1/admin/{table}.py').exists(), table


def test_m2_new_business_tables_have_no_generic_app_agent_open_crud() -> None:
    """新业务表的通用 app/agent/open CRUD 已删除：避免无 owner 隔离/无脱敏的 PII 泄漏面（M3 手写业务 API）。"""
    app_root = ROOT / 'backend/app/hasn_growth'
    for scope in ('app', 'agent', 'open'):
        for table in _GROWTH_TABLES:
            assert not (app_root / f'api/v1/{scope}/{table}.py').exists(), f'{scope}/{table}'


def test_m2_new_models_inherit_growth_schema_base() -> None:
    """7 张新 model 继承 HasnGrowthAppBase → 落 hasn_growth schema（ADR-15 强隔离）。"""
    model_dir = ROOT / 'backend/app/hasn_growth/model'
    for table in _GROWTH_TABLES:
        src = (model_dir / f'{table}.py').read_text(encoding='utf-8')
        assert 'from backend.app.hasn_growth.model._base import HasnGrowthAppBase' in src, table
        assert '(HasnGrowthAppBase):' in src, table
        assert '(Base):' not in src, table


def test_m2_new_models_map_to_hasn_growth_schema() -> None:
    """SQLAlchemy 映射：7 张新表 schema == hasn_growth。"""
    from backend.app.hasn_growth.model.activity import Activity
    from backend.app.hasn_growth.model.customer import Customer
    from backend.app.hasn_growth.model.form_submission import FormSubmission
    from backend.app.hasn_growth.model.opportunity import Opportunity
    from backend.app.hasn_growth.model.optout_record import OptoutRecord
    from backend.app.hasn_growth.model.outreach_message import OutreachMessage
    from backend.app.hasn_growth.model.playbook import Playbook

    for model in (Customer, Opportunity, OutreachMessage, Activity, Playbook, FormSubmission, OptoutRecord):
        assert model.__table__.schema == 'hasn_growth', model.__name__


def test_s5_profile_tables_use_codegen_artifacts_without_generic_routes() -> None:
    """画像历史与建议表由 codegen 生成，业务读写只走 Owner/Agent 专用契约。"""
    app_root = ROOT / 'backend/app/hasn_growth'
    for table in ('growth_profile_version', 'growth_profile_suggestion'):
        assert (app_root / f'model/{table}.py').exists()
        assert (app_root / f'schema/{table}.py').exists()
        assert (app_root / f'crud/crud_{table}.py').exists()
        assert (app_root / f'service/{table}_service.py').exists()
        source = (app_root / f'model/{table}.py').read_text(encoding='utf-8')
        assert 'HasnGrowthAppBase' in source
        for scope in ('admin', 'app', 'agent', 'open'):
            assert not (app_root / f'api/v1/{scope}/{table}.py').exists()


def test_m2_create_sql_has_seven_tables_and_key_constraints() -> None:
    sql = (ROOT / 'backend/sql/hasn_growth/002_create_growth_tables.sql').read_text(encoding='utf-8')
    for table in _GROWTH_TABLES:
        assert f'CREATE TABLE IF NOT EXISTS {table}' in sql, table
        assert f'COMMENT ON TABLE {table}' in sql, table
    # 关键约束（设计 §5.2）
    assert 'uq_growth_customer_user_lead' in sql
    assert 'REFERENCES contact(id)' in sql  # customer.lead_contact_id 物理外键（收编后同 schema）
    assert 'uq_growth_outreach_dedupe' in sql
    assert 'uq_growth_optout_user_channel_addr' in sql
    # 字典字段带色值注释
    assert 'blocked_optout:退订拦截:red' in sql
    assert 'pending_approval:待审批:orange' in sql


def test_sensitive_admin_codegen_crud_is_not_mounted() -> None:
    """codegen 按表生成的 admin CRUD 一律不得进入路由表。

    原本 opportunity / playbook 两张「不含 PII」的表还挂着生成 CRUD，断言它们**必须挂载**；
    现已随其余 admin 面一并删除，断言相应反转为**不得出现在路由表里**——运营面不再由 codegen
    按表生成，见运营管理面设计（应用经 SDK 接入、自建业务运营面）。
    v1 上只应剩 GDPR/DSR 合规面 business.py 的端点。
    """
    import backend.app.hasn_growth.api.router as growth_router_mod

    from backend.app.hasn_growth.api.router import v1

    v1_paths = {getattr(r, 'path', '') for r in v1.routes}
    # 反转：这两条曾被要求「必须挂载」，现在必须消失
    assert not any(p == '/api/v1/growth/opportunitys' for p in v1_paths)
    assert not any(p == '/api/v1/growth/playbooks' for p in v1_paths)
    retired_prefixes = (
        # 原「高风险、刻意不挂路由」的生成 CRUD——依然不得出现
        '/api/v1/growth/lead/raw/records',
        '/api/v1/growth/lead/contacts',
        '/api/v1/growth/lead/contact/sources',
        '/api/v1/growth/lead/rejected/records',
        '/api/v1/growth/lead/export/items',
        '/api/v1/growth/lead/audit/logs',
        '/api/v1/growth/customers',
        '/api/v1/growth/outreach-messages',
        '/api/v1/growth/activitys',
        '/api/v1/growth/form-submissions',
        '/api/v1/growth/optout-records',
        # 本批新退役：低风险配置/作业面的生成 CRUD 同样随 admin 面整体下线
        '/api/v1/growth/opportunitys',
        '/api/v1/growth/playbooks',
        '/api/v1/growth/lead-source-configs',
        '/api/v1/growth/lead/collection/jobs',
        '/api/v1/growth/lead/firecrawl/requests',
        '/api/v1/growth/lead/export/batchs',
    )
    assert all(not any(path.startswith(prefix) for path in v1_paths) for prefix in retired_prefixes)
    # 合规面（GDPR/DSR）单独立项、本批不动：/admin/* 端点必须仍在
    assert any(p.startswith('/api/v1/growth/admin/') for p in v1_paths), v1_paths
    # M8 退役：legacy_* 符号已删除，旧 /api/v1/lead-automation/* 转发面整体清零（双中心归一）
    assert not hasattr(growth_router_mod, 'legacy_v1')
    assert not any('lead-automation' in p for p in v1_paths)


def test_m2_app_registration_manifest_scope_catalog() -> None:
    """应用注册：manifest、权限 scope 与云端工具声明齐备。"""
    from backend.app.hasn_core.app_platform import AINativeAppRegistry, app_catalog_registry
    from backend.app.hasn_growth.manifest import GROWTH_AI_NATIVE_MANIFEST
    from backend.app.mcp.scopes import SCOPE_CATALOG

    # 命名铁律：app_id=growth（de-prefixed），模块/schema 仍 hasn_growth
    assert GROWTH_AI_NATIVE_MANIFEST['app_id'] == 'growth'
    assert GROWTH_AI_NATIVE_MANIFEST['execution_mode'] == 'cloud'
    # 纯云端业务应用：工具走云端 gateway_internal（对齐 community/knowledge），非本地 hasn-mcp 中转。
    assert GROWTH_AI_NATIVE_MANIFEST['transport_mode'] == 'cloud'
    caps = GROWTH_AI_NATIVE_MANIFEST['capabilities']
    # 35 = S5–S9 的 33 个工具 + S11 项目经营报表与下一周期建议提交。
    assert len(caps) == 35
    assert {
        'hasn.growth.lead.ingest',
        'hasn.growth.lead.list',
        'hasn.growth.outreach.draft',
        'hasn.growth.outreach.submit',
        'hasn.growth.opportunity.list',
        'hasn.growth.deal.close',
        'hasn.growth.report.performance',
        'hasn.growth.review.suggest',
    }.issubset({cap['mcp_name'] for cap in caps})
    assert all(c['mcp_name'].startswith('hasn.growth.') for c in caps)
    # 所有 required_scopes 冒号词表
    for c in caps:
        for s in c['required_scopes']:
            assert ':' in s and '.' not in s, s

    # tools[] 由 capabilities 派生，每条 gateway_internal + handler 指向云端 handler 注册表键。
    tools = GROWTH_AI_NATIVE_MANIFEST['tools']
    assert len(tools) == len(caps)
    assert {t['tool_id'] for t in tools} == {c['tool_id'] for c in caps}
    assert all(t['transport'] == 'gateway_internal' and t['handler'].startswith('growth.') for t in tools)

    # manifest registry
    reg = AINativeAppRegistry()
    assert reg.get_builtin_manifest('growth')['app_id'] == 'growth'

    # 5 scope 聚合进 SCOPE_CATALOG
    for s in ('growth:read', 'growth:manage', 'growth:outreach', 'growth:collect', 'growth:pii'):
        assert s in SCOPE_CATALOG, s

    # App：manual install（default_mount=FALSE）
    wapp = app_catalog_registry.get('growth')
    assert wapp.id == 'growth'
    assert wapp.name == '获客'
    assert wapp.install_policy == 'manual'
    assert 'growth' not in {a.id for a in app_catalog_registry.auto_install_apps('personal')}


def test_business_layer_does_not_replace_generated_crud() -> None:
    business_source = (ROOT / 'backend/app/hasn_growth/service/business_service.py').read_text(encoding='utf-8')

    assert 'CRUDPlus' not in business_source
    assert 'repository' not in business_source
    assert 'from backend.app.hasn_growth.model import' in business_source


def test_business_api_and_tasks_are_registered_beside_codegen_crud() -> None:
    router_source = (ROOT / 'backend/app/hasn_growth/api/router.py').read_text(encoding='utf-8')
    app_business = (ROOT / 'backend/app/hasn_growth/api/v1/app/business.py').read_text(encoding='utf-8')
    admin_business = (ROOT / 'backend/app/hasn_growth/api/v1/admin/business.py').read_text(encoding='utf-8')
    agent_business = (ROOT / 'backend/app/hasn_growth/api/v1/agent/business.py').read_text(encoding='utf-8')
    open_business = (ROOT / 'backend/app/hasn_growth/api/v1/open/business.py').read_text(encoding='utf-8')
    task_source = (ROOT / 'backend/app/hasn_growth/tasks.py').read_text(encoding='utf-8')
    pipeline_source = (ROOT / 'backend/app/hasn_growth/service/pipeline_service.py').read_text(encoding='utf-8')

    assert 'app_business_router' in router_source
    assert 'admin_business_router' in router_source
    assert 'open_business_router' in router_source
    assert 'agent_business_router' in router_source
    # canonical /growth 单挂载（旧 /lead-automation 薄转发已 M8 退役 2026-06-13，双中心归一）
    assert "_build_routers('growth')" in router_source
    assert "_build_routers('lead-automation')" not in router_source
    assert "post('/jobs'" in app_business
    assert "post('/jobs/{job_id}/run'" in app_business
    assert "get('/jobs/{job_id}'" in app_business
    assert "get('/rejected'" in app_business
    assert "post('/exports'" in app_business
    assert 'request.user.id' in app_business
    assert 'run_job(db, job_id, user_id=request.user.id)' in app_business
    assert 'user_id=obj.user_id' not in app_business
    assert 'user_id: int | None = None' not in app_business
    assert "'/admin/audit-logs'" in admin_business
    assert "'/admin/contacts/by-email'" in admin_business
    assert "'/admin/contacts/by-phone'" in admin_business
    assert "'/admin/archive-expired'" in admin_business
    assert "'/admin/source-configs/blacklist'" in admin_business
    assert "'/admin/contacts/{contact_id}/extend-retention'" in admin_business
    assert "get('/status'" in agent_business
    assert "get('/healthz'" in open_business
    assert 'def lead_automation_run_job' in task_source
    assert 'def lead_automation_archive_expired' in task_source
    assert 'lead_automation_pipeline_service' in task_source
    assert 'class LeadAutomationPipelineService' in pipeline_source
    assert 'async_db_session.begin()' in task_source


def test_business_service_covers_compliance_side_effects() -> None:
    business_source = (ROOT / 'backend/app/hasn_growth/service/business_service.py').read_text(encoding='utf-8')

    assert 'mask_contact_fields(row, reveal=False)' in business_source
    assert "event_type='config_change'" in business_source
    assert 'async def update_blacklist' in business_source
    assert 'async def extend_retention' in business_source
    assert 'async def dsr_delete_by_email' in business_source
    assert 'daily export limit exceeded' in business_source


def test_codegen_templates_keep_generated_output_importable() -> None:
    model_template = (ROOT / 'backend/plugin/code_generator/templates/python/model.jinja').read_text(encoding='utf-8')
    router_template = (ROOT / 'backend/plugin/code_generator/templates/python/router.jinja').read_text(encoding='utf-8')
    app_template = (ROOT / 'backend/plugin/code_generator/templates/python/api_app.jinja').read_text(encoding='utf-8')
    frontend_generator = (ROOT / 'backend/plugin/code_generator/frontend/generator.py').read_text(encoding='utf-8')
    ts_api_template = (ROOT / 'backend/plugin/code_generator/templates/typescript/api.ts.jinja').read_text(
        encoding='utf-8'
    )

    assert '{% endif %}' in model_template
    assert 'open_api.include_router(open_{{ table_name }}_router, prefix=' in router_template
    assert 'get_list(db=db, user_id=user_id)' not in app_template
    assert 'app.replace("_", "-")' in frontend_generator
    assert 'module.replace("_", "-")' in frontend_generator
    assert 'id: number;' in ts_api_template
    assert 'create{{ class_name }}Api(data: any)' in ts_api_template


def test_generated_frontend_api_paths_match_registered_backend_prefixes() -> None:
    # M1 收编：管理端前端 api 前缀切到 canonical /api/v1/growth/*
    # 兼容主 clone 与 `.worktrees/<任务>` 两种布局，找不到真实前端仓必须失败。
    frontend_api_root = next(
        (
            parent / 'hasn-cloud-frontend/apps/web-antdv-next/src/api/lead_automation'
            for parent in (ROOT, *ROOT.parents)
            if (parent / 'hasn-cloud-frontend/apps/web-antdv-next/src/api/lead_automation').is_dir()
        ),
        None,
    )
    assert frontend_api_root is not None, '未找到真实 hasn-cloud-frontend 仓，无法校验跨仓 API 前缀'
    for path in frontend_api_root.glob('*.ts'):
        text = path.read_text(encoding='utf-8')
        assert '/api/v1/lead_automation/' not in text
        assert '/api/v1/growth/' in text
        assert 'export interface Lead' in text
        assert 'export interface Lead' in text and '{\n  id: number;' in text
        assert 'Params {\n  id: number;' not in text
        assert 'CreateParams {\n  id: number;' not in text
        assert 'ListResult {\n  id: number;' not in text

    source_config_api = (frontend_api_root / 'lead_source_config.ts').read_text(encoding='utf-8')
    assert '/api/v1/growth/lead-source-configs' in source_config_api
    assert '/api/v1/growth/lead/source/configs' not in source_config_api
