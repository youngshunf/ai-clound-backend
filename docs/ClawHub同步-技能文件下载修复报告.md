# ClawHub 同步 - 技能文件下载修复报告

**日期**: 2026-05-27  
**状态**: ✅ 已完成

## 问题描述

用户反馈：ClawHub 同步的技能有问题
1. hasn-hub 仓库中没有实际的技能文件
2. `source_repo_path` 字段是空的

**根本原因**：同步服务只保存了技能的元数据到数据库，但没有实际下载技能文件到本地仓库。

## 解决方案

### 1. 添加技能文件下载功能

新增 `_download_skill_file()` 方法，从 ClawHub 下载技能文件并保存到本地：

```python
async def _download_skill_file(
    self,
    skill_id: str,
    owner_handle: str,
    slug: str,
    version: str
) -> str | None:
    """
    Download skill file from ClawHub and save to local hub
    
    Returns:
        Local repo path if successful, None otherwise
    """
    # Create directory: hasn-hub/clawhub/{owner}/{slug}
    skill_dir = self.hub_local_path / 'clawhub' / owner_handle / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    
    # Download URL - ClawHub uses query parameters for skills
    download_url = f"{self.clawhub_api_url}/download"
    params = {'slug': slug, 'version': version}
    
    # Download and extract
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(download_url, params=params)
        response.raise_for_status()
        
        zip_file = skill_dir / f"{slug}-{version}.zip"
        zip_file.write_bytes(response.content)
        
        # Extract zip file
        with zipfile.ZipFile(zip_file, 'r') as zip_ref:
            zip_ref.extractall(skill_dir)
        
        zip_file.unlink()  # Remove zip after extraction
    
    return f"clawhub/{owner_handle}/{slug}"
```

### 2. 修复下载 API 端点

**错误的 URL**：
```python
# ❌ 这个端点不存在
download_url = f"{api_url}/skills/{slug}/versions/{version}/download"
```

**正确的 URL**（参考 OpenClaw 源码）：
```python
# ✅ ClawHub 使用查询参数
download_url = f"{api_url}/download?slug={slug}&version={version}"
```

### 3. 修复字段名错误

**问题**：使用了错误的字段名 `repo_path`，实际字段名是 `source_repo_path`。

```python
# ❌ 错误
skill_data['repo_path'] = repo_path

# ✅ 正确
skill_data['source_repo_path'] = repo_path
```

### 4. 集成到同步流程

在 `_sync_skill()` 方法中，同步版本时下载文件：

```python
# Sync latest version
latest_version = clawhub_skill.get('latestVersion')
if latest_version:
    version_str = latest_version.get('version', '1.0.0')
    
    # Download skill file
    repo_path = await self._download_skill_file(
        skill_id=skill_id,
        owner_handle=owner_handle,
        slug=slug,
        version=version_str
    )
    
    # Update skill with source_repo_path
    if repo_path:
        current_skill = await marketplace_skill_dao.get_by_id(db, skill_id)
        if current_skill:
            update_data = {
                # ... other fields
                'source_repo_path': repo_path,
                # ...
            }
            update_param = UpdateMarketplaceSkillParam(**update_data)
            await marketplace_skill_dao.update(db, current_skill.id, update_param)
```

## 验证结果

### 1. 数据库验证

```
✅ clawhub/mnetfairy/insurance-advisor-china
   source_repo_path: clawhub/mnetfairy/insurance-advisor-china

✅ clawhub/mnetfairy/ai-insurance-advisor
   source_repo_path: clawhub/mnetfairy/ai-insurance-advisor
```

### 2. 文件系统验证

```bash
$ ls -la hasn-hub/clawhub/mnetfairy/insurance-advisor-china/
total 32
-rw-r--r--  1 mac  staff    144 May 27 01:41 _meta.json
drwxr-xr-x  6 mac  staff    192 May 27 01:34 references
drwxr-xr-x  5 mac  staff    160 May 27 01:34 scripts
-rw-r--r--  1 mac  staff  11811 May 27 01:41 SKILL.md
```

技能文件已成功下载并解压到本地仓库。

### 3. 下载日志

```
INFO - Downloading skill insurance-advisor-china version 1.8.164 from https://clawhub.ai/api/v1/download
INFO - Downloaded skill file to .../insurance-advisor-china-1.8.164.zip (372088 bytes)
INFO - Extracting skill file...
INFO - Extracted and removed zip file
```

## 目录结构

```
hasn-hub/
└── clawhub/
    └── {owner_handle}/
        └── {slug}/
            ├── SKILL.md          # 技能描述文件
            ├── _meta.json        # 元数据
            ├── scripts/          # 脚本目录
            └── references/       # 参考资料
```

## 技术细节

### ClawHub API

根据 OpenClaw 源码（`external/openclaw/src/infra/clawhub.ts`）：

```typescript
// Skills 下载端点
export async function downloadClawHubSkillArchive(params: {
  slug: string;
  version?: string;
  tag?: string;
}) {
  const { response } = await clawhubRequest({
    path: "/api/v1/download",
    search: {
      slug: params.slug,
      version: params.version,
      tag: params.version ? undefined : params.tag,
    },
  });
  // Returns a zip file
}
```

### 文件格式

- ClawHub 返回的是 ZIP 压缩包
- 解压后包含 SKILL.md、scripts、references 等目录
- `_meta.json` 包含技能的元数据

## 修改的文件

1. **backend/app/marketplace/service/clawhub_sync_service.py**
   - 添加 `_download_skill_file()` 方法
   - 修改 `_sync_skill()` 集成下载功能
   - 修复字段名：`repo_path` → `source_repo_path`
   - 添加必要的 import：`os`, `shutil`, `zipfile`, `Path`

## 配置

需要在 `.env` 中配置 hasn-hub 路径：

```bash
HUANXING_HUB_LOCAL_PATH='/path/to/hasn-hub'
```

默认值：`/tmp/hasn-hub`

## 使用示例

```python
from backend.app.marketplace.service.clawhub_sync_service import clawhub_sync_service
from backend.database.db import async_db_session

async with async_db_session() as db:
    # 同步技能（自动下载文件）
    result = await clawhub_sync_service.sync_from_clawhub(
        db,
        skill_ids=['insurance-advisor-china']
    )
    
    print(f"同步成功: {result['synced']} 个技能")
```

## 总结

✅ **已完成**：
1. 实现技能文件下载功能
2. 修复 ClawHub API 端点
3. 修复字段名错误
4. 集成到同步流程
5. 验证文件正确下载到 hasn-hub

✅ **效果**：
- 技能文件正确下载到 `hasn-hub/clawhub/{owner}/{slug}/`
- `source_repo_path` 字段正确保存
- 支持自动解压 ZIP 文件
- 完整的错误处理和日志记录

---

**最后更新**: 2026-05-27  
**状态**: ✅ 已完成并验证
