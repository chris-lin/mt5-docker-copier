FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV WINEDEBUG=-all
ENV WINEARCH=win64
ENV WINEPREFIX=/root/.wine64
ENV DISPLAY=:99

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wine \
        wine64 \
        xvfb \
        procps \
        ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /mt5

# 改用 wine 執行，保留 tail 防止閃退洗版
CMD ["sh", "-c", "rm -rf /tmp/.X* && Xvfb :99 -screen 0 1024x768x16 -nolisten tcp & sleep 2 && wine /mt5/terminal64.exe /portable /config:mt5-server.ini || tail -f /dev/null"]
