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
| **通知推送**   |                    不支持                    |                   支持                    |              飞书仪卡片式推送               |

## 项目亮点

### 极简架构

单进程，零外部中间件依赖。仅需 Python 3.9+、aiohttp、SQLite 即可运行。

```
本地种子 → 提取 pieces_hash → 并发查询所有站点 API → 匹配 → 添加到下载器
```

全程异步非阻塞，不产生冗余线程或子进程开销。

### 极限性能

| 维度              | 策略                              | 效果                                  |
| ----------------- | --------------------------------- | ------------------------------------- |
| 全链路异步        | `asyncio` + `aiohttp` 连接池复用  | 万级种子秒级完成扫描                  |
| 站点并发查询      | 信号量限流 + `asyncio.gather`     | 94 条查询链路并行，耗时约等于最慢站点 |
| 增量缓存          | SQLite 持久化 pieces_hash 索引    | 已扫描种子零重复解析                  |
| 批量 API 调用     | 单次 POST 最多 100 个 pieces_hash | 减少 HTTP 往返，实测提升 3-5x         |
| Bencode 纯 Python | 零依赖解析器，线程池并行          | 解包 37000+ 种子文件 < 20 秒          |
| 分批限流添加      | 30 个/批 + 0.3s 间隔              | 避免下载器瞬时阻塞                    |

### 飞书仪表盘卡片

辅种完成后自动推送结构化仪表盘卡片到飞书机器人，基于飞书「消息卡片」JSON 协议，使用 `interactive` 消息类型渲染。卡片包含三个区域：

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

各站点匹配/成功/失败数量，按成功率排序。每行带状态灯：

| 图标 | 含义                                   |
| ---- | -------------------------------------- |
| 🟢   | 全部下载成功                           |
| 🟡   | 部分成功（有失败）                     |
| 🔵   | 有匹配但未下载（dry-run / 种子已存在） |
| ⚪   | 无任何匹配                             |

**③ 页脚摘要（分隔线 + note 标签）**

- 第一行：⏱ 耗时 · 📡 站点数 · 🎯 命中数 · ❌ 无匹配数 · 💀 死种数
- 第二行（自动折叠）：列出所有无匹配站点名

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

### 硬编码零配置原则

不接受命令行临时参数注入。所有配置收敛于 YAML 文件，避免运行时参数散落导致的「配置漂移」问题。

### 安全设计

- `config.yaml` / `sites.yaml` 不入库，使用 `.example` 文件作为模板
- passkey 不出现在日志中
- 飞书 webhook 仅在配置文件中声明

## 目录结构

```
seedhound/
├── run.py                    # 入口
├── config.example.yaml       # 主配置模板
├── sites.example.yaml        # 站点配置模板
├── requirements.txt          # 依赖
├── src/
│   ├── engine/
│   │   ├── seeder.py         # 辅种引擎主控（三阶段流水线）
│   │   ├── parser.py         # 种子解析器（纯 Python Bencode + 线程池）
│   │   └── downloader.py     # 下载器接口（qBittorrent / Transmission）
│   ├── network/
│   │   └── client.py         # 异步 HTTP 客户端（连接池 + DNS 缓存）
│   ├── notify/
│   │   └── feishu.py         # 飞书机器人推送（消息卡片）
│   ├── report/
│   │   └── generator.py      # 报告生成器（文本 / Markdown）
│   ├── storage/
│   │   ├── config.py         # 配置加载与校验
│   │   └── cache.py          # SQLite 缓存（pieces_hash 索引 + 辅种记录）
│   └── utils/
│       ├── _bencode.py       # Bencode 编解码（纯 Python，零依赖）
│       ├── hash.py           # 哈希工具（SHA1 / Pieces SHA1）
│       └── logger.py         # 日志初始化
└── tests/
    ├── test_core.py          # 核心模块单元测试
    ├── test_integration.py   # 异步集成测试
    └── test_report.py        # 报告与飞书推送测试
```

