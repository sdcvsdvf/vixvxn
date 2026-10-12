# 云端部署指南 · Cloud Deployment

把本项目从「跑在自己电脑上」迁到「完全云端运行」。全部使用**免费**云服务。

---

## 一分钟选型

| 平台 | 免费额度 | 休眠 | 适合 |
|---|---|---|---|
| **Render** ⭐推荐 | 750 小时/月 Web + 定时任务 | 15 分钟无访问后休眠 | 最省事，本仓库已配好 `render.yaml` |
| **Railway** | 试用额度（$5 起） | 不休眠 | 想要常驻、不介意少量费用 |
| **Fly.io** | 小额度免费 | 可保活 | 想要更快冷启动 |
| **自己的 VPS** | 24–60 元/月 | 不休眠 | 完全可控 |
| **本机 Docker** | 免费 | 不休眠 | 只想容器化，不对外 |

**免费套餐的代价**：实例休眠后，下一个人访问需要等 30–60 秒冷启动。
数据不会丢（见下方「数据持久化」），只是第一次打开慢。

---

## 方案一：Render（推荐，全免费）

### 步骤

1. **推到 GitHub**
   ```bash
   cd vixvxn
   git init && git add . && git commit -m "market dashboard"
   git remote add origin https://github.com/<你的用户名>/vixvxn.git
   git push -u origin main
   ```

2. **在 Render 创建 Blueprint**
   - 登录 <https://render.com> → **New** → **Blueprint**
   - 选择该仓库，Render 会自动读取 `render.yaml`
   - 点 **Apply**

3. **等待首次部署**
   - 构建镜像约 2–3 分钟
   - 首次启动会自动抓数据，约 **90 秒**（`healthCheckPath` 已设 `/healthz`，不会误判失败）

4. **拿到网址**
   - 形如 `https://vixvxn-dashboard.onrender.com`
   - 打开即用，无需登录

5. **（可选）设置每日更新**
   - `render.yaml` 里已定义 cron job `vixvxn-daily-update`
   - 部署后在 Render 面板给它填环境变量：
     - `TARGET_URL` = 你的 Web 服务地址
     - `ADMIN_TOKEN` = 自定义一串密钥（同时给 Web 服务也加上同名变量）

> **为什么用 cron job 而不是进程内定时器？**
> 免费实例会休眠，进程内的定时器随之停摆。Render 的 cron job 独立运行，可靠得多。
> 所以 `render.yaml` 里设了 `VIXVXN_SCHEDULER=0` 关掉进程内定时器。

### 免费套餐的休眠问题

15 分钟无访问就休眠。三种应对：

| 做法 | 说明 |
|---|---|
| **不管它** | 打开时等 30–60 秒，能用 |
| **GitHub Actions 保活** | 见 `\.github\workflows\daily-update.yml`，每天定时唤醒并更新，顺便让站点保持活跃 |
| **外部监控保活** | 用 UptimeRobot 等免费服务每 10 分钟访问一次 `/healthz` |

---

## 方案二：Docker（任意支持容器的平台）

```bash
docker build -t vixvxn .
docker run -d -p 8801:8080 \
  -e DATA_DIR=/data \
  -e VIXVXN_SCHEDULER=1 \
  -v vixdata:/data \
  --name vixvxn \
  vixvxn
# 打开 http://localhost:8801
```

或者用 compose（含持久卷）：

```bash
docker compose up -d --build
```

---

## 方案三：Railway / Fly.io / Heroku

仓库里已放好 **`Procfile`**，这些平台会自动识别：

```
web: gunicorn -c gunicorn.conf.py app:app
```

只需在平台面板加环境变量：

| 变量 | 值 |
|---|---|
| `DATA_DIR` | `/data`（挂了持久卷时） |
| `VIXVXN_SCHEDULER` | `1`（常驻不休眠的实例可以开着） |
| `ADMIN_TOKEN` | 可选，保护手动更新接口 |

---

## 环境变量总表

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `8801` | 监听端口。**PaaS 会自动注入，不要写死** |
| `HOST` | `127.0.0.1` | 容器/云上必须是 `0.0.0.0`，否则外部访问不到 |
| `DATA_DIR` | `./data` | 数据目录。云上指向持久磁盘，如 `/data` |
| `LOG_DIR` | `./logs` | 日志目录 |
| `AUTO_SEED` | `1` | 启动时若数据为空则自动抓取（约 90 秒） |
| `VIXVXN_SCHEDULER` | `1` | 进程内每日定时器。用外部 cron 时设为 `0` |
| `ADMIN_TOKEN` | 空 | 设置后，`POST /api/update` 必须带 `?token=` |
| `DAILY_HOUR` / `DAILY_MINUTE` | `6` / `10` | 进程内定时器的触发时刻（北京时间） |

