# SeedHound

> 继承自 [reseed-puppy](https://github.com/Reseed-Puppy/Reseed-Puppy)，Hound（猎犬）象征更敏捷、更专业的辅种体验。Puppy 是小狗，Hound 是训练有素的猎犬——继承其基因，但嗅觉更敏锐、速度更快、自动化程度更高。

最简高性能 Python 辅种引擎 —— 跨 PT 站点间的高效种子调度工具，猎犬般自动嗅探并匹配可辅种任务。

## 为什么选择 SeedHound

| 对比维度       |                 reseed-puppy                 |                MP青蛙辅种                 |                **SeedHound**                |
| -------------- | :------------------------------------------: | :---------------------------------------: | :-----------------------------------------: |
| **运行模式**   |             WebUI，串行全量执行              |            WebUI，串行全量执行            | 纯 CLI，全链路异步并行，可指定任意站点运行  |
| **日志查看**   | WebUI 内置，**站多种多会卡死且日志查看不便** |                WebUI 内置                 |          控制台 + 文件日志自动轮转          |
| **配置存储**   |                    数据库                    | **数据库,曾多次出现无法保存配置只能重装** |         YAML 纯文本文件，复制即迁移         |
| **配置方式**   |                  WebUI 表单                  |                WebUI 表单                 |       文本编辑器直接编辑，编辑即配置        |
| **批量处理**   |                 逐个站点串行                 |               逐个站点串行                | 多站点异步并行，实测4万种一百站点大约20分钟 |
| **大规模种子** |                WebUI 渲染开销                |              WebUI 渲染开销               |               无 UI 渲染开销                |
| **通知推送**   |                    不支持                    |                   支持                    |              飞书卡片式推送                |

## 项目亮点

### 极简架构

单进程，零外部中间件依赖。要求 **Python 3.11+**（Docker 镜像基于 3.11，CI 覆盖 3.11 / 3.13），
运行时依赖共 7 个包，见下方「依赖」章节与 `requirements.txt`；缓存使用 SQLite（`aiosqlite` 驱动）。

```
本地种子 → 提取 pieces_hash → 并发查询所有站点 API → 匹配 → 添加到下载器
```

全程异步非阻塞，不产生冗余线程或子进程开销。

### 极限性能

| 维度              | 策略                              | 效果                                  |
| ----------------- | --------------------------------- | ------------------------------------- |
| 全链路异步        | `asyncio` + `aiohttp` 异步并发    | 万级种子秒级完成扫描                  |
| 站点并发查询      | 信号量限流 + `asyncio.gather`     | 94 条查询链路并行，耗时约等于最慢站点 |
| 增量缓存          | SQLite 持久化 pieces_hash 索引    | 已扫描种子零重复解析                  |
| 批量 API 调用     | 单次 POST 最多 100 个 pieces_hash | 减少 HTTP 往返，实测提升 3-5x         |
| Bencode 纯 Python | 零依赖解析器，线程池并行          | 解包 37000+ 种子文件 < 20 秒          |
| 分批限流添加      | 20 并发 + 0.3s 间隔               | 避免下载器瞬时阻塞                    |

> 两处 `TCPConnector` 都设置了 `force_close=True`：PT 站点与 Jackett 的长连接容易被中间设备静默断开，
> 每次请求新建连接更稳；因此这里不宣称「长连接复用」，并发度由信号量与 `limit_per_host` 控制。

### 飞书消息卡片

辅种完成后自动推送结构化消息卡片到飞书机器人，基于飞书「消息卡片」JSON 协议，使用 `interactive` 消息类型渲染。卡片包含三个区域：

**① 总览面板（标题 + 8 项核心指标）**

首次推送时附带标题状态图标：全部成功 ✅ / 部分成功 ⚠️ / 无匹配 ❌

```
✅ 总览

🟢 辅种成功　152　　　　🔴 下载失败　3
🎯 全站匹配　428　　　　⏭️ 追踪器跳过 89
📦 扫描种子　38520　　　🔄 去重　　　34182
🆕 新增种子　58　　　　　📌 唯一种子　4338
```

**② 站点详情表（分隔线 + Markdown 表格）**

各站点匹配/成功/失败数量，按**成功率降序**排列（成功率 = 成功数 / (成功数 + 失败数)；
成功率相同时依次按成功数、匹配数降序，最后按站点名升序保证顺序稳定）。每行带四档状态灯：

文本报告、Markdown 报告与飞书卡片共用 `src/report/site_table.py` 的行构造、状态灯与排序规则，
同一轮辅种不会在这三处给出不同结论或不同顺序。

| 图标 | 含义                                   |
| ---- | -------------------------------------- |
| 🟢   | 全部下载成功                           |
| 🟡   | 部分成功（有失败）                     |
| 🔵   | 有匹配但未下载（dry-run / 种子已存在） |
| ⚪   | 无成功且无匹配（仅有失败记录）         |

**③ 页脚摘要（分隔线 + note 标签）**

- 第一行：⏱ 耗时 · 📡 站点数 · 🎯 命中数 · ❌ 无匹配数 · 💀 死种数
- 第二行（note）：最多列出 10 个无匹配站点名，超出部分以「等 N 个」收束

**配置方式**：在 `config.yaml` 中填入飞书机器人 Webhook 地址和签名密钥即可：

```yaml
notify:
  feishu:
    webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
    secret: "" # 签名密钥（可选）
```

使用 `--no-feishu` 参数可临时跳过推送。

### 全程可观测

- 四级日志：控制台 + 文件双输出，自动轮转
- SQLite 缓存表记录完整辅种历史
- 支持 `--dry-run` 演练模式：只查询不添加
- 缓存自动增量更新，无需手动干预

### 配置以 YAML 为主

业务参数（站点、下载器、超时、并发、通知）统一收敛在 `config.yaml` / `sites.yaml`。
所有可缺省字段的默认值集中定义在 `src/storage/config.py` 的 `DEFAULTS`，模板 `config.example.yaml`
与之保持一致；配置里出现未识别字段时启动会打印告警，避免拼错后静默回退到默认值。

CLI 与环境变量只负责**运行模式与临时覆盖**，不写回配置文件：
`--dry-run` / `--no-feishu` / `--site` / `--mode` / `--config` / `--sites-path`、
`reseed` / `schedule` / `sync-cookies` 三个子命令，以及容器场景的 `SEEDHOUND_MODE`。
公共开关写在子命令前后都生效（例如 `python run.py --dry-run reseed`）。

### 安全设计

- `config.yaml` / `sites.yaml` 不入库，使用 `.example` 文件作为模板
- 代码中不保留任何凭据默认值，CookieCloud 的 uuid / 密码只从本地 `config.yaml` 读取
- 日志写入前统一脱敏 `passkey`（`src/utils/logger.py` 的日志过滤器，覆盖文件与控制台两个 handler）
- 飞书 webhook 仅在配置文件中声明

## 目录结构

```
seedhound/
├── run.py                        # 唯一入口（CLI 解析 + 运行模式分派）
├── SeedHound.bat                 # Windows 一键启动（切目录 / 查 Python / 透传参数 / 错误码 / pause）
├── README.md / CHANGELOG.md / AGENTS.md
├── config.example.yaml           # 主配置模板
├── sites.example.yaml            # 站点配置模板
├── requirements.txt              # 运行时依赖
├── requirements-dev.txt          # 测试与 lint 依赖
├── pytest.ini / .flake8 / .dockerignore / Dockerfile
├── .github/workflows/
│   ├── docker.yml                # 打 v* tag / Release 时构建并推送 GHCR
│   └── ci.yml                    # push / PR 上跑 flake8 + pytest（Python 3.11 / 3.13）
├── src/
│   ├── runner.py                 # 单轮辅种唯一实现（reseed 与 schedule 首轮共用）
│   ├── scheduler.py              # 原生定时调度器（循环运行 + 优雅退出）
│   ├── cookie_sync.py            # CookieCloud 同步并写回 sites.yaml
│   ├── engine/
│   │   ├── seeder.py             # 辅种引擎主控（三阶段流水线）
│   │   ├── parser.py             # 种子解析器（纯 Python Bencode + 线程池）
│   │   ├── downloader.py         # 下载器抽象层（qBittorrent / Transmission）
│   │   └── jackett.py            # Jackett Torznab 搜索与下载缓存
│   ├── network/
│   │   ├── client.py             # 异步 HTTP 客户端（限流 + 重试 + 死种识别）
│   │   └── cookiecloud.py        # CookieCloud 解密与域名匹配
│   ├── notify/
│   │   └── feishu.py             # 飞书机器人推送（消息卡片 + 签名）
│   ├── report/
│   │   ├── generator.py          # 报告生成器（文本 / Markdown）
│   │   └── site_table.py         # 站点表统一口径（行 / 状态灯 / 排序 / 截断）
│   ├── storage/
│   │   ├── config.py             # 配置加载、DEFAULTS 单一来源与字段校验
│   │   └── cache.py              # SQLite 缓存（pieces_hash 索引 + 辅种记录 + 死种）
│   └── utils/
│       ├── _bencode.py           # Bencode 编解码（纯 Python，零依赖）
│       ├── hash.py               # 哈希工具（SHA1 / Pieces SHA1）
│       └── logger.py             # 日志初始化（passkey 脱敏 + UTF-8 输出 + RUN_MARKER）
├── scripts/
│   └── weekly_report.py          # 日志周报 + 远期日志清理（默认 dry-run；不在 Docker 镜像内）
└── tests/
    ├── conftest.py / helpers.py  # tmp_path 配置 fixture 与外部 golden 值
    ├── test_cli.py / test_config.py / test_cookie.py / test_core.py
    ├── test_downloader.py / test_engine.py / test_integration.py
    └── test_network.py / test_report.py / test_scheduler.py
```

## 快速开始

### 安装

```bash
pip install -r requirements.txt
```

### Docker 部署

```bash
# 方式一：从 GHCR 拉取镜像（推荐，无需本地构建）
docker pull ghcr.io/schalkiii/reseedhound:latest
# 方式二：本地构建
docker build -t seedhound .
```

> 镜像由 GitHub Actions 在打 `v*` 标签 / 发布 Release 时自动构建并推送到 **GHCR**（见仓库 `.github/workflows/docker.yml`）；也支持手动 `workflow_dispatch`，但手动构建只产出分支名标签、不会覆盖 `latest`。镜像地址为 `ghcr.io/<owner>/<repo>`，本项目默认 `ghcr.io/schalkiii/reseedhound`。
>
> 镜像内只包含 `src/`、`run.py` 与 `requirements.txt`（见 `Dockerfile`）：**不含 `scripts/weekly_report.py`**，需要生成日志周报请在宿主机运行，并通过 `-v` 挂载 `logs/` 后读取。容器内 `localhost` 指向容器自身，访问宿主机的下载器 / Jackett 时须填写宿主机可达地址。

# 运行容器（GHCR 镜像用 ghcr.io/schalkiii/reseedhound，本地构建用 seedhound）
docker run -d \
  --name seedhound \
  --restart unless-stopped \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -v $(pwd)/sites.yaml:/app/sites.yaml \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -v /path/to/BT_backup:/app/torrents \
  ghcr.io/schalkiii/reseedhound

| 挂载项        | 说明                                                        |
| ------------- | ----------------------------------------------------------- |
| `config.yaml` | 主配置文件，必须挂载                                        |
| `sites.yaml`  | 站点配置文件，API 模式必须挂载                              |
| `data/`       | SQLite 缓存 + Jackett 下载缓存，持久化                      |
| `logs/`       | 运行日志，方便排查问题                                      |
| `torrents/`   | 种子文件目录（对应 `downloader.torrent_dir`），只读挂载即可 |

**运行模式（环境变量 `SEEDHOUND_MODE`）**：`run.py` 在容器启动时会直接读取该变量选择运行模式（无子命令时）；也可在 `docker run` 后追加子命令覆盖。

| 值            | 行为                                                         |
| ------------- | ------------------------------------------------------------ |
| `reseed`      | 一次性运行辅种引擎后退出（默认，向后兼容）                   |
| `schedule`    | 按 `config.yaml` 中 `scheduler` 配置**循环定时运行**（常驻） |
| `sync-cookies`| 同步 Cookie Cloud 到 `sites.yaml` 后退出                     |

```bash
# 常驻定时运行（推荐）：每 interval_minutes 自动辅种一轮
docker run -d \
  --name seedhound \
  --restart unless-stopped \
  -e SEEDHOUND_MODE=schedule \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -v $(pwd)/sites.yaml:/app/sites.yaml \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -v /path/to/BT_backup:/app/torrents \
  ghcr.io/schalkiii/reseedhound
```

> **提示**：若 `config.yaml` 中 `scheduler.enabled=true`，即便使用默认 `reseed` 模式，容器也会自动切换为定时运行，无需额外设置环境变量。

### 从 GHCR 拉取镜像（推荐）

GitHub 原生容器仓库为 **GHCR（`ghcr.io`）**。SeedHound 的镜像发布在 `ghcr.io/<owner>/<repo>`（本项目默认 `ghcr.io/schalkiii/reseedhound`），可直接拉取，无需本地 `docker build`。

**可用标签**（由 `docker/metadata-action` 生成）：

| 标签         | 生成规则                                  | 说明                                        |
| ------------ | ----------------------------------------- | ------------------------------------------- |
| `latest`     | `flavor: latest=auto`                     | **仅**在推送 semver `v*` 标签 / Release 时生成 |
| `1.2.3`      | `type=semver,pattern={{version}}`         | 精确版本（去掉前导 `v`）                    |
| `1.2`        | `type=semver,pattern={{major}}.{{minor}}` | 主.次 版本通道                              |
| `<分支名>`   | `type=ref,event=branch`                   | 手动 `workflow_dispatch` / 分支构建，**不覆盖 `latest`** |

> 工作流不再无条件写 `latest`，也不再用 `type=ref,event=tag` 与 `type=semver` 重复产出
> `v1.2.3` / `1.2.3` 两个标签。`IMAGE_NAME` 保留 `${{ github.repository }}`，
> `docker/metadata-action` 会自动把仓库名小写化为 `reseedhound`。

**拉取与运行（docker / wslc 通用）**：

```bash
# docker
docker pull ghcr.io/schalkiii/reseedhound:latest
docker run -d --name seedhound -e SEEDHOUND_MODE=schedule \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -v $(pwd)/sites.yaml:/app/sites.yaml \
  -v $(pwd)/data:/app/data -v $(pwd)/logs:/app/logs \
  -v /path/to/BT_backup:/app/torrents \
  ghcr.io/schalkiii/reseedhound

# wslc（语法一致）
wslc pull ghcr.io/schalkiii/reseedhound:latest
wslc run -d --name seedhound -e SEEDHOUND_MODE=schedule \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -v $(pwd)/sites.yaml:/app/sites.yaml \
  -v $(pwd)/data:/app/data -v $(pwd)/logs:/app/logs \
  -v /path/to/BT_backup:/app/torrents \
  ghcr.io/schalkiii/reseedhound
```

> **发布方式**：镜像通过仓库内的 GitHub Actions 工作流（`.github/workflows/docker.yml`）自动发布——在 GitHub 上打 `v*` 标签或发布 Release 即触发构建并推送到 GHCR，使用仓库自带的 `GITHUB_TOKEN` 鉴权，无需手动 `docker login`。首次使用前需在仓库 **Settings → Packages** 中确认已开启容器包的写入权限。

**注意**：容器有独立的网络命名空间，`127.0.0.1` 指向容器自身。访问宿主机上的下载器与 Jackett 时，请在 `config.yaml` 中填写宿主机可达地址，且 `jackett.url` 同样要改：

| 运行环境 | `downloader.host` 建议值 |
| --- | --- |
| Docker Desktop | `host.docker.internal` |
| WSL / `wslc` | Windows 主机在 `vEthernet (WSL)` 网卡上的 IPv4（容器内查 `/etc/resolv.conf` 的 `nameserver`，或 Windows 上 `ipconfig` 查看） |
| 局域网可达 | 宿主机 LAN IP（需确认容器网段可路由） |

> WSL 虚拟网卡地址会在 `wsl --shutdown` / 主机重启后变化，建议在启动脚本中动态探测并注入配置。
> 已实测：`wslc` 容器内填写 `192.168.10.1` 无效（那是路由器网关），必须填 Windows 主机侧地址。
> `downloader.torrent_dir` 要写**容器内挂载后的路径**（如 `/app/torrents`）。

### 使用 wslc 部署（微软原生 WSL 容器）

`wslc`（WSL Container CLI，`wslc.exe`）是微软在 Build 2026 随 WSL 预览版**内置**的 Linux 容器命令行，语法与 docker 高度一致，**无需安装 Docker Desktop**。SeedHound 的镜像在 wslc 下同样适用（微软同时提供 `container.exe` 别名，二者等价）；镜像同样来自 GHCR（见上方"从 GHCR 拉取镜像"）。

**① 安装 / 升级 WSL 预览版**（wslc 随其一同发布）

```bash
wsl --update --pre-release
```

**② 构建镜像**（命令与 docker 一致）

```bash
wslc build -t seedhound .
```

**③ 运行容器（定时常驻）**

```bash
wslc run -d \
  --name seedhound \
  --restart unless-stopped \
  -e SEEDHOUND_MODE=schedule \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -v $(pwd)/sites.yaml:/app/sites.yaml \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -v /path/to/BT_backup:/app/torrents \
  seedhound
```

> 挂卷（`-v`）、环境变量（`-e`）、后台运行（`-d`）、容器命名（`--name`）等参数写法与 docker 完全相同；个别参数若 wslc 暂未支持，按提示移除即可。

**④ 常用运维命令（对照 docker）**

| 操作 | wslc 命令 |
| --- | --- |
| 查看运行容器 | `wslc container ps` |
| 查看日志 | `wslc logs -f seedhound` |
| 停止 / 删除 | `wslc container stop seedhound` / `wslc container rm seedhound` |
| 列出镜像 | `wslc image ls` |
| 进入容器 | `wslc exec -it seedhound sh` |

**⑤ 访问宿主机下载器（关键）**

wslc 容器本质是一个 WSL 环境，容器内 `127.0.0.1` 指向容器自身。若下载器（qBittorrent/Transmission）或 Jackett 装在 Windows 宿主机，请在 `config.yaml` 中填写 **Windows 主机在 `vEthernet (WSL)` 网卡上的 IPv4**（容器内查 `/etc/resolv.conf` 的 `nameserver`，或 Windows 上 `ipconfig` 查看），**不要填 `127.0.0.1` 或路由器网关 `192.168.x.1`**。该地址在 WSL 重启后会变化；`wslc` 不支持 `--network host` / `--add-host`。

```yaml
downloader:
  type: qbittorrent
  host: 172.23.240.1          # 示例值：Windows 主机 vEthernet (WSL) 地址，按实际网络拓扑调整
  port: 9091
  username: admin
  password: your_password_here
  torrent_dir: /app/torrents  # 对应挂载进容器的种子目录

jackett:
  url: "http://172.23.240.1:9117"
```

种子目录挂载同理，Windows 侧真实路径或 WSL 内路径均可；9p 挂载**不支持 Windows 目录联接（Junction）**，指向 `BT_backup` 这类联接目录会失败，应挂载真实目录或改用 qB API 导出种子。

### 配置

复制示例文件为实际配置文件：

```bash
cp config.example.yaml config.yaml
cp sites.example.yaml sites.yaml
```

编辑 `config.yaml`（下面与 `config.example.yaml` 字段、取值完全一致，完整注释版见模板文件）：

```yaml
global:
  concurrency: 20
  batch_size: 100
  api_timeout: 60
  download_timeout: 15
  download_interval: 5.0
  retry_count: 2
  retry_delay: 2.0
  mode: pieces_hash

db:
  path: data/cache.db

log:
  level: INFO
  file: logs/reseed.log

scheduler:
  enabled: false
  interval_minutes: 360
  startup_delay: 0
  run_on_start: true

downloader:
  type: qbittorrent
  host: 127.0.0.1
  port: 8080
  username: admin
  password: your_password_here
  torrent_dir: /path/to/BT_backup
  skip_hash_check: true
  auto_start: true
  tag: SeedHound

notify:
  feishu:
    webhook_url: ""
    secret: ""

jackett:
  enabled: false
  url: "http://localhost:9117"
  api_key: ""
  concurrency: 5
  search_interval: 1.0
  timeout: 30

cookiecloud:
  enabled: false
  url: "http://127.0.0.1:8082/cookie"
  uuid: "your-uuid"
  password: "your-password"
```

**默认值单一来源**：上表中所有可缺省字段（含 `api_timeout=60`、`download_timeout=15`、
`retry_count=2`、`retry_delay=2.0`、`download_interval=5.0`、`log.file=logs/reseed.log`、
`downloader.skip_hash_check/auto_start/tag`）都定义在 `src/storage/config.py` 的 `DEFAULTS`；
业务模块不再各自硬编码数值。省略某字段即取该默认值，写进配置则覆盖。
`jackett.retry_count` / `retry_delay` 可省略，省略时继承 `global` 的同名值。

**双下载器模式**：把 `downloader` 换成 `source` / `destination` 两个子块（写法见 `config.example.yaml`）。
运行时**主下载器取 `destination`**（实际添加种子的目标），`source` 只提供本地种子库；
`torrent_dir` 读 `source`，`skip_hash_check` / `auto_start` / `tag` 读 `destination`。
`create_downloader` 缺少 `host` 时会给出可读报错，不会抛 `KeyError`。

编辑 `sites.yaml` 填入各 PT 站点的 passkey：

```yaml
sites:
  - name: 站点名
    url: https://example.com
    api_url: https://example.com/api/pieces-hash
    passkey: your_passkey_here
    enabled: true
```

| 字段 | 说明 |
| --- | --- |
| `name` | 站点名，用于 `--site` 指定与报告展示 |
| `url` | 站点主页，按 tracker 域名做同站预过滤 |
| `api_url` | pieces_hash 查询接口，POST `{"passkey": ..., "pieces_hash": [...]}` |
| `passkey` | 必填；为空的站点会被忽略 |
| `enabled` | 是否启用，缺省 `true`；兼容历史写法 `enable`，两者同时出现时以 `enable` 为准 |
| `cookie` / `cookies` | 可选站点 Cookie，同时存在时优先 `cookie` |
| `download_interval` | 可选，覆盖 `global.download_interval` 的每域名下载间隔（秒） |

### 运行

```bash
# 正常运行（全站点辅种）
python run.py

# 指定辅种模式
python run.py --mode both     # pieces_hash + Jackett 双模式
python run.py --mode jackett  # 仅 Jackett 模式
python run.py --mode pieces_hash  # 仅 pieces_hash 模式（默认）

# 演练模式（只查询不添加）
python run.py --dry-run

# 指定站点辅种（逗号分隔多个）
python run.py --site 站点A,站点B,站点C

# 跳过飞书通知
python run.py --no-feishu

# 定时运行（原生调度器，按 config 中 scheduler 配置循环执行）
python run.py schedule
```

| 参数 / 变量        | 说明                                                    |
| ------------------ | ------------------------------------------------------- |
| `--mode MODE`      | 辅种模式：`pieces_hash`(默认) / `jackett` / `both`      |
| `--dry-run`        | 演练模式，只查询匹配不添加种子                          |
| `--site S1,S2`     | 仅对指定站点辅种（逗号分隔）                            |
| `--no-feishu`      | 跳过飞书通知推送                                        |
| `--config PATH`    | 指定主配置文件（默认 `config.yaml`）                    |
| `--sites-path PATH`| 指定站点配置文件（默认 `sites.yaml`）                   |
| `reseed`           | 子命令：一次性辅种（默认）                              |
| `schedule`         | 子命令：按 `scheduler` 配置循环定时运行辅种引擎         |
| `sync-cookies`     | 子命令：从 CookieCloud 同步 Cookie 到 `sites.yaml`      |
| `SEEDHOUND_MODE`   | 容器无子命令时选择运行模式，值同上三个子命令            |

公共开关在子命令前后都生效，例如 `python run.py --dry-run reseed` 与 `python run.py reseed --dry-run` 等价。

**Windows 一键启动**：双击 `SeedHound.bat` 会先切到脚本所在目录、检查 `python` 与 `config.yaml`，
再把全部参数透传给 `python run.py`，最后显示退出码并 `pause`（窗口不会一闪而过）。
它**继承 `config.yaml` 的 `scheduler.enabled`**：设为 `true` 时启动的是常驻定时模式（按 `Ctrl+C` 退出），
需要一次性辅种请把该值改为 `false`。也可直接传参，例如 `SeedHound.bat --dry-run reseed`。

### 定时运行（原生调度器）

SeedHound 内置原生定时运行能力，**无需依赖外部 cron 或编排器**。调度器在进程内以异步循环方式运行辅种引擎，每轮结束后按配置间隔休眠，并支持 `SIGINT`/`SIGTERM` 优雅退出（收到信号后完成当前轮再停止）。

配置（在 `config.yaml` 中）：

```yaml
scheduler:
  enabled: false          # true 时，默认 reseed 会自动切换为定时运行
  interval_minutes: 360   # 每轮间隔（分钟）
  startup_delay: 0        # 启动后延迟多少秒再首轮（错峰用）
  run_on_start: true      # 启动后是否立即执行首轮（false 则先等一个完整间隔）
```

三种触发方式：

```bash
# 方式一：显式子命令（最明确）
python run.py schedule

# 方式二：config 中 scheduler.enabled=true 后，默认运行即自动定时
python run.py

# 方式三：Docker 中通过环境变量（见下方部署章节）
docker run -e SEEDHOUND_MODE=schedule ... seedhound
```

### Cookie 同步

SeedHound 支持从 [Cookie Cloud](https://github.com/easychen/CookieCloud) 自动同步各站点的 Cookie 信息，无需手动从浏览器导出。

Cookie Cloud 是一个浏览器插件，可将浏览器中的 Cookie 加密同步至自托管服务端。SeedHound 通过读取服务端数据，自动匹配 `sites.yaml` 中各站点的域名，将对应 Cookie 写入配置。

**前置条件**：

1. 在浏览器中安装 Cookie Cloud 插件，连接至你的 Cookie Cloud 服务端
2. 确保 Cookie Cloud 服务端可访问，且 `config.yaml` 中已配置连接参数

**配置**（在 `config.yaml` 中添加）：

```yaml
cookiecloud:
  enabled: true
  url: "http://127.0.0.1:8082/cookie" # CookieCloud 服务端地址
  uuid: "your-uuid" # 用户 UUID
  password: "your-password" # 解密密码
```

> 字段名统一为 `enabled`（与 `scheduler` / `jackett` / `sites[].enabled` 一致）；
> 历史写法 `enable` 仍可读取，且两者同时出现时以 `enable` 为准。

**定时自动同步**：当 `cookiecloud.enabled: true` 时，原生调度器（`schedule` 子命令，或 `scheduler.enabled=true` 下的 `reseed`）会在**每轮辅种引擎启动前**自动执行一次 CookieCloud 同步。同步失败时仅记录告警并继续本轮（沿用已有 Cookie），不会中断辅种；需确保 CookieCloud 服务端（如 Docker 容器）处于运行状态。

**使用**：

```bash
# 同步 Cookie 到 sites.yaml
python run.py sync-cookies
```

执行后 SeedHound 会：

1. 从 Cookie Cloud 服务端拉取加密的 Cookie 数据并解密
2. 提取各域名下的 Cookie，与 `sites.yaml` 中的站点 URL 域名进行匹配
3. 将匹配的 Cookie 以双引号包裹的单行格式写入 `sites.yaml`
4. 输出匹配结果统计（成功匹配数 / 总站点数）

**Cookie 日志解读**：

```
[OK] 站点名 <- 匹配域名 (N 条 Cookie)    ← 匹配成功
[--] 站点名 未匹配到 Cookie               ← 该站点在 Cookie Cloud 中无数据
```

> **注意**：`sites.yaml` 中的 cookie 值以双引号包裹且位于单行，避免 YAML 解析歧义。
> 该文件含站点 Cookie / passkey，已在 `.gitignore` 中排除，**不要提交入库**；需要备份请自行复制本地文件。

### 日志周报与日志清理

`scripts/weekly_report.py` 解析 `logs/reseed.log`，按站点聚合近一段时间的辅种指标：

```bash
python scripts/weekly_report.py                        # 近 7 天周报，输出到控制台
python scripts/weekly_report.py --days 30              # 自定义时间窗
python scripts/weekly_report.py --markdown out.md      # 同时写出 Markdown
python scripts/weekly_report.py --clean --keep-days 30           # 预览将清理的远期日志
python scripts/weekly_report.py --clean --keep-days 30 --apply   # 真正截断
```

清理默认 dry-run，只有加 `--apply` 才会改写日志文件。

## 辅种方法学

SeedHound 的设计核心是**用最小站点压力换取最大辅种收益**。以下是其关键优化策略：

### 1. 按内容去重而非按种子去重

不同站点发布的同一资源，尽管 `.torrent` 文件的 `info_hash` 不同，但文件内容的 `pieces_hash` 相同。SeedHound 在扫描阶段以 `pieces_hash` 为键对所有本地种子去重：

```
38,000 个 .torrent 文件 → 按 pieces_hash 去重 → ~4,000 个唯一种子
```

这意味着向站点 API 发送的查询量减少约 **89%**，大幅降低站点压力。

### 2. 三层过滤机制

SeedHound 在辅种流水线的不同阶段部署了三层过滤，逐层拦截已知冗余，最小化站点 API 压力和网络下载开销：

```
┌─ 阶段2: 查询前 ───────────────────────────────────────────────┐
│                                                               │
│  第一层 · tracker 预过滤                                       │
│  pieces_hash 在 qB 中且 tracker 域名匹配同站 → 不发送 API 查询 │
│                                                               │
│  第二层 · reseed_history 预过滤                                │
│  (pieces_hash, 站点) 已成功辅种且种子仍在 qB → 不发送 API 查询 │
│                                                               │
│  效果: 从源头减少 API 请求量，避免无谓的匹配结果                │
└───────────────────────────────────────────────────────────────┘
                              ↓ 未命中
┌─ 阶段3: 下载后 ───────────────────────────────────────────────┐
│                                                               │
│  第三层 · info_hash 去重                                       │
│  下载 → 解析 → info_hash 已在 qB → 写入缓存 + 跳过             │
│  （仅首次遇到时触发，后续被第二层拦截）                          │
│                                                               │
│  效果: 兜底保障，首次遇到后永久缓存                             │
└───────────────────────────────────────────────────────────────┘
```

> **关键设计**：两层在下载前完成（查询阶段），零下载带宽消耗。第二层在查询前校验 reseed_history 的 qB 存在性，避免种子已从 qB 删除后被错误跳过。第三层为兜底，确保任何漏网之鱼只被下载一次。

### 3. 增量缓存

已解析的 `.torrent` 文件的 `pieces_hash` 持久化到 SQLite。后续运行时仅需增量扫描新增文件，避免每次全量解析。

### 4. 智能站点跳过（严重错误累计）

下载种子时发生的错误被分为两类：

| 类型 | 错误码 | 处理方式 |
| --- | --- | --- |
| **临时错误** | `timeout`、`connection_error`、`rate_limit`、`http_5xx`、`too_short` | `try_download` 按 `retry_count` / `retry_delay` 重试；不计入站点跳过 |
| **严重错误** | `http_401`、`http_403`、`http_404`、`http_410`、`html_permission_denied`、`html_torrent_deleted`、`html_not_found`、`html_forbidden`、`html_auth_required`、`auth_redirect` | 累计 3 次后跳过该站点后续下载 |
| **超时累计** | `timeout` | 累计 30 次后跳过该站点后续下载（独立阈值） |
| **已知死种** | `dead_cached` | 直接跳过的同时**不计入失败** |

错误码在 `src/network/client.py` 产生：HTTP 状态码映射为 `http_<status>`，HTML 响应由
`DEAD_TORRENT_PATTERNS` 识别为 `html_<reason>`，限流文案由 `RATE_LIMIT_KEYWORDS` /
`RATE_LIMIT_HINT_PATTERNS` 识别为 `rate_limit`；累计阈值常量
`SERIOUS_ERRORS` / `MAX_SERIOUS_ERRORS=3` / `MAX_TIMEOUT_ERRORS=30` 定义在
`src/engine/seeder.py` 的 `_phase_add_torrents`。持续 15 天的死种会写入 SQLite 并在启动时硬跳过。

这避免了网络波动或服务器临时繁忙导致的误判——之前能正常辅种的站点不会因为网络抖动而被全部跳过。只有真正无法访问（认证过期、权限不足、种子已删除）的站点才会被自动跳过。

### 5. 自动开始已完成的种子

添加种子成功后，SeedHound 自动检测该种子是否已完成校验（数据已在本地磁盘）：

- 已完成 → 立即自动恢复，开始做种
- 未完成 → 保持暂停，等待用户手动处理

这确保辅种操作「即加即生效」，无需用户手动启动。

判定所用的状态名同时覆盖新旧 qBittorrent：`pausedUP`（≤ 4.6）与 `stoppedUP`（5.x 改名）、
以及 Transmission 侧的 `completed`。只判旧名会让 5.3.0 上的「自动开始」永不触发——种子加进去了却
一直停在暂停态。相关实现在 `src/engine/seeder.py` 的 `STOPPED_COMPLETE_STATES`。

`torrents_add` 的成功判定同样分版本：WebAPI v2.14.0（qB 5.2）起返回 JSON 计数
（`success_count` / `failure_count` / `pending_count`），更早才返回纯文本 `Ok.`；只判 `== "Ok."`
会把 5.2/5.3 上的每次成功添加都记成失败；更关键的是「自动开始」只在添加成功分支里执行，这批种子会长期
停在暂停态不做种。见 `QbittorrentDownloader._is_add_success`。

此外 `is_paused=False` 会被底层库折叠成「不发送该参数」，qB 便按全局 `add_stopped_enabled` 把种子加成
`stoppedUP`。因此添加后还有 `_ensure_started` 兜底：轮询确认状态，仍停在停止态就补一次 `resume`。
`auto_start=false` 时不执行该补偿，种子保持暂停。

```
┌────────────┐    ┌─────────────┐    ┌──────────────┐
│  Parser    │    │  Seeder     │    │  Downloader  │
│  .torrent  │───>│  引擎调度    │───>│  qB/TR API   │
│  → pieces  │    │  三阶段流水线 │    │  添加种子    │
└────────────┘    └──────┬──────┘    └──────────────┘
                          │
                 ┌────────┴────────┐
                 │  HTTP Client    │
                 │  aiohttp 客户端  │
                 │  并发查询各站点   │
                 └───┬─────────┬───┘
                     │         │
         ┌───────────┘         └──────────────┐
         │  pieces_hash 模式                  │  Jackett 模式
         │                                    │
    ┌────┴────┐    ┌────┴────┐          ┌────┴────────────┐
    │ Site A  │    │ Site B  │          │   Jackett        │
    │ /api/   │    │ /api/   │          │   跨站搜索        │
    │ pieces- │    │ pieces- │          │   ├── title+year  │
    │ hash    │    │ hash    │          │   ├── size ±0.01G │
    └─────────┘    └─────────┘          │   └── pieces_hash │
                                         └─────────────────┘
```

三阶段流水线：

1. **扫描阶段**：扫描 `torrent_dir` **当前层**的 `*.torrent`（`Path.glob`，不递归子目录），解析提取 `pieces_hash`，增量更新缓存；种子按子目录组织时需自行展平或调整挂载路径
2. **查询阶段**：Pieces-hash 模式批量 POST 到各站点 `/api/pieces-hash`；Jackett 模式通过 Torznab API 跨站搜索
3. **添加阶段**：匹配到的种子依次执行 ① 从站点下载 `.torrent` → ② pieces_hash 校验 → ③ 添加到下载器。pieces_hash 模式按 `config.yaml` 的 `downloader.skip_hash_check`（默认 `true`，即直接认定完成）与 `downloader.auto_start`（默认 `true`，即不加暂停）下发；Jackett 模式则固定 `skip_hash_check=false, paused=true`，由 qBittorrent 自行 recheck 确认文件匹配后做种

### 双模式辅种

| 模式            | 原理                                                 | 适用场景            |
| --------------- | ---------------------------------------------------- | ------------------- |
| **pieces_hash** | 直接 POST pieces_hash 到站点 `/api/pieces-hash`      | 支持该 API 的 PT 站 |
| **Jackett**     | 通过 Jackett Torznab API 搜索 title+year，按尺寸过滤 | 任意 Jackett 索引器 |

Jackett 模式流程：

```
本地种子 → 提取 title+year 搜索词 → Jackett Torznab 搜索
→ 尺寸过滤（±0.01GB for ≥1GB / ±0.01MB for ≥1MB）
→ 下载候选种子 → pieces_hash 校验
→ 不匹配的 URL 写入缓存（data/jackett_dl_cache.json），后续跳过
→ 匹配则添加至 qBittorrent（paused，由 qB 自行 recheck）
```

### 站点 pieces-hash API 检测

站点 API 端点是否支持 pieces_hash 可通过 HTTP 请求快速判断：

```
POST https://example.com/api/pieces-hash

→ 404 Not Found      站点不支持 pieces_hash API，可尝试 Jackett 模式
→ 405 Method Not Allowed  站点支持该 API，但需检查 passkey 或请求格式
→ 200 OK                  API 正常，返回匹配的 torrent_id 列表
```

配置站点时若不确定 API 是否可用，先用 `curl -X POST` 试一下即可确认；不支持该 API 的站点可改用上方「双模式辅种」中的 Jackett 路径。

## 依赖

与 `requirements.txt` 保持一致：

```
aiohttp>=3.9.0
aiosqlite>=0.20.0
pycryptodome>=3.20.0
pyyaml>=6.0
qbittorrent-api>=2026.8.1
transmission-rpc>=7.0.0
yarl>=1.9.0
```

> **`qbittorrent-api` 的版本下限是有意的**：qBittorrent 5.3.0（WebAPI v2.16）把 `torrents/add` 的
> `skip_checking` 改名为 `seedMode`，且服务端对不认识的参数**静默忽略**。2026.8.1 起该库同时发送新旧
> 两个名字；用更早的版本，`skip_hash_check` 会在 5.3.0 上悄悄失效（种子照加，但会老老实实重头校验）。

`transmission-rpc` 已包含在 `requirements.txt` 中，使用 Transmission 作为下载器时无需额外安装。

## 测试与开发

测试与 lint 依赖单独声明在 `requirements-dev.txt`（`pytest` / `pytest-asyncio` / `flake8`），
`pytest.ini` 固定 `asyncio_mode = auto` 与 `testpaths = tests`：

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m flake8 src tests run.py       # 0 错误
python -m pytest tests/ -q              # 当前 135 项用例全绿
```

`.github/workflows/ci.yml` 在 push / PR 上以 Python 3.11 / 3.13 矩阵自动执行这两条门禁。
测试全部用 `tmp_path` 现场生成 `config.yaml` / `sites.yaml` 与临时数据库，
**不读取本地含真实凭据的配置、不在 `data/` 留下测试文件**；因此干净克隆、CI，
乃至把本地配置临时改名后，套件都能全绿。哈希正确性用外部 golden 值校验，不用自身实现自证。

## License

MIT
