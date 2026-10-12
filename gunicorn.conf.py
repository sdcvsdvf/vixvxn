# gunicorn 配置  |  production WSGI server config
#
# 用法 (usage):
#     gunicorn -c gunicorn.conf.py app:app
#
# 平台会注入 PORT，这里自动读取；本地默认 8801。
import multiprocessing
import os

# 监听地址：云上必须绑 0.0.0.0，否则外部访问不到
bind = f"0.0.0.0:{os.environ.get('PORT', '8801')}"

# 定时抓取在应用内是后台线程，多 worker 会导致每个 worker 各跑一份定时器。
# 因此默认用 1 个 worker + 多线程，既够用（纯 IO 的轻量页面）又避免重复抓取。
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = int(os.environ.get("WEB_THREADS", "8"))
worker_class = "gthread"

# 首次启动可能要抓 60–120 秒数据，超时放宽，避免被平台判定为启动失败
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "300"))
graceful_timeout = 30
keepalive = 5

# 云平台通常把日志直接收走，这里输出到 stdout/stderr
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")

# 进程名，便于在平台上辨认
proc_name = "vixvxn-dashboard"

preload_app = False
