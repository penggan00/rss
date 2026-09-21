#rss_venv_debian
mkdir -p ~/rss && cd ~/rss && rm -rf rss_venv && wget -q --show-progress -O rss_venv_debian.tar.gz "https://github.com/penggan00/qq/releases/download/venv-debian-latest/rss_venv_debian.tar.gz" && tar -xzf rss_venv_debian.tar.gz && rm -f rss_venv_debian.tar.gz && echo "✅ 完成：$(du -sh ~/rss/rss_venv)"
#rss_venv_alpine
mkdir -p ~/rss && cd ~/rss && rm -rf rss_venv && wget -q --show-progress -O rss_venv_alpine.tar.gz "https://github.com/penggan00/qq/releases/download/venv-alpine-latest/rss_venv_alpine.tar.gz" && tar -xzf rss_venv_alpine.tar.gz && rm -f rss_venv_alpine.tar.gz && echo "✅ 完成：$(du -sh ~/rss/rss_venv)"

# openwrt aarch64
mkdir -p ~/rss && cd ~/rss && rm -rf rss_venv && wget -q --show-progress -O rss_venv_openwrt_aarch64.tar.gz "https://github.com/penggan00/qq/releases/download/venv-openwrt-aarch64-latest/rss_venv_openwrt_aarch64.tar.gz" && tar -xzf rss_venv_openwrt_aarch64.tar.gz && rm -f rss_venv_openwrt_aarch64.tar.gz && echo "✅ 完成：$(du -sh ~/rss/rss_venv)"