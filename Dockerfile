# 使用多架构兼容的 Python 镜像
FROM --platform=$BUILDPLATFORM python:3.9-slim as builder

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 最终阶段
FROM python:3.9-slim

# 从构建阶段复制已安装的依赖
COPY --from=builder /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 创建工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Shanghai

# 创建数据目录
RUN mkdir -p /app/data

# 设置入口点
ENTRYPOINT ["python", "/app/rss.py"]