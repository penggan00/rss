# RSS Bot 依赖环境镜像

多架构 Docker 镜像 (x86/ARM)，仅包含 Python 运行环境。

## 使用方法

1. 确保本地有 `rss.py` 和 `.env` 文件
2. 运行容器：

```bash
docker run -d \
  --name rss-bot \
  -v $(pwd)/rss.py:/app/rss.py \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/data:/app/data \
  yourusername/rss-bot-deps:latest
```

## 构建说明

镜像会自动通过 GitHub Actions 构建并推送到 Docker Hub。