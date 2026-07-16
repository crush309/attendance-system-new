#!/bin/bash

# 切换到脚本所在目录（确保路径正确）
cd "$(dirname "$0")"

# 检查是否在 Railway 环境（通过环境变量 RAILWAY_ENVIRONMENT 判断）
if [ -n "$RAILWAY_ENVIRONMENT" ]; then
    echo "检测到 Railway 环境，使用生产模式启动..."
    # 安装依赖（Railway 通常会自动执行，但显式写更安全）
    pip install -r requirements.txt
    # 使用 uvicorn 启动，监听 Railway 分配的端口
    uvicorn backend.app:app --host 0.0.0.0 --port $PORT
else
    echo "检测到本地环境，使用开发模式启动..."
    # 本地开发：使用 Python 直接运行 app.py（适合热重载调试）
    # 如果希望使用 uvicorn，也可以改为：
    # uvicorn backend.app:app --host 127.0.0.1 --port 8080 --reload
    python backend/app.py
fi