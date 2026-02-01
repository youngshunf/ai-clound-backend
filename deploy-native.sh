#!/bin/bash
# Clound-Backend 原生部署脚本（不使用 Docker）

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# 配置变量
PROJECT_DIR="$(pwd)"
SERVICE_NAME="creator-flow-backend"
DEPLOY_BRANCH="creator-flow"
API_PORT="8020"
FLOWER_PORT="8556"
# 动态设置服务用户：root 用户运行时使用 root，否则使用 www-data
if [ "$USER" = "root" ] || [ "$(id -u)" = "0" ]; then
    SERVICE_USER="root"
else
    SERVICE_USER="www-data"
fi
PYTHON_VERSION="3.12"
VENV_DIR="$PROJECT_DIR/.venv"
LOG_DIR="$PROJECT_DIR/logs"
PID_DIR="$PROJECT_DIR/pids"

# 检查系统环境
check_system() {
    log_step "检查系统环境..."
    
    # 检查操作系统
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        log_info "操作系统: $NAME $VERSION"
    else
        log_error "无法识别操作系统"
        exit 1
    fi
    
    # 检查用户（允许 root 或具有 sudo 权限的用户）
    if [ "$USER" != "root" ] && [ "$(id -u)" != "0" ]; then
        if ! sudo -n true 2>/dev/null; then
            log_warn "当前用户可能需要 sudo 权限来配置 Supervisor"
        fi
    fi
    
    log_info "当前运行用户: $USER, 服务用户: $SERVICE_USER"
    
    # 检查项目目录和文件
    if [ ! -f "backend/main.py" ]; then
        log_error "请在 clound-backend 项目根目录运行此脚本"
        log_error "当前目录: $(pwd)"
        exit 1
    fi
    
    # 更新项目目录为当前目录
    PROJECT_DIR="$(pwd)"
    VENV_DIR="$PROJECT_DIR/.venv"
    
    # 检查配置文件
    if [ ! -f "backend/.env.prod" ]; then
        log_error "配置文件不存在: backend/.env.prod"
        exit 1
    fi
    
    log_info "✅ 系统环境检查完成"
}

# 安装系统依赖（不包括 Python，Python 由 uv 管理）
install_system_deps() {
    log_step "安装系统依赖..."
    
    if [ "$USER" != "root" ]; then
        log_warn "需要 root 权限安装系统依赖，请手动安装："
        echo "  sudo apt update"
        echo "  sudo apt install -y build-essential libpq-dev pkg-config curl"
        echo "  sudo apt install -y supervisor redis-tools postgresql-client"
        echo ""
        echo "  # Python 由 uv 自动管理，无需手动安装"
        return
    fi
    
    # 更新包列表
    apt update
    
    # 安装基础开发工具（编译依赖）
    apt install -y build-essential libpq-dev pkg-config curl
    
    # 安装进程管理
    apt install -y supervisor
    
    # 安装数据库客户端（用于测试连接）
    apt install -y redis-tools postgresql-client 2>/dev/null || log_warn "数据库客户端安装失败，跳过"
    
    log_info "✅ 系统依赖安装完成"
    log_info "   Python 将由 uv 自动下载并管理"
}