---

## 数据持久化（重要）

数据是 CSV/JSON 文本文件，**约 2.2 MB**，共 17 个文件。

| 情况 | 结果 |
|---|---|
| **挂了持久磁盘**（`DATA_DIR=/data`） | 数据长期保留，重启不重抓 |
| **没有持久磁盘**（免费套餐常见） | 实例重启后数据目录清空 → `AUTO_SEED` 重新抓，约 90 秒 |

免费套餐多数不给持久磁盘，所以设计成**「随时可从官方接口重建」**：
即使数据全丢，启动时抓一次就恢复，不会永久损坏。

想彻底避免重抓，可以：
1. 用带持久卷的平台（Fly.io 免费额度含 1GB 卷）
2. 或让 `.gitignore` 不排除 `data/`，把数据一起提交，镜像自带数据

---

## 每日更新的三种方式

| 方式 | 适用 | 配置 |
|---|---|---|
| **平台 cron job** | Render / Railway 等有定时任务的平台 | `render.yaml` 已配好 |
| **GitHub Actions** | 所有平台通用，完全免费 | `.github/workflows/daily-update.yml` |
| **进程内定时器** | 实例常驻不休眠（VPS / Docker） | `VIXVXN_SCHEDULER=1` |

三者可共存，有互斥锁保护不会重复抓取。

**GitHub Actions 配置**：仓库 → Settings → Secrets → Actions 添加
- `APP_URL` = 你的应用地址
- `ADMIN_TOKEN` = 与 Web 服务一致的密钥

---

## 手动更新（cron 入口）

`cron_update.py` 是给云上定时任务用的统一入口，三种模式：

```bash
# 1) 容器内直接抓取（写同一个 DATA_DIR）
python cron_update.py

# 2) 远程触发（Web 实例有独立文件系统）
python cron_update.py --url https://your-app.onrender.com --token SECRET

# 3) 先唤醒休眠实例再触发（免费套餐推荐）
python cron_update.py --url https://your-app.onrender.com --token SECRET --wake
```

退出码 `0` 成功、`1` 失败，便于平台告警。

---

## 健康检查

```
GET /healthz
```

```json
{
  "ok": true,
  "data_dir": "/data",
  "has_data": true,
  "last_update": "2026-10-12 14:49:34",
  "updating": false,
  "symbols": 7
}
```

用途：
- 平台据此判断实例是否就绪（`render.yaml` 已设 `healthCheckPath: /healthz`）
- 外部 cron 可先探测再决定是否触发
- 数据为空时也返回 **200**，避免首次抓取的 90 秒被误判为启动失败

---

## 安全建议

站点默认**公开只读**，任何人可看。写入接口 `POST /api/update` 有两层保护：

1. **设置 `ADMIN_TOKEN` 后**，必须带 `?token=xxx` 才能触发，否则返回 `403`
2. **有互斥锁**，重复触发会被拒（`409`），不会打爆上游接口

公网部署建议至少设一个 `ADMIN_TOKEN`，防止陌生人反复触发抓取。

---

## 部署后自检清单

```bash
# 1. 健康检查
curl https://你的地址/healthz

# 2. 首页能打开
curl -I https://你的地址/

# 3. 数据接口正常
curl "https://你的地址/api/data?days=30" | head -c 200

# 4. 手动更新被保护（设了 ADMIN_TOKEN 时应返回 403）
curl -X POST https://你的地址/api/update

# 5. 带 token 能触发
curl -X POST "https://你的地址/api/update?token=你的token"
```

浏览器再确认：四组卡片、走势对比图、货币面板的按钮都能点。

---

## 常见问题

**Q：打开很慢，要等半分钟？**
免费实例休眠了，正在冷启动。想避免就上方案里的保活手段，或用不休眠的平台。

**Q：重启后图表空了？**
没有持久磁盘，且 `AUTO_SEED=0`。设为 `1` 让它自动重抓，或挂持久卷。

**Q：平台上启动失败？**
最常见两个原因：
1. `HOST` 没设成 `0.0.0.0` —— 平台从容器外访问不到
2. 端口写死 —— 必须读 `PORT` 环境变量（本项目已自动读取）

**Q：怎么改绑定端口？**
不用改代码。平台注入 `PORT`，`gunicorn.conf.py` 和 `app.py` 都会读它。

**Q：日志在哪看？**
输出到 stdout/stderr，在平台面板的 Logs 里看；同时写入 `$LOG_DIR/fetch.log`（UTF-8）。

**Q：会不会被抓取失败搞崩？**
不会。抓取有重试 + 东方财富失败自动回退新浪，且**任何源失败都不覆盖已有数据**。
页面显示本地缓存并给出提示。
