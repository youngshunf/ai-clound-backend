-- =====================================================
-- AI-Native 应用管理：管理端菜单 + 字典（PostgreSQL，幂等）
-- 模块 16 / 实施 06 C5 管理面：应用目录(catalog) + 应用权益(entitlement)
--   后端 admin 路由：/api/v1/hasn/app-catalogs、/api/v1/hasn/app-entitlements（admin scope，无 /app/ 段）
--   前端视图：apps/web-antdv-next/src/views/hasn/{hasn_app_catalog,hasn_app_entitlement}/index.vue
-- 幂等键：菜单按 name 唯一定位（route name）；字典按 code + (type_code,value) NOT EXISTS。
-- 说明：codegen 生成的 *_menu.sql 把两个菜单都命名为「AI-Native管理」(标题冲突) 且平铺到 /hasn，
--       本迁移收口为「AI-Native 应用」子目录 + 应用目录/应用权益 两子项。
-- =====================================================

-- ============ 菜单 ============
DO $$
DECLARE
    v_hasn_id    INTEGER;  -- /hasn 顶级目录（HASN社交网络）
    v_group_id   INTEGER;  -- AI-Native 应用 子目录
    v_catalog_id INTEGER;  -- 应用目录 菜单
    v_ent_id     INTEGER;  -- 应用权益 菜单
BEGIN
    -- 1) 定位 HASN 顶级目录（不存在则兜底创建）
    SELECT id INTO v_hasn_id FROM sys_menu WHERE path = '/hasn' AND type = 0 ORDER BY id LIMIT 1;
    IF v_hasn_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('HASN社交网络', 'Hasn', '/hasn', 80, 'lucide:network', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'HASN 模块', NULL, NOW(), NULL)
        RETURNING id INTO v_hasn_id;
    END IF;

    -- 2) AI-Native 应用 子目录（按 name 幂等）
    SELECT id INTO v_group_id FROM sys_menu WHERE name = 'HasnAiNativeApp' ORDER BY id LIMIT 1;
    IF v_group_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('AI-Native 应用', 'HasnAiNativeApp', '/hasn/ai_native', 13, 'lucide:layout-grid', 0, NULL, NULL, 1, 1, 1, '', 'AI-Native 应用目录与商业化管理', v_hasn_id, NOW(), NULL)
        RETURNING id INTO v_group_id;
    ELSE
        UPDATE sys_menu SET title = 'AI-Native 应用', path = '/hasn/ai_native', icon = 'lucide:layout-grid',
                            type = 0, component = NULL, parent_id = v_hasn_id, updated_time = NOW()
        WHERE id = v_group_id;
    END IF;

    -- 3) 应用目录 菜单（name=HasnAppCatalog 幂等，匹配 views/hasn/hasn_app_catalog/index.vue defineOptions name）
    SELECT id INTO v_catalog_id FROM sys_menu WHERE name = 'HasnAppCatalog' ORDER BY id LIMIT 1;
    IF v_catalog_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('应用目录', 'HasnAppCatalog', '/hasn/hasn_app_catalog', 1, 'lucide:list', 1, '/hasn/hasn_app_catalog/index', NULL, 1, 1, 1, '', 'AI-Native 应用目录（云端权威）', v_group_id, NOW(), NULL)
        RETURNING id INTO v_catalog_id;
    ELSE
        UPDATE sys_menu SET title = '应用目录', path = '/hasn/hasn_app_catalog', icon = 'lucide:list',
                            type = 1, component = '/hasn/hasn_app_catalog/index', parent_id = v_group_id, updated_time = NOW()
        WHERE id = v_catalog_id;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:app:catalog:add' AND parent_id = v_catalog_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddHasnAppCatalog', NULL, 1, NULL, 2, NULL, 'hasn:app:catalog:add', 1, 0, 1, '', NULL, v_catalog_id, NOW(), NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:app:catalog:edit' AND parent_id = v_catalog_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditHasnAppCatalog', NULL, 2, NULL, 2, NULL, 'hasn:app:catalog:edit', 1, 0, 1, '', NULL, v_catalog_id, NOW(), NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:app:catalog:del' AND parent_id = v_catalog_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteHasnAppCatalog', NULL, 3, NULL, 2, NULL, 'hasn:app:catalog:del', 1, 0, 1, '', NULL, v_catalog_id, NOW(), NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:app:catalog:get' AND parent_id = v_catalog_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewHasnAppCatalog', NULL, 4, NULL, 2, NULL, 'hasn:app:catalog:get', 1, 0, 1, '', NULL, v_catalog_id, NOW(), NULL);
    END IF;

    -- 4) 应用权益 菜单（name=HasnAppEntitlement 幂等，匹配 views/hasn/hasn_app_entitlement/index.vue defineOptions name）
    SELECT id INTO v_ent_id FROM sys_menu WHERE name = 'HasnAppEntitlement' ORDER BY id LIMIT 1;
    IF v_ent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('应用权益', 'HasnAppEntitlement', '/hasn/hasn_app_entitlement', 2, 'lucide:key-round', 1, '/hasn/hasn_app_entitlement/index', NULL, 1, 1, 1, '', 'AI-Native 应用权益（云端权威）', v_group_id, NOW(), NULL)
        RETURNING id INTO v_ent_id;
    ELSE
        UPDATE sys_menu SET title = '应用权益', path = '/hasn/hasn_app_entitlement', icon = 'lucide:key-round',
                            type = 1, component = '/hasn/hasn_app_entitlement/index', parent_id = v_group_id, updated_time = NOW()
        WHERE id = v_ent_id;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:app:entitlement:add' AND parent_id = v_ent_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddHasnAppEntitlement', NULL, 1, NULL, 2, NULL, 'hasn:app:entitlement:add', 1, 0, 1, '', NULL, v_ent_id, NOW(), NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:app:entitlement:edit' AND parent_id = v_ent_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditHasnAppEntitlement', NULL, 2, NULL, 2, NULL, 'hasn:app:entitlement:edit', 1, 0, 1, '', NULL, v_ent_id, NOW(), NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:app:entitlement:del' AND parent_id = v_ent_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteHasnAppEntitlement', NULL, 3, NULL, 2, NULL, 'hasn:app:entitlement:del', 1, 0, 1, '', NULL, v_ent_id, NOW(), NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:app:entitlement:get' AND parent_id = v_ent_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewHasnAppEntitlement', NULL, 4, NULL, 2, NULL, 'hasn:app:entitlement:get', 1, 0, 1, '', NULL, v_ent_id, NOW(), NULL);
    END IF;