# 设置 Python 环境（使用 uv 管理）
setup_python_env() {
    log_step "设置 Python 环境..."
    
    # 检查并安装 uv
    if ! command -v uv >/dev/null 2>&1; then
        log_info "安装 uv 包管理器..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
    
    log_info "uv 版本: $(uv --version)"
    
    # 使用 uv 创建虚拟环境并安装指定 Python 版本
    log_info "使用 uv 创建 Python $PYTHON_VERSION 环境..."
    
    # uv 会自动下载并安装指定版本的 Python
    if [ -f "pyproject.toml" ]; then
        log_info "使用 pyproject.toml 安装依赖..."
        
        # uv sync 会自动创建虚拟环境并安装依赖
        if uv sync --python $PYTHON_VERSION --group server; then
            log_info "✅ uv sync 成功"
        else
            log_warn "uv sync --group server 失败，尝试不使用 group..."
            if uv sync --python $PYTHON_VERSION; then
                log_info "✅ uv sync 成功"
            else
                log_error "uv sync 失败"
                exit 1
            fi
        fi
    else
        log_error "未找到 pyproject.toml 文件"
        exit 1
    fi
    
    # 更新 VENV_DIR 路径（uv 默认创建在 .venv）
    VENV_DIR="$PROJECT_DIR/.venv"
    
    # 激活虚拟环境
    source "$VENV_DIR/bin/activate"
    
    # 验证关键依赖是否安装成功
    log_info "验证依赖安装..."
    
    # 设置 Python 路径
    export PYTHONPATH="$PROJECT_DIR/backend:$PYTHONPATH"
    
    # 检查关键 Python 包
    local critical_packages=("fastapi" "uvicorn" "celery" "flower" "loguru" "pydantic_settings" "redis" "psycopg" "sqlalchemy")
    local missing_deps=()
    
    for package in "${critical_packages[@]}"; do
        if ! python -c "import $package" 2>/dev/null; then
            missing_deps+=("$package")
        fi
    done
    
    if [ ${#missing_deps[@]} -gt 0 ]; then
        log_warn "缺少关键依赖: ${missing_deps[*]}"
        log_info "尝试重新安装项目依赖..."
        
        # 强制重新安装项目
        if [ -f "pyproject.toml" ]; then
            pip install -e . --force-reinstall --no-deps
            pip install -e .
        fi
        
        # 再次验证
        local still_missing=()
        for package in "${missing_deps[@]}"; do
            if ! python -c "import $package" 2>/dev/null; then
                still_missing+=("$package")
            fi
        done
        
        if [ ${#still_missing[@]} -gt 0 ]; then
            log_error "❌ 关键依赖仍然缺失: ${still_missing[*]}"
            log_info "尝试显示详细错误信息..."
            for package in "${still_missing[@]}"; do
                echo "测试导入 $package:"
                python -c "import $package" 2>&1 || true
                echo "---"
            done
            exit 1
        fi
    fi
    
    # 验证可执行文件
    if ! command -v uvicorn >/dev/null 2>&1 || ! command -v celery >/dev/null 2>&1; then
        log_warn "可执行文件缺失，检查安装..."
        
        # 检查虚拟环境中的可执行文件
        if [ -f "$VENV_DIR/bin/uvicorn" ] && [ -f "$VENV_DIR/bin/celery" ]; then
            log_info "✅ 可执行文件存在于虚拟环境中"
        else
            log_error "❌ 可执行文件缺失"
            ls -la "$VENV_DIR/bin/" | grep -E "(uvicorn|celery)" || true
            exit 1
        fi
    fi
    
    # 测试 Celery 应用导入
    log_info "测试 Celery 应用导入..."
    
    if python -c "from backend.app.task.celery import celery_app; print('Celery app loaded successfully')" 2>/dev/null; then
        log_info "✅ Celery 应用导入成功"
    else
        log_error "❌ Celery 应用导入失败"
        log_info "错误详情："
        python -c "from backend.app.task.celery import celery_app" 2>&1 || true
        
        log_info "检查项目结构..."
        ls -la backend/app/ 2>/dev/null || true
        ls -la backend/app/task/ 2>/dev/null || true
        
        exit 1
    fi
    
    log_info "✅ 所有依赖验证成功"
    
    log_info "✅ Python 环境设置完成"
}

# 配置环境变量
setup_config() {
    log_step "配置环境变量..."
    
    # 备份现有配置
    if [ -f "backend/.env" ]; then
        cp backend/.env backend/.env.backup-$(date +%Y%m%d-%H%M%S)
    fi
    
    # 复制生产配置
    cp backend/.env.prod backend/.env
    
    # 创建数据库（PostgreSQL）
    log_info "检查数据库连接..."
    source backend/.env
    db_host=$(echo $DATABASE_HOST | tr -d "'\"")
    db_port=$(echo $DATABASE_PORT | tr -d "'\"")
    db_user=$(echo $DATABASE_USER | tr -d "'\"")
    db_name=$(echo $DATABASE_SCHEMA | tr -d "'\"")
    
    # 测试 PostgreSQL 连接
    if command -v psql &> /dev/null; then
        if PGPASSWORD="$DATABASE_PASSWORD" psql -h "$db_host" -p "$db_port" -U "$db_user" -d "$db_name" -c "SELECT 1" &>/dev/null; then
            log_info "✅ PostgreSQL 连接成功"
        else
            log_warn "⚠️ PostgreSQL 连接失败，请检查数据库配置"
        fi
    else
        log_warn "psql 客户端未安装，跳过数据库连接测试"
    fi
    
    log_info "✅ 配置完成"
}

# 创建目录结构
create_directories() {
    log_step "创建目录结构..."
    
    # 创建 Supervisor 日志目录
    mkdir -p "$LOG_DIR"
    if [ "$USER" = "root" ]; then
        chown -R "$SERVICE_USER:$SERVICE_USER" "$LOG_DIR" 2>/dev/null || true
    fi
    
    # 创建应用程序日志目录
    mkdir -p "$PROJECT_DIR/backend/log"
    if [ "$USER" = "root" ]; then
        chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR/backend/log" 2>/dev/null || true
    fi
    
    # 创建 PID 目录
    mkdir -p "$PID_DIR"
    if [ "$USER" = "root" ]; then
        chown -R "$SERVICE_USER:$SERVICE_USER" "$PID_DIR" 2>/dev/null || true
    fi
    
    # 创建静态文件目录
    mkdir -p "$PROJECT_DIR/backend/static/upload"
    mkdir -p "$PROJECT_DIR/backend/app/static"
    
    # 确保目录权限正确
    if [ "$USER" = "root" ]; then
        chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR/backend/static" 2>/dev/null || true
        chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR/backend/log" 2>/dev/null || true
    else
        # 非 root 用户，确保当前用户有写权限
        chmod -R 755 "$PROJECT_DIR/backend/log" 2>/dev/null || true
        chmod -R 755 "$PROJECT_DIR/backend/static" 2>/dev/null || true
    fi
    
    log_info "✅ 目录结构创建完成"
    log_info "Supervisor 日志目录: $LOG_DIR"
    log_info "应用程序日志目录: $PROJECT_DIR/backend/log"
    log_info "PID 目录: $PID_DIR"
}

# 验证虚拟环境
verify_venv() {
    log_step "验证虚拟环境..."
    
    # 检查虚拟环境目录
    if [ ! -d "$VENV_DIR" ]; then
        log_error "虚拟环境目录不存在: $VENV_DIR"
        return 1
    fi
    
    # 检查关键可执行文件 (uv 环境不需要 pip)
    local required_bins=("python" "fba" "celery")
    local missing_bins=()
    
    for bin in "${required_bins[@]}"; do
        if [ ! -f "$VENV_DIR/bin/$bin" ]; then
            missing_bins+=("$bin")
        fi
    done
    
    if [ ${#missing_bins[@]} -gt 0 ]; then
        log_error "虚拟环境中缺少可执行文件: ${missing_bins[*]}"
        log_info "虚拟环境路径: $VENV_DIR"
        log_info "请检查依赖安装是否成功"
        return 1
    fi
    
    log_info "✅ 虚拟环境验证成功"
    return 0
}

# 配置 Supervisor
setup_supervisor() {
    log_step "配置 Supervisor..."
    
    # FastAPI 服务配置 - 使用 fba run 命令
    sudo tee /etc/supervisor/conf.d/${SERVICE_NAME}-api.conf > /dev/null << EOF
[program:${SERVICE_NAME}-api]
command=$VENV_DIR/bin/fba run --host 0.0.0.0 --port $API_PORT --no-reload --workers 1
directory=$PROJECT_DIR
user=$SERVICE_USER
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=$LOG_DIR/api.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=10
environment=PATH="$VENV_DIR/bin",PYTHONPATH="$PROJECT_DIR/backend"
startsecs=10
startretries=3
EOF

    # Celery Worker 配置
    sudo tee /etc/supervisor/conf.d/${SERVICE_NAME}-worker.conf > /dev/null << EOF
[program:${SERVICE_NAME}-worker]
command=$VENV_DIR/bin/celery -A backend.app.task.celery:celery_app worker -l info -c 4 -Q celery
directory=$PROJECT_DIR
user=$SERVICE_USER
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=$LOG_DIR/worker.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=10
stderr_logfile=$LOG_DIR/worker_error.log
stderr_logfile_maxbytes=50MB
stderr_logfile_backups=10
environment=PATH="$VENV_DIR/bin",PYTHONPATH="$PROJECT_DIR/backend"
startsecs=10
startretries=3
EOF

    # Celery Beat 配置
    sudo tee /etc/supervisor/conf.d/${SERVICE_NAME}-beat.conf > /dev/null << EOF
[program:${SERVICE_NAME}-beat]
command=$VENV_DIR/bin/celery -A backend.app.task.celery:celery_app beat -l info
directory=$PROJECT_DIR
user=$SERVICE_USER
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=$LOG_DIR/beat.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=10
stderr_logfile=$LOG_DIR/beat_error.log
stderr_logfile_maxbytes=50MB
stderr_logfile_backups=10
environment=PATH="$VENV_DIR/bin",PYTHONPATH="$PROJECT_DIR/backend"
startsecs=10
startretries=3
EOF

    # Celery Flower 配置
    sudo tee /etc/supervisor/conf.d/${SERVICE_NAME}-flower.conf > /dev/null << EOF
[program:${SERVICE_NAME}-flower]
command=$VENV_DIR/bin/celery -A backend.app.task.celery:celery_app flower --port=$FLOWER_PORT --basic-auth=admin:123456
directory=$PROJECT_DIR
user=$SERVICE_USER
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=$LOG_DIR/flower.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=10
stderr_logfile=$LOG_DIR/flower_error.log
stderr_logfile_maxbytes=50MB
stderr_logfile_backups=10
environment=PATH="$VENV_DIR/bin",PYTHONPATH="$PROJECT_DIR/backend"
startsecs=10
startretries=3
EOF

    # 确保 Supervisor 服务运行
    sudo systemctl start supervisor || log_warn "Supervisor 启动失败"
    
    # 等待 Supervisor 启动
    sleep 2
    
    # 重新加载 Supervisor 配置
    log_info "重新加载 Supervisor 配置..."
    sudo supervisorctl reread
    sudo supervisorctl update
    
    # 验证配置是否加载成功
    log_info "验证服务配置..."
    if sudo supervisorctl status | grep -q "$SERVICE_NAME"; then
        log_info "✅ Supervisor 配置加载成功"
    else
        log_warn "⚠️  Supervisor 配置可能未正确加载"
    fi
    
    log_info "✅ Supervisor 配置完成"
}

# 显示 Nginx 配置建议
show_nginx_config() {
    log_step "Nginx 配置建议..."
    
    echo ""
    echo "📝 请在服务器统一 Nginx 中添加以下配置："
    echo ""
    echo "# Clound-Backend 服务配置"
    echo "location /api/ {"
    echo "    proxy_pass http://127.0.0.1:$API_PORT/;"
    echo "    proxy_set_header Host \$host;"
    echo "    proxy_set_header X-Real-IP \$remote_addr;"
    echo "    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;"
    echo "    proxy_set_header X-Forwarded-Proto \$scheme;"
    echo "    proxy_connect_timeout 60s;"
    echo "    proxy_send_timeout 60s;"
    echo "    proxy_read_timeout 60s;"
    echo "}"
    echo ""
    echo "# 静态文件配置"
    echo "location /static/ {"
    echo "    alias $PROJECT_DIR/backend/static/;"
    echo "    expires 30d;"
    echo "    add_header Cache-Control \"public, immutable\";"
    echo "}"
    echo ""
    echo "# Flower 监控配置"
    echo "location /flower/ {"
    echo "    proxy_pass http://127.0.0.1:$FLOWER_PORT/;"
    echo "    proxy_set_header Host \$host;"
    echo "    proxy_set_header X-Real-IP \$remote_addr;"
    echo "    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;"
    echo "    proxy_set_header X-Forwarded-Proto \$scheme;"
    echo "}"
    echo ""
}

# 停止现有服务
stop_existing_services() {
    log_step "停止现有服务..."
    
    # 检查 Supervisor 是否运行
    if systemctl is-active --quiet supervisor; then
        log_info "停止现有的应用服务..."
        
        # 停止所有相关服务
        sudo supervisorctl stop ${SERVICE_NAME}-api 2>/dev/null || true
        sudo supervisorctl stop ${SERVICE_NAME}-worker 2>/dev/null || true
        sudo supervisorctl stop ${SERVICE_NAME}-beat 2>/dev/null || true
        sudo supervisorctl stop ${SERVICE_NAME}-flower 2>/dev/null || true
        
        log_info "等待服务完全停止..."
        sleep 3
    else
        log_info "Supervisor 未运行，跳过服务停止"
    fi
    
    # 清理 PID 文件
    sudo rm -f $PID_DIR/celerybeat.pid 2>/dev/null || true
    
    log_info "✅ 现有服务已停止"
}

# 启动服务
start_services() {
    log_step "启动服务..."
    
    # 启动 Supervisor
    sudo systemctl start supervisor
    sudo systemctl enable supervisor
    
    # 等待 Supervisor 启动
    sleep 2
    
    # 重新加载配置并更新程序
    log_info "重新加载 Supervisor 配置..."
    sudo supervisorctl reread
    sudo supervisorctl update
    
    # 等待配置更新完成
    sleep 2
    
    # 启动所有服务
    log_info "启动应用服务..."
    sudo supervisorctl start ${SERVICE_NAME}-api || log_warn "API 服务启动失败"
    sudo supervisorctl start ${SERVICE_NAME}-worker || log_warn "Worker 服务启动失败"
    sudo supervisorctl start ${SERVICE_NAME}-beat || log_warn "Beat 服务启动失败"
    sudo supervisorctl start ${SERVICE_NAME}-flower || log_warn "Flower 服务启动失败"
    
    # 等待服务启动
    log_info "等待服务启动..."
    sleep 10
    
    log_info "✅ 服务启动完成"
}

# 检查服务日志
check_service_logs() {
    log_step "检查服务日志..."
    
    local services=("api" "worker" "beat" "flower")
    
    for service in "${services[@]}"; do
        local log_file="$LOG_DIR/${service}.log"
        echo ""
        echo "📋 ${service} 服务日志 (最后 10 行):"
        if [ -f "$log_file" ]; then
            tail -n 10 "$log_file" || echo "无法读取日志文件"
        else
            echo "日志文件不存在: $log_file"
        fi
        echo "---"
    done
}

# 检查服务状态
check_services() {
    log_step "检查服务状态..."
    
    echo ""
    echo "📊 Supervisor 服务状态："
    local status_output
    if status_output=$(sudo supervisorctl status 2>/dev/null); then
        echo "$status_output"
        echo ""
        
        # 检查是否有 FATAL 状态的服务
        if echo "$status_output" | grep -q "FATAL"; then
            log_warn "发现服务启动失败，检查日志..."
            check_service_logs
            return 1
        fi
    else
        log_warn "无法获取 Supervisor 状态"
        return 1
    fi
    
    echo "🔗 服务连接测试："
    
    # 等待服务完全启动
    log_info "等待服务完全启动..."
    sleep 5
    
    # 测试 API 服务
    local api_attempts=0
    while [ $api_attempts -lt 3 ]; do
        if curl -s -f http://localhost:$API_PORT/docs > /dev/null 2>&1; then
            log_info "✅ API 服务正常 (http://localhost:$API_PORT)"
            break
        else
            api_attempts=$((api_attempts + 1))
            if [ $api_attempts -lt 3 ]; then
                log_info "API 服务未就绪，等待中... ($api_attempts/3)"
                sleep 5
            else
                log_warn "❌ API 服务异常，检查端口占用..."
                netstat -tlnp | grep :$API_PORT || log_warn "端口 $API_PORT 未被占用"
                
                # 显示 API 服务日志
                echo ""
                echo "📋 API 服务错误日志:"
                tail -n 20 "$LOG_DIR/api.log" 2>/dev/null || echo "无法读取 API 日志"
            fi
        fi
    done
    
    # 测试 Flower
    local flower_attempts=0
    while [ $flower_attempts -lt 3 ]; do
        if curl -s -f http://localhost:$FLOWER_PORT > /dev/null 2>&1; then
            log_info "✅ Flower 监控正常 (http://localhost:$FLOWER_PORT)"
            break
        else
            flower_attempts=$((flower_attempts + 1))
            if [ $flower_attempts -lt 3 ]; then
                log_info "Flower 服务未就绪，等待中... ($flower_attempts/3)"
                sleep 5
            else
                log_warn "❌ Flower 监控异常"
            fi
        fi
    done
}

# 显示部署信息
show_deployment_info() {
    log_step "部署完成！"
    
    echo ""
    echo "🎉 Clound-Backend 一键部署成功！"
    echo ""
    echo "📋 服务信息："
    echo "   🌐 API 服务: http://localhost:$API_PORT"
    echo "   📚 API 文档: http://localhost:$API_PORT/docs"
    echo "   🌸 Flower 监控: http://localhost:$FLOWER_PORT"
    echo ""
    echo "📁 重要目录："
    echo "   项目目录: $PROJECT_DIR"
    echo "   虚拟环境: $VENV_DIR"
    echo "   日志目录: $LOG_DIR"
    echo "   PID 目录: $PID_DIR"
    echo ""
    echo "🔧 管理命令："
    echo "   初始化环境: ./deploy-native.sh --init"
    echo "   更新服务: ./deploy-native.sh --update"
    echo "   重启服务: ./deploy-native.sh --restart"
    echo "   检查状态: ./deploy-native.sh --check"
    echo "   停止服务: ./deploy-native.sh --stop"
    echo "   查看所有服务: sudo supervisorctl status"
    echo "   查看日志: tail -f $LOG_DIR/api.log"

    echo ""
    echo "📝 配置文件："
    echo "   Supervisor: /etc/supervisor/conf.d/${SERVICE_NAME}-*.conf"
    echo "   环境变量: $PROJECT_DIR/backend/.env"
    echo ""
}

# 快速更新依赖（确保 uv 可用）
update_dependencies() {
    log_step "更新 Python 依赖..."
    
    # 激活虚拟环境
    source "$VENV_DIR/bin/activate"
    
    # 设置 Python 路径
    export PYTHONPATH="$PROJECT_DIR/backend:$PYTHONPATH"
    
    # 检查 uv 是否可用，如果不可用则安装
    if ! command -v uv >/dev/null 2>&1; then
        log_info "uv 不可用，正在安装..."
        pip install uv
        
        # 再次验证 uv 安装
        if ! command -v uv >/dev/null 2>&1; then
            log_error "uv 安装失败"
            exit 1
        fi
        log_info "uv 安装成功: $(uv --version)"
    else
        log_info "使用现有的 uv: $(uv --version)"
    fi
    
    # 使用 uv 同步依赖
    if [ -f "pyproject.toml" ]; then
        log_info "使用 uv 同步依赖..."
        if uv sync --group server; then
            log_info "✅ uv sync 成功"
        else
            log_warn "uv sync 失败，尝试不使用组..."
            if uv sync; then
                log_info "✅ uv sync (无组) 成功"
            else
                log_warn "uv sync 完全失败，尝试使用 pip 安装..."
                
                # 使用 uv pip 安装项目依赖
                if uv pip install -e .; then
                    log_info "✅ uv pip install 成功"
                else
                    log_warn "uv pip 失败，使用标准 pip..."
                    pip install -e .
                fi
            fi
        fi
    else
        log_error "未找到 pyproject.toml 文件"
        exit 1
    fi
    
    # 确保 pip 可用（插件系统需要）
    log_info "确保 pip 可用..."
    if ! python -m pip --version >/dev/null 2>&1; then
        log_info "安装 pip..."
        python -m ensurepip --upgrade 2>/dev/null || {
            log_warn "ensurepip 失败，使用 uv 安装 pip..."
            uv pip install pip
        }
    fi
    
    # 安装插件依赖
    log_info "检查并安装插件依赖..."
    if [ -d "backend/plugin" ]; then
        for plugin_dir in backend/plugin/*/; do
            if [ -f "${plugin_dir}requirements.txt" ]; then
                plugin_name=$(basename "$plugin_dir")
                log_info "安装插件 $plugin_name 依赖..."
                python -m pip install -r "${plugin_dir}requirements.txt" -q 2>/dev/null || {
                    log_warn "插件 $plugin_name 依赖安装失败，将在运行时尝试"
                }
            fi
        done
    fi
    
    # 快速验证关键依赖
    log_info "验证关键依赖..."
    
    # 检查关键 Python 包
    local critical_packages=("fastapi" "celery" "loguru")
    local missing_deps=()
    
    for package in "${critical_packages[@]}"; do
        if ! python -c "import $package" 2>/dev/null; then
            missing_deps+=("$package")
        fi
    done
    
    if [ ${#missing_deps[@]} -gt 0 ]; then
        log_warn "缺少关键依赖: ${missing_deps[*]}"
        log_info "尝试重新安装项目依赖..."
        python -m pip install -e . --force-reinstall --no-deps
        python -m pip install -e .
    fi
    
    # 测试 Celery 应用导入
    if ! python -c "from backend.app.task.celery import celery_app" 2>/dev/null; then
        log_warn "Celery 应用导入失败，可能需要重新安装依赖"
    fi
    
    log_info "✅ 依赖更新完成"
}

# 初始化环境（完整安装）
init_environment() {
    log_info "🔧 初始化服务器环境..."
    install_system_deps
    setup_python_env
    verify_venv
    setup_config
    create_directories
    fix_permissions
    setup_supervisor
    log_info "✅ 环境初始化完成"
}

# 更新代码
update_code() {
    log_step "更新代码..."
    
    # 检查是否是 Git 仓库
    if [ ! -d ".git" ]; then
        log_warn "当前目录不是 Git 仓库，跳过代码更新"
        return
    fi
    
    # 检查当前分支
    current_branch=$(git branch --show-current 2>/dev/null || echo "unknown")
    log_info "当前分支: $current_branch"
    
    # 保存本地更改（如果有）
    if ! git diff --quiet || ! git diff --cached --quiet; then
        log_info "发现本地更改，保存到 stash..."
        git stash push -m "Auto stash before update $(date '+%Y-%m-%d %H:%M:%S')"
    fi
    
    # 切换到部署分支并拉取最新代码
    log_info "切换到部署分支: $DEPLOY_BRANCH"
    git checkout "$DEPLOY_BRANCH" || {
        log_error "无法切换到分支 $DEPLOY_BRANCH"
        exit 1
    }
    
    log_info "拉取最新代码 (分支: $DEPLOY_BRANCH)..."
    if git pull origin "$DEPLOY_BRANCH"; then
        log_info "✅ 代码更新成功"
    else
        log_error "❌ 代码更新失败"
        
        # 尝试恢复 stash
        if git stash list | grep -q "Auto stash before update"; then
            log_info "尝试恢复本地更改..."
            git stash pop
        fi
        
        exit 1
    fi
    
    # 显示更新信息
    log_info "最新提交: $(git log -1 --oneline)"
}

# 修复权限问题
fix_permissions() {
    log_step "修复权限问题..."
    
    # 确保项目目录权限正确
    if [ "$USER" = "root" ]; then
        log_info "设置项目目录所有者为 $SERVICE_USER..."
        chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR" 2>/dev/null || true
    else
        log_info "设置目录权限..."
        # 确保当前用户有读写权限
        find "$PROJECT_DIR" -type d -exec chmod 755 {} \; 2>/dev/null || true
        find "$PROJECT_DIR" -type f -exec chmod 644 {} \; 2>/dev/null || true
        
        # 确保可执行文件有执行权限
        chmod +x "$PROJECT_DIR/deploy-native.sh" 2>/dev/null || true
        find "$PROJECT_DIR" -name "*.sh" -exec chmod +x {} \; 2>/dev/null || true
        
        # 确保虚拟环境可执行文件有权限
        if [ -d "$VENV_DIR/bin" ]; then
            chmod +x "$VENV_DIR/bin/"* 2>/dev/null || true
        fi
    fi
    
    log_info "✅ 权限修复完成"
}

# 更新服务（快速更新）
update_service() {
    log_info "🔄 更新服务..."
    
    # 检查虚拟环境是否存在
    if [ ! -d "$VENV_DIR" ]; then
        log_error "虚拟环境不存在，请先运行 --init 初始化环境"
        exit 1
    fi
    
    # 停止服务
    stop_existing_services
    
    # 更新代码
    update_code
    
    # 确保目录结构存在
    create_directories
    
    # 修复权限问题
    fix_permissions
    
    # 更新依赖
    update_dependencies
    
    # 更新配置
    setup_config
    
    # 重新配置 Supervisor（可能有配置变更）
    setup_supervisor
    
    # 启动服务
    start_services
    
    log_info "✅ 服务更新完成"
}

# 显示帮助
show_help() {
    echo "Clound-Backend 一键部署脚本"
    echo ""
    echo "用法: $0 [选项]"
    echo ""
    echo "🚀 主要命令:"
    echo "  --init            初始化服务器环境（首次部署）"
    echo "  --update          更新服务（代码/依赖更新）"
    echo "  --restart         重启所有服务"
    echo "  --stop            停止所有服务"
    echo "  --check           检查服务状态"
    echo ""
    echo "🔧 其他选项:"
    echo "  -h, --help        显示帮助信息"
    echo ""
    echo "📋 服务组件:"
    echo "  - FastAPI 应用服务器 (端口 $API_PORT)"
    echo "  - Celery Worker 任务处理"
    echo "  - Celery Beat 定时任务"
    echo "  - Celery Flower 监控 (端口 $FLOWER_PORT)"
    echo ""
    echo "💡 使用场景:"
    echo "  首次部署:     ./deploy-native.sh --init"
    echo "  代码更新:     ./deploy-native.sh --update"
    echo "  依赖更新:     ./deploy-native.sh --update"
    echo "  配置更新:     ./deploy-native.sh --update"
    echo "  重启服务:     ./deploy-native.sh --restart"
    echo "  检查状态:     ./deploy-native.sh --check"
    echo ""
    echo "🔍 详细功能:"
    echo ""
    echo "  --init (初始化环境):"
    echo "    ✓ 安装系统依赖 (Python, Supervisor, MySQL客户端等)"
    echo "    ✓ 创建 Python 虚拟环境"
    echo "    ✓ 安装 uv 包管理器"
    echo "    ✓ 安装所有 Python 依赖"
    echo "    ✓ 配置环境变量和数据库"
    echo "    ✓ 创建目录结构"
    echo "    ✓ 配置 Supervisor 服务"
    echo "    ✓ 启动所有服务"
    echo ""
    echo "  --update (更新服务):"
    echo "    ✓ 停止现有服务"
    echo "    ✓ 使用 uv 快速同步依赖"
    echo "    ✓ 更新配置文件"
    echo "    ✓ 重新配置 Supervisor"
    echo "    ✓ 重新启动服务"
    echo ""
}

# 重启服务
restart_services() {
    log_step "重启所有服务..."
    
    # 重新加载配置
    sudo supervisorctl reread
    sudo supervisorctl update
    
    # 重启服务
    sudo supervisorctl restart ${SERVICE_NAME}-api || log_warn "API 服务重启失败"
    sudo supervisorctl restart ${SERVICE_NAME}-worker || log_warn "Worker 服务重启失败"
    sudo supervisorctl restart ${SERVICE_NAME}-beat || log_warn "Beat 服务重启失败"
    sudo supervisorctl restart ${SERVICE_NAME}-flower || log_warn "Flower 服务重启失败"
    
    sleep 5
    check_services
    log_info "✅ 服务重启完成"
}

# 停止服务
stop_services() {
    log_step "停止所有服务..."
    
    sudo supervisorctl stop ${SERVICE_NAME}-api
    sudo supervisorctl stop ${SERVICE_NAME}-worker
    sudo supervisorctl stop ${SERVICE_NAME}-beat
    sudo supervisorctl stop ${SERVICE_NAME}-flower
    
    log_info "✅ 服务已停止"
}

# 主函数
main() {
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        --init)
            log_info "🚀 开始初始化 Clound-Backend 环境..."
            check_system
            init_environment
            start_services
            check_services
            show_nginx_config
            show_deployment_info
            ;;
        --update)
            log_info "🔄 开始更新 Clound-Backend 服务..."
            check_system
            update_service
            check_services
            log_info "✅ 服务更新完成！"
            ;;
        --check|--check-only)
            check_system
            check_services
            ;;
        --restart)
            check_system
            restart_services
            ;;
        --stop)
            check_system
            stop_services
            ;;
        --logs)
            check_system
            check_service_logs
            ;;
        --fix-permissions)
            check_system
            create_directories
            fix_permissions
            log_info "✅ 权限修复完成，请重启服务: ./deploy-native.sh --restart"
            ;;
        "")
            # 无参数时显示帮助
            echo "❌ 请指定操作命令"
            echo ""
            show_help
            exit 1
            ;;
        *)
            echo "❌ 未知命令: $1"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

# 执行主函数
main "$@"