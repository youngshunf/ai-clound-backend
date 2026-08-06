# ClawHub 同步 - repo_path 字段修复与本地打包功能

**日期**: 2026-05-27  
**状态**: ✅ 已完成

## 问题说明

用户指出了字段使用错误：
1. ❌ 使用了 `source_repo_path`（这是给第三方 GitHub 仓库路径用的）
2. ✅ 应该使用 `repo_path`（本地 hasn-hub 仓库路径）
3. ❓ 下载 zip 时应该从 `repo_path` 打包，而不是直接返回 CDN 链接

## 字段含义澄清

### repo_path
- **用途**: 本地 hasn-hub 仓库中的路径
- **示例**: `clawhub/mnetfairy/insurance-advisor-china`
- **场景**: 
  - ClawHub 同步的技能
  - 本地开发的技能
  - 从 GitHub 同步并下载到本地的技能

### source_repo_path
- **用途**: 第三方源仓库（如 GitHub）中的路径
- **示例**: `skills/translator-pro`
- **场景**: 
  - GitHub 仓库中的技能路径
  - 用于从 GitHub 拉取更新

### source_repo_url
- **用途**: 第三方源仓库的 URL
- **示例**: `https://github.com/owner/repo` 或 `https://clawhub.ai/skills/slug`
- **场景**: 
  - 记录技能的来源
  - 用于显示和跳转

## 修复内容

### 1. 添加 repo_path 到 Schema

**文件**: `backend/app/marketplace/schema/marketplace_skill.py`

```python
class MarketplaceSkillSchemaBase(SchemaBase):
    # ... 其他字段
    source_repo_url: str | None = Field(None, description='源仓库 URL')
    source_repo_path: str | None = Field(None, description='源仓库内路径（如 skills/translator-pro，用于 GitHub）')
    repo_path: str | None = Field(None, description='在 hasn-hub 中的路径')  # 新增
    # ...
```

### 2. 修改同步服务使用 repo_path

**文件**: `backend/app/marketplace/service/clawhub_sync_service.py`

```python
# 下载技能文件后，更新 repo_path
if repo_path:
    current_skill = await marketplace_skill_dao.get_by_id(db, skill_id)
    if current_skill:
        update_data = {
            # ... 其他字段
            'repo_path': repo_path,  # ✅ 使用 repo_path
            # ...
        }
        update_param = UpdateMarketplaceSkillParam(**update_data)
        await marketplace_skill_dao.update(db, current_skill.id, update_param)
```

### 3. 实现本地打包功能

**文件**: `backend/app/marketplace/api/v1/download.py`

新增 `create_skill_package()` 函数，从本地 `repo_path` 打包 zip：

```python
def create_skill_package(repo_path: str, skill_id: str, version: str) -> str:
    """
    从本地 repo_path 创建技能包 zip 文件
    
    Args:
        repo_path: 技能在 hasn-hub 中的路径（如 clawhub/owner/slug）
        skill_id: 技能 ID
        version: 版本号
    
    Returns:
        临时 zip 文件的路径
    """
    hub_path = Path(getattr(settings, 'HUANXING_HUB_LOCAL_PATH', '/tmp/hasn-hub'))
    skill_dir = hub_path / repo_path
    
    if not skill_dir.exists():
        raise errors.NotFoundError(msg=f'技能文件不存在: {repo_path}')
    
    # 创建临时 zip 文件
    temp_dir = tempfile.gettempdir()
    zip_filename = f"{skill_id.replace('/', '_')}_{version}.zip"
    zip_path = os.path.join(temp_dir, zip_filename)
    
    # 打包技能目录
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(skill_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, skill_dir)
                zipf.write(file_path, arcname)
    
    return zip_path
```

### 4. 修改下载接口优先使用本地打包