END $$;

-- ============ 字典（复用共享 hasn_* 字典；只补缺值，不改共享 type 的展示名）============
-- 字典类型：缺则建，不用 ON CONFLICT DO UPDATE 覆盖既有共享展示名
INSERT INTO sys_dict_type (name, code, remark, created_time, updated_time)
VALUES ('状态', 'hasn_status', 'HASN 模块通用状态字典', NOW(), NULL)
ON CONFLICT (code) DO NOTHING;
INSERT INTO sys_dict_type (name, code, remark, created_time, updated_time)
VALUES ('准入类型', 'hasn_access_type', 'AI-Native 应用准入类型', NOW(), NULL)
ON CONFLICT (code) DO NOTHING;
INSERT INTO sys_dict_type (name, code, remark, created_time, updated_time)
VALUES ('权益主体', 'hasn_subject_type', 'AI-Native 应用权益主体类型', NOW(), NULL)
ON CONFLICT (code) DO NOTHING;

-- 字典数据：catalog 上架状态 + 权益状态（共享 hasn_status）；准入类型；权益主体——按 (type_code,value) 幂等补缺
DO $$
DECLARE
    v_status_id  INTEGER;
    v_access_id  INTEGER;
    v_subject_id INTEGER;
BEGIN
    SELECT id INTO v_status_id  FROM sys_dict_type WHERE code = 'hasn_status'       ORDER BY id LIMIT 1;
    SELECT id INTO v_access_id  FROM sys_dict_type WHERE code = 'hasn_access_type'  ORDER BY id LIMIT 1;
    SELECT id INTO v_subject_id FROM sys_dict_type WHERE code = 'hasn_subject_type' ORDER BY id LIMIT 1;

    -- hasn_status: catalog(published/disabled/draft) + entitlement(active/expired/revoked)
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_status' AND value='published') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_status','已上架','published','green',1,1,v_status_id,'',NOW(),NULL); END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_status' AND value='disabled') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_status','已下架','disabled','gray',2,1,v_status_id,'',NOW(),NULL); END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_status' AND value='draft') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_status','草稿','draft','orange',3,1,v_status_id,'',NOW(),NULL); END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_status' AND value='active') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_status','生效','active','green',4,1,v_status_id,'',NOW(),NULL); END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_status' AND value='expired') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_status','已过期','expired','gray',5,1,v_status_id,'',NOW(),NULL); END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_status' AND value='revoked') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_status','已撤销','revoked','red',6,1,v_status_id,'',NOW(),NULL); END IF;

    -- hasn_access_type: free/tier/purchase
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_access_type' AND value='free') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_access_type','免费','free','green',1,1,v_access_id,'',NOW(),NULL); END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_access_type' AND value='tier') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_access_type','订阅准入','tier','blue',2,1,v_access_id,'',NOW(),NULL); END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_access_type' AND value='purchase') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_access_type','购买','purchase','orange',3,1,v_access_id,'',NOW(),NULL); END IF;

    -- hasn_subject_type: owner/enterprise
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_subject_type' AND value='owner') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_subject_type','个人','owner','blue',1,1,v_subject_id,'',NOW(),NULL); END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_subject_type' AND value='enterprise') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_subject_type','企业','enterprise','purple',2,1,v_subject_id,'',NOW(),NULL); END IF;
END $$;

-- =====================================================
-- 完成：菜单「AI-Native 应用 / 应用目录 / 应用权益」+ 字典补缺
-- =====================================================
