# 第一阶段：构建 Python 依赖
FROM python:3.11-slim as builder

WORKDIR /app

# 安装构建依赖（如某些 Python 包需要编译）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 创建虚拟环境并安装依赖
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 第二阶段：仅包含 Python 环境（不包含代码）
FROM python:3.11-slim

# 从 builder 复制 Python 虚拟环境
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 设置工作目录（但不复制代码）
WORKDIR /app

# 声明挂载点（可选，实际挂载由 docker-compose 控制）
VOLUME ["/app"]

# 设置默认命令（可被 docker-compose 覆盖）
ENTRYPOINT ["python"]
CMD ["--help"]