from fastapi import APIRouter

from backend.core.conf import settings

# --- 管理端（JWT + RBAC） ---
from backend.app.hasn_quant.api.v1.admin.quant_strategy import router as admin_quant_strategy_router
from backend.app.hasn_quant.api.v1.admin.quant_backtest_run import router as admin_quant_backtest_run_router
# --- 用户端（仅 JWT） ---
from backend.app.hasn_quant.api.v1.app.quant_strategy import router as app_quant_strategy_router
from backend.app.hasn_quant.api.v1.app.quant_backtest_run import router as app_quant_backtest_run_router
# --- Agent（Agent Key） ---
from backend.app.hasn_quant.api.v1.agent.quant_strategy import router as agent_quant_strategy_router
from backend.app.hasn_quant.api.v1.agent.quant_backtest_run import router as agent_quant_backtest_run_router
# --- 公开（无需认证） ---
from backend.app.hasn_quant.api.v1.open.quant_strategy import router as open_quant_strategy_router
from backend.app.hasn_quant.api.v1.open.quant_backtest_run import router as open_quant_backtest_run_router

# ========================================
# 管理端 API（JWT + RBAC）
# 路径前缀: /api/v1/hasn_quant/
# ========================================
v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_quant', tags=['量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）管理'])

v1.include_router(admin_quant_strategy_router, prefix='/quant-strategy', tags=['量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）管理-量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）'])
v1.include_router(admin_quant_backtest_run_router, prefix='/quant/backtest/runs', tags=['回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）-回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）'])

# ========================================
# 用户端 API（仅 JWT，无 RBAC）
# 路径前缀: /api/v1/hasn_quant/app/
# ========================================
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_quant/app', tags=['量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）用户端'])

app.include_router(app_quant_strategy_router, prefix='/quant-strategy', tags=['量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）用户端-量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）'])
app.include_router(app_quant_backtest_run_router, prefix='/quant/backtest/runs', tags=['回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）-回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）'])

# ========================================
# 公开 API（无需认证）
# 路径前缀: /api/v1/hasn_quant/open/
# ========================================
open_api = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_quant/open', tags=['量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）公开'])

open_api.include_router(open_quant_strategy_router, prefix='/quant-strategy', tags=['量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）公开-量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）'])
open_api.include_router(open_quant_backtest_run_router, prefix='/quant/backtest/runs', tags=['回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）-回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）'])

# ========================================
# Agent API
# 路径前缀: /api/v1/hasn_quant/agent/
# ========================================
agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_quant/agent', tags=['量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）Agent'])

agent.include_router(agent_quant_strategy_router, prefix='/quant-strategy', tags=['量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）Agent-量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）'])
agent.include_router(agent_quant_backtest_run_router, prefix='/quant/backtest/runs', tags=['回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）-回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）'])
