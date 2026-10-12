# 云端容器镜像  |  container image for cloud deployment
FROM python:3.12-slim

# 不写 .pyc、日志实时输出（便于平台收集）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOST=0.0.0.0 \
    DATA_DIR=/data \
    LOG_DIR=/data/logs

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 数据目录（云上应挂载持久磁盘到 /data，否则实例重启后需重新抓取）
# 首次启动若为空，AUTO_SEED=1 会自动抓取（约 90 秒）。
RUN mkdir -p /data/logs
VOLUME ["/data"]

EXPOSE 8080

# 健康检查：容器平台据此判断实例是否就绪
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
  CMD python -c "import os,urllib.request; \
urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz',timeout=5)"

# 用 gunicorn 生产模式启动（不要用 Flask 自带开发服务器）
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