```python
@router.get('/skill/{skill_id}/{version}')
async def download_skill(db: CurrentSession, skill_id: str, version: str):
    skill = await marketplace_skill_dao.get_by_id(db, skill_id)
    skill_version = await marketplace_skill_version_dao.get_by_skill_and_version(db, skill_id, version)
    
    # 优先使用本地 repo_path 打包
    if skill.repo_path:
        try:
            zip_path = create_skill_package(skill.repo_path, skill_id, skill_version.version)
            return FileResponse(
                path=zip_path,
                filename=f"{skill.slug}_{skill_version.version}.zip",
                media_type='application/zip',
            )
        except Exception as e:
            log.error(f"Failed to create package from repo_path: {e}")
            # 降级到使用 package_url
    
    # 降级：使用 package_url（CDN 链接）
    if skill_version.package_url:
        return response_base.success(data=DownloadResponse(
            download_url=skill_version.package_url,
            version=skill_version.version,
            file_hash=skill_version.file_hash,
            file_size=skill_version.file_size,
        ))
```

## 工作流程

### 同步流程

```
1. ClawHub API 获取技能元数据
   ↓
2. 翻译 name 和 description
   ↓
3. 下载技能 zip 文件
   ↓
4. 解压到 hasn-hub/clawhub/{owner}/{slug}/
   ↓
5. 保存 repo_path = "clawhub/{owner}/{slug}"
   ↓
6. 提交到数据库
```

### 下载流程

```
用户请求下载
   ↓
检查 skill.repo_path 是否存在
   ↓
存在 → 从本地打包 zip → 返回 FileResponse
   ↓
不存在 → 返回 package_url（CDN 链接）
```

## 验证结果

### 1. 同步验证

```
✅ skill_id: clawhub/mnetfairy/insurance-advisor-china
✅ repo_path: clawhub/mnetfairy/insurance-advisor-china
✅ source_repo_url: https://clawhub.ai/skills/insurance-advisor-china
```

### 2. 文件验证

```bash
$ ls -la hasn-hub/clawhub/mnetfairy/insurance-advisor-china/
-rw-r--r--  1 mac  staff    144 _meta.json
-rw-r--r--  1 mac  staff  11811 SKILL.md
drwxr-xr-x  6 mac  staff    192 references/
drwxr-xr-x  5 mac  staff    160 scripts/
```

### 3. 打包验证

```
✅ 打包成功: /tmp/clawhub_mnetfairy_insurance-advisor-china_1.8.164.zip
✅ 文件大小: 374891 bytes (366.10 KB)
```

## 优势

### 使用本地打包的好处

1. **性能优化**
   - 不依赖外部 CDN
   - 本地打包速度快
   - 减少网络延迟

2. **可控性**
   - 完全控制技能文件
   - 可以修改和定制
   - 不受第三方服务影响

3. **一致性**
   - 保证下载的文件与本地一致
   - 避免 CDN 缓存问题
   - 版本控制更可靠

4. **降级机制**
   - 如果本地文件不存在，自动降级到 CDN
   - 保证服务可用性

## 数据流向

```
ClawHub
  ↓ (同步)
hasn-hub (本地文件系统)
  ├── clawhub/
  │   └── {owner}/
  │       └── {slug}/
  │           ├── SKILL.md
  │           ├── _meta.json
  │           ├── scripts/
  │           └── references/
  ↓ (打包)
临时 zip 文件
  ↓ (下载)
用户
```

## 配置

需要在 `.env` 中配置：

```bash
HUANXING_HUB_LOCAL_PATH='/path/to/hasn-hub'
```

## 修改的文件

1. **backend/app/marketplace/schema/marketplace_skill.py**
   - 添加 `repo_path` 字段到 schema

2. **backend/app/marketplace/service/clawhub_sync_service.py**
   - 修改使用 `repo_path` 而不是 `source_repo_path`

3. **backend/app/marketplace/api/v1/download.py**
   - 添加 `create_skill_package()` 函数
   - 修改 `download_skill()` 优先使用本地打包
   - 添加必要的 import

## 总结

✅ **已完成**：
1. 修复字段使用：`source_repo_path` → `repo_path`
2. 实现本地打包功能
3. 修改下载接口优先使用本地文件
4. 添加降级机制（本地失败 → CDN）
5. 完整测试验证

✅ **效果**：
- 技能文件正确保存到 `hasn-hub`
- `repo_path` 字段正确记录
- 下载时从本地打包 zip
- 性能和可控性提升

---

**最后更新**: 2026-05-27  
**状态**: ✅ 已完成并验证
