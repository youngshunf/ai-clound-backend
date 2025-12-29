# Cloud Backend - AI 上下文文档

> **路径**: `services/cloud-backend/`
> **类型**: FastAPI 云端后端服务
> **作者**: @Ysf

---

## 📋 模块概览

**Cloud Backend** 是 AI Creator 的云端后端服务，基于 fastapi_best_architecture 框架构建。

### 核心定位

- 提供云端 Agent 执行能力
- 管理云端浏览器池
- 凭证同步服务
- 用户认证与授权
- 订阅与计费

### 技术栈

- **框架**: FastAPI + SQLAlchemy 2.0 + Pydantic v2
- **数据库**: PostgreSQL + Redis
- **任务队列**: Celery
- **存储**: MinIO/S3
- **搜索**: Meilisearch

---

## 🏗️ 目录结构

```
services/cloud-backend/
├── pyproject.toml                       # 包配置
├── README.md                            # 包说明
├── CLAUDE.md                            # 本文档
│
└── backend/                             # 源代码
    ├── __init__.py                      # 版本信息
    ├── cli.py                           # CLI 工具
    │
    ├── app/                             # 应用层
    │   ├── main.py                      # FastAPI 应用入口
    │   │
    │   ├── api/                         # API 路由
    │   │   ├── v1/                      # API v1
    │   │   │   ├── auth.py              # 认证接口
    │   │   │   ├── agent.py             # Agent 接口
    │   │   │   ├── credential.py        # 凭证接口
    │   │   │   └── llm.py               # LLM 接口
    │   │   └── router.py                # 路由注册
    │   │
    │   ├── agent/                       # Agent 模块
    │   │   ├── __init__.py
    │   │   ├── executor.py              # CloudExecutor
    │   │   └── tools/                   # 云端工具
    │   │       ├── __init__.py
    │   │       └── browser.py           # 云端浏览器工具
    │   │
    │   ├── credential/                  # 凭证模块
    │   │   ├── __init__.py
    │   │   ├── model.py                 # 数据模型
    │   │   ├── schema.py                # Pydantic Schema
    │   │   ├── service.py               # 业务服务
    │   │   └── api.py                   # REST API
    │   │
    │   ├── services/                    # 业务服务
    │   │   ├── __init__.py
    │   │   └── browser_pool.py          # 浏览器池管理
    │   │
    │   ├── models/                      # 数据模型
    │   │   ├── __init__.py
    │   │   ├── user.py                  # 用户模型
    │   │   └── subscription.py          # 订阅模型
    │   │
    │   └── task/                        # 异步任务
    │       ├── __init__.py
    │       └── agent_task.py            # Agent 任务
    │
    ├── plugin/                          # 插件系统
    │   ├── oauth2/                      # OAuth2 插件
    │   ├── notice/                      # 通知插件
    │   ├── email/                       # 邮件插件
    │   └── config/                      # 配置插件
    │
    └── alembic/                         # 数据库迁移
        ├── versions/                    # 迁移脚本
        └── env.py                       # Alembic 配置
```

---

## 🔧 核心功能

### 1. CloudExecutor

**文件**: `backend/app/agent/executor.py`

**功能**:
- 加载 Graph 定义
- 执行 Graph 节点
- 调用云端工具
- 事件流推送

**特性**:
- 支持同步/异步/流式执行
- 成本追踪
- 超时控制
- 错误处理

### 2. 凭证同步服务

**文件**: `backend/app/credential/`

**功能**:
- 凭证加密存储
- 凭证同步
- 凭证访问控制
- 审计日志

**API 端点**:
- `POST /api/v1/credential/sync` - 同步凭证
- `GET /api/v1/credential/list` - 列出凭证
- `DELETE /api/v1/credential/{id}` - 删除凭证
- `POST /api/v1/credential/revoke-all` - 撤销所有凭证

### 3. 浏览器池管理

**文件**: `backend/app/services/browser_pool.py`

**功能**:
- 实例池化复用
- 平台隔离
- 自动清理空闲实例
- 健康检查

**特性**:
- 容器化隔离
- 资源限制
- 熔断降级
- 自动扩缩容

### 4. Agent API

**文件**: `backend/app/api/v1/agent.py`

**API 端点**:
- `POST /api/v1/agent/run` - 执行 Graph
- `GET /api/v1/agent/run/{run_id}` - 查询执行状态
- `GET /api/v1/agent/run/{run_id}/events` - SSE 事件流
- `POST /api/v1/agent/graphs` - 列出可用 Graph

---

## 📦 依赖管理

### pyproject.toml

```toml
[project]
name = "fastapi_best_architecture"
requires-python = ">=3.10"

dependencies = [
    "alembic>=1.17.2",
    "fastapi[standard-no-fastapi-cloud-cli]>=0.123.5",
    "sqlalchemy[asyncio]>=2.0.44",
    "celery>=5.6.0",
    "redis[hiredis]>=7.1.0",
    "litellm>=1.0.0",
]
```

---

## 🧪 开发

### 启动服务

```bash
# 开发模式
cd services/cloud-backend
uv run uvicorn backend.app.main:app --reload

# 生产模式
uv run granian backend.app.main:app --workers 4
```

### 数据库迁移

```bash
# 生成迁移脚本
uv run alembic revision --autogenerate -m "description"

# 执行迁移
uv run alembic upgrade head

# 回滚迁移
uv run alembic downgrade -1
```

---

## 🔗 关键文件

| 文件 | 说明 | 优先级 |
|------|------|--------|
| `app/main.py` | FastAPI 应用入口 | P0 |
| `app/agent/executor.py` | CloudExecutor | P0 |
| `app/agent/tools/browser.py` | 云端浏览器工具 | P0 |
| `app/credential/service.py` | 凭证同步服务 | P0 |
| `app/services/browser_pool.py` | 浏览器池管理 | P0 |
| `app/api/v1/agent.py` | Agent API | P0 |
| `app/api/v1/credential.py` | 凭证 API | P0 |

---

## 📚 相关文档

- [云端服务设计](../../docs/04-云端服务设计.md)
- [Agent Runtime](../../docs/05-Agent-Runtime.md)
- [开发规范](../../docs/11-开发规范.md)

---

## 🔼 导航

[← 返回根目录](../../CLAUDE.md)
