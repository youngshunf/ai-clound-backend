# 应用市场 CLI 工具

用于发布技能和应用到市场的命令行工具。

## 命令概览

```bash
# 技能命令
fba skill validate --path <技能目录>      # 验证技能包
fba skill info --path <技能目录>          # 查看技能信息
fba skill publish --path <技能目录>       # 发布技能
fba skill publish-all --directory <目录>  # 批量发布目录下所有技能

# 应用命令
fba app validate --path <应用目录>        # 验证应用包
fba app info --path <应用目录>            # 查看应用信息
fba app publish --path <应用目录>         # 发布应用
```

## 技能包结构

```
my-skill/
├── config.yaml     # 必需 - 技能配置
├── SKILL.md        # 必需 - 技能说明文档
└── icon.svg        # 可选 - 技能图标
```

### config.yaml 格式

```yaml
id: my-skill-id           # 技能唯一标识
name: 技能名称
version: 1.0.0            # 语义化版本号
description: 技能描述
author_name: 作者名称
pricing_type: free        # free | paid | subscription
tags:                     # 可选
  - 标签1
  - 标签2
```

## 应用包结构

```
my-app/
├── manifest.json         # 必需 - 应用清单
└── assets/
    └── icon.svg          # 可选 - 应用图标
```

### manifest.json 格式

```json
{
  "id": "app.my-app-id",
  "name": "应用名称",
  "version": "1.0.0",
  "description": "应用描述",
  "author_name": "作者名称",
  "pricing_type": "free",
  "skill_dependencies": [
    "skill-id-1",
    "skill-id-2@1.0.0"
  ]
}
```

## 发布命令详解

### 技能发布

```bash
fba skill publish --path <技能目录> [选项]
```

选项：
- `--version <版本号>` - 指定发布版本（覆盖 config.yaml 中的版本）
- `--bump <major|minor|patch>` - 基于最新版本自动递增
- `--changelog <更新日志>` - 版本更新说明
- `--remote` / `-r` - 远程发布模式
- `--api-url <URL>` - API 服务器地址（远程模式必需）
- `--api-key <KEY>` - 发布 API Key（远程模式必需）

示例：
```bash
# 本地发布 - 使用 config.yaml 中的版本
fba skill publish --path ./skills/my-skill

# 本地发布 - 指定版本号
fba skill publish --path ./skills/my-skill --version 2.0.0

# 本地发布 - 自动递增补丁版本
fba skill publish --path ./skills/my-skill --bump patch

# 本地发布 - 带更新日志
fba skill publish --path ./skills/my-skill --bump minor --changelog "新增功能X"

# 远程发布 - 发布到远程服务器
fba skill publish --path ./skills/my-skill --version 1.0.0 \
    --remote --api-url http://api.example.com --api-key your-api-key
```

### 批量发布技能

```bash
fba skill publish-all --directory <目录> [选项]
```

选项：
- `--bump <major|minor|patch>` - 对所有技能自动递增版本

示例：
```bash
# 发布 skills/ 目录下所有技能
fba skill publish-all --directory ./skills

# 批量递增补丁版本
fba skill publish-all --directory ./skills --bump patch
```

### 应用发布

```bash
fba app publish --path <应用目录> [选项]
```

选项：
- `--version <版本号>` - 指定发布版本
- `--bump <major|minor|patch>` - 自动递增版本
- `--changelog <更新日志>` - 版本更新说明
- `--remote` / `-r` - 远程发布模式
- `--api-url <URL>` - API 服务器地址（远程模式必需）
- `--api-key <KEY>` - 发布 API Key（远程模式必需）

示例：
```bash
# 本地发布
fba app publish --path ./apps/my-app --version 1.0.0

# 远程发布
fba app publish --path ./apps/my-app --version 1.0.0 \
    --remote --api-url http://api.example.com --api-key your-api-key
```

## 远程发布模式

远程发布模式允许你从本地发布技能/应用到远程服务器，无需直接访问数据库和 S3 存储。

### 配置

1. 在服务端设置发布 API Key（环境变量）：
   ```
   MARKETPLACE_PUBLISH_API_KEY=your-secure-api-key
   ```

2. 使用远程模式发布：
   ```bash
   fba skill publish --path ./my-skill --version 1.0.0 \
       --remote --api-url http://your-server:8020 --api-key your-secure-api-key
   ```

### 工作流程

```
本地 CLI  --验证+打包-->  ZIP 文件  --HTTP上传-->  服务端 API  --保存-->  S3 + 数据库
```

## 版本号规范

遵循语义化版本 (SemVer)：`MAJOR.MINOR.PATCH`

- `MAJOR` - 不兼容的 API 变更
- `MINOR` - 向下兼容的功能新增
- `PATCH` - 向下兼容的问题修复

## 发布流程

1. **验证** - 检查必需文件和配置格式
2. **打包** - 创建 ZIP 压缩包
3. **上传** - 上传包和图标到 S3 存储
4. **入库** - 创建/更新数据库记录

## 常见问题

### 验证失败

```bash
# 先单独验证，查看详细错误
fba skill validate --path ./my-skill
```

### 版本已存在

同一版本号不能重复发布。使用 `--bump` 自动递增或指定新版本号。

### S3 上传失败

检查存储配置是否正确：
- 确认 S3 存储已启用
- 检查 bucket 和访问凭证配置

## CDN 域名配置

发布后的文件 URL 默认使用 S3 原始域名。如需使用自定义 CDN 域名，在管理后台的 S3 存储配置中设置 `cdn_domain` 字段：

```
CDN域名: https://cdn.example.com
```

配置后，所有上传文件的 URL 将自动使用 CDN 域名：
- 无 CDN: `http://s3.example.com/bucket/prefix/marketplace/skills/xxx/1.0.0.zip`
- 有 CDN: `https://cdn.example.com/prefix/marketplace/skills/xxx/1.0.0.zip`
