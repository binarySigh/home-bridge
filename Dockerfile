FROM python:3.12-slim

LABEL description="Home Bridge — ESP32 bridge service with system monitoring MCP server"
LABEL maintainer="home-bridge"

# 创建非 root 用户
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash appuser

# 安装系统依赖（websocket 等不需要编译）
RUN apt-get update && apt-get install -y --no-install-recommends \
    procps \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 复制依赖文件并安装 Python 包
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 切换到非 root 用户
USER appuser

# WebSocket 服务端口
EXPOSE 8899
# MCP SSE 端口
EXPOSE 8898

# 默认启动 WebSocket 桥接服务（main.py）
# 如需启动 MCP 监控服务，覆盖 CMD 为: python monitor_mcp.py
CMD ["python", "main.py"]