## 快速开始

### 安装

```bash
pip install -r requirements.txt
```

### Docker 部署

```bash
# 构建镜像
docker build -t seedhound .

# 运行容器
docker run -d \
  --name seedhound \
  --restart unless-stopped \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -v $(pwd)/sites.yaml:/app/sites.yaml \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -v /path/to/BT_backup:/app/torrents \
  seedhound
```

| 挂载项        | 说明                                                        |
| ------------- | ----------------------------------------------------------- |
| `config.yaml` | 主配置文件，必须挂载                                        |
| `sites.yaml`  | 站点配置文件，API 模式必须挂载                              |
| `data/`       | SQLite 缓存 + Jackett 下载缓存，持久化                      |
| `logs/`       | 运行日志，方便排查问题                                      |
| `torrents/`   | 种子文件目录（对应 `downloader.torrent_dir`），只读挂载即可 |

**注意**：Docker 容器内需要访问宿主机的下载器（qBittorrent/Transmission），请确保 `config.yaml` 中 `downloader.host` 使用宿主机可访问的 IP（如 `host.docker.internal` 或宿主机真实 IP），不要使用 `127.0.0.1`。

### 配置

复制示例文件为实际配置文件：

```bash
cp config.example.yaml config.yaml
cp sites.example.yaml sites.yaml
```

编辑 `config.yaml`：

```yaml
global:
  concurrency: 20 # 并发站点查询数
  batch_size: 100 # 每批查询的 pieces_hash 数量
  api_timeout: 10 # 站点 API 查询超时（秒）
  download_timeout: 30 # 种子下载超时（秒）
  download_interval: 5.0 # 同站点下载间隔（秒）
  retry_count: 2 # 失败重试次数
  retry_delay: 2.0 # 重试间隔（秒）

db:
  path: data/cache.db # SQLite 数据库路径

log:
  level: INFO # 日志级别
  file: logs/seedhound.log # 日志文件路径

downloader:
  type: qbittorrent # qbittorrent 或 transmission
  host: 127.0.0.1
  port: 8080
  username: admin
  password: adminadmin
  torrent_dir: /path/to/BT_backup # 种子文件存放目录
  skip_hash_check: true # 跳过哈希校验（已有文件直接做种）
  auto_start: true # 自动开始做种
  tag: SeedHound # 为添加的种子打标签

# 双下载器模式（跨下载器辅种，源→目标）：
# downloader:
#   source:
#     type: qbittorrent
#     host: 192.168.1.100
#     port: 8080
#     username: admin
#     password: your_password
#     torrent_dir: /path/to/source/BT_backup
#   destination:
#     type: transmission
#     host: 127.0.0.1
#     port: 9091
#     username: admin
#     password: your_password
#     tag: SeedHound

notify:
  feishu:
    webhook_url: "" # 飞书机器人 Webhook 地址
    secret: "" # 飞书签名密钥（可选）

jackett:
  enabled: true
  url: "http://localhost:9117"
  api_key: "your_jackett_api_key"
  concurrency: 5 # 搜索并发数
  search_interval: 1.0 # 搜索间隔（秒）
  timeout: 30 # 请求超时（秒）
```

编辑 `sites.yaml` 填入各 PT 站点的 passkey：

```yaml
sites:
  - name: 站点名
    url: https://example.com
    api_url: https://example.com/api/pieces-hash
    passkey: your_passkey_here
```

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
```

| 参数           | 说明                                               |
| -------------- | -------------------------------------------------- |
| `--mode MODE`  | 辅种模式：`pieces_hash`(默认) / `jackett` / `both` |
| `--dry-run`    | 演练模式，只查询匹配不添加种子                     |
| `--site S1,S2` | 仅对指定站点辅种（逗号分隔）                       |
| `--no-feishu`  | 跳过飞书通知推送                                   |
| `sync-cookies` | 从 Cookie Cloud 同步 Cookie 到 sites.yaml          |

### Cookie 同步

SeedHound 支持从 [Cookie Cloud](https://github.com/easychen/CookieCloud) 自动同步各站点的 Cookie 信息，无需手动从浏览器导出。

Cookie Cloud 是一个浏览器插件，可将浏览器中的 Cookie 加密同步至自托管服务端。SeedHound 通过读取服务端数据，自动匹配 `sites.yaml` 中各站点的域名，将对应 Cookie 写入配置。

**前置条件**：

1. 在浏览器中安装 Cookie Cloud 插件，连接至你的 Cookie Cloud 服务端
2. 确保 Cookie Cloud 服务端可访问，且 `config.yaml` 中已配置连接参数

**配置**（在 `config.yaml` 中添加）：

```yaml
cookiecloud:
  enable: true
  url: "http://127.0.0.1:8082/cookie" # Cookie Cloud 服务端地址
  uuid: "your-uuid" # 用户 UUID
  password: "your-password" # 解密密码
```

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

> **注意**：`sites.yaml` 中的 cookie 值以双引号包裹且位于单行，避免 YAML 解析歧义。同步完成后建议提交 `sites.yaml` 以备份最新的站点认证信息。

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

| 类型         | 错误码                                                                           | 处理方式            |
| ------------ | -------------------------------------------------------------------------------- | ------------------- |
| **临时错误** | timeout, connection_error, rate_limit, http_5xx, too_short                       | 重试，不累计        |
| **严重错误** | http_401, http_403, http_404, http_410, auth_redirect, html_permission_denied 等 | 累计3次后跳过该站点 |

这避免了网络波动或服务器临时繁忙导致的误判——之前能正常辅种的站点不会因为网络抖动而被全部跳过。只有真正无法访问（认证过期、权限不足、种子已删除）的站点才会被自动跳过。

### 5. 自动开始已完成的种子

添加种子成功后，SeedHound 自动检测该种子是否已完成校验（数据已在本地磁盘）：

- 已完成 → 立即自动恢复，开始做种
- 未完成 → 保持暂停，等待用户手动处理

这确保辅种操作「即加即生效」，无需用户手动启动。

```
┌────────────┐    ┌─────────────┐    ┌──────────────┐
│  Parser    │    │  Seeder     │    │  Downloader  │
│  .torrent  │───>│  引擎调度    │───>│  qB/TR API   │
│  → pieces  │    │  三阶段流水线 │    │  添加种子    │
└────────────┘    └──────┬──────┘    └──────────────┘
                          │
                 ┌────────┴────────┐
                 │  HTTP Client    │
                 │  aiohttp 连接池  │
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

1. **扫描阶段**：递归遍历 `torrent_dir`，解析 `.torrent` 文件提取 `pieces_hash`，增量更新缓存
2. **查询阶段**：Pieces-hash 模式批量 POST 到各站点 `/api/pieces-hash`；Jackett 模式通过 Torznab API 跨站搜索
3. **添加阶段**：匹配到的种子依次执行 ① 从站点下载 `.torrent` → ② pieces_hash 校验 → ③ 添加到下载器（`skip_hash_check=false`, `paused=true`，由 qBittorrent 自行 recheck 确认文件匹配后做种）

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

配置站点时若不确定 API 是否可用，先用 `curl -X POST` 试一下即可确认。虽然配置简单，但我们已在后端内置了两种模式：第一种是通过每个站点的专用 API 进行精确查询，直接通过企业级的并发管道匹配；第二种则是接入 Jackett 索引器，在全局维度进行智能搜索并返回符合条件的结果。这两种方式互不干扰，双轨并行，确保无论是在内部站点还是外部资源平台，都能高效准确地完成数据请求与匹配任务。

## 依赖

```
aiohttp>=3.8
PyYAML>=6.0
aiosqlite>=0.20.0
```

> **可选依赖**：使用 Transmission 作为下载器时需额外安装：
>
> ```bash
> pip install transmission-rpc
> ```

## License

MIT
