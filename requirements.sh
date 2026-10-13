# alpine 一键下载依赖
rm -rf ~/rss/rss_venv && \
mkdir -p ~/rss && \
wget -O /tmp/rss_venv_alpine.tar.gz "https://github.com/penggan00/qq/releases/download/venv-alpine-latest/rss_venv_alpine.tar.gz" && \
tar -xzf /tmp/rss_venv_alpine.tar.gz -C ~/rss && \
rm /tmp/rss_venv_alpine.tar.gz && \
ls -la ~/rss/rss_venv

# debian 一键下载依赖
rm -rf ~/rss/rss_venv && \
mkdir -p ~/rss && \
wget -O /tmp/rss_venv_debian.tar.gz "https://github.com/penggan00/qq/releases/download/venv-debian-latest/rss_venv_debian.tar.gz" && \
tar -xzf /tmp/rss_venv_debian.tar.gz -C ~/rss && \
rm /tmp/rss_venv_debian.tar.gz && \
ls -la ~/rss/rss_venv