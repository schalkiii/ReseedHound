# SeedHound

最简高性能 Python 辅种引擎 —— 跨 PT 站点间的高效种子调度工具，猎犬般自动嗅探并匹配可辅种任务。

## 项目亮点

### 极简架构

单进程，零外部中间件依赖。仅需 Python 3.9+、aiohttp、SQLite 即可运行。代码量精简至 ~1500 行，模块边界清晰，无魔法、无反射、无黑盒。

```
本地种子 → 提取 pieces_hash → 并发查询所有站点 API → 匹配 → 添加到下载器
```

全程异步非阻塞，不产生冗余线程或子进程开销。

### 极限性能

| 维度 | 策略 | 效果 |
|------|------|------|
| 全链路异步 | `asyncio` + `aiohttp` 连接池复用 | 万级种子秒级完成扫描 |
| 站点并发查询 | 信号量限流 + `asyncio.gather` | 94 条查询链路并行，耗时约等于最慢站点 |
| 增量缓存 | SQLite 持久化 pieces_hash 索引 | 已扫描种子零重复解析 |
| 批量 API 调用 | 单次 POST 最多 100 个 pieces_hash | 减少 HTTP 往返，实测提升 3-5x |
| Bencode 纯 Python | 零依赖解析器，线程池并行 | 解包 37000+ 种子文件 < 20 秒 |
| 分批限流添加 | 30 个/批 + 0.3s 间隔 | 避免下载器瞬时阻塞 |

### 飞书仪表盘卡片

辅种完成后自动推送结构化仪表盘卡片到飞书机器人，包含：

- **总览面板**：辅种成功/下载失败/全站匹配/追踪器跳过/扫描种子/去重/新增/唯一，关键指标一目了然
- **站点详情表**：各站点匹配/成功/失败数量，按成功率排序，带状态图标（🟢全部成功 / 🟡部分成功 / 🔵有匹配 / ⚪无匹配）
- **页脚摘要**：总耗时、站点数、命中数、无匹配数、死种数
- 卡片使用蓝色主题头 + 分隔线分区 + 标题状态图标（✅成功 / ⚠️部分 / ❌无匹配）

### 全程可观测

- 四级日志：控制台 + 文件双输出，自动轮转
- SQLite 缓存表记录完整辅种历史
- 支持 `--dry-run` 演练模式：只查询不添加
- 支持 `--no-cache` 强制全量重建

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

### 配置

复制示例文件为实际配置文件：

```bash
cp config.example.yaml config.yaml
cp sites.example.yaml sites.yaml
```

编辑 `config.yaml`：

```yaml
global:
  concurrency: 20              # 并发站点查询数
  batch_size: 100              # 每批查询的 pieces_hash 数量
  request_timeout: 15          # HTTP 请求超时（秒）
  retry_count: 2               # 失败重试次数
  retry_delay: 2.0             # 重试间隔（秒）

db:
  path: data/cache.db          # SQLite 数据库路径

log:
  level: INFO                  # 日志级别
  file: logs/seedhound.log     # 日志文件路径

downloader:
  type: qbittorrent            # qbittorrent 或 transmission
  host: 127.0.0.1
  port: 8080
  username: admin
  password: adminadmin
  torrent_dir: /path/to/BT_backup    # 种子文件存放目录
  skip_hash_check: true              # 跳过哈希校验（已有文件直接做种）
  auto_start: true                   # 自动开始做种
  tag: SeedHound                     # 为添加的种子打标签

notify:
  feishu:
    webhook_url: ""            # 飞书机器人 Webhook 地址
    secret: ""                 # 飞书签名密钥（可选）
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

# 演练模式（只查询不添加）
python run.py --dry-run

# 指定站点辅种（逗号分隔多个）
python run.py --site 站点A,站点B,站点C

# 跳过飞书通知
python run.py --no-feishu

# 强制全量重建缓存
python run.py --no-cache
```

| 参数 | 说明 |
|------|------|
| `--dry-run` | 演练模式，只查询匹配不添加种子 |
| `--site S1,S2` | 仅对指定站点辅种（逗号分隔） |
| `--no-feishu` | 跳过飞书通知推送 |

## 辅种方法学

SeedHound 的设计核心是**用最小站点压力换取最大辅种收益**。以下是其关键优化策略：

### 1. 按内容去重而非按种子去重

不同站点发布的同一资源，尽管 `.torrent` 文件的 `info_hash` 不同，但文件内容的 `pieces_hash` 相同。SeedHound 在扫描阶段以 `pieces_hash` 为键对所有本地种子去重：

```
38,000 个 .torrent 文件 → 按 pieces_hash 去重 → ~4,000 个唯一种子
```

这意味着向站点 API 发送的查询量减少约 **89%**，大幅降低站点压力。

### 2. Tracker 解析跳过（零站点流量）

种子添加阶段，通过解析 qBittorrent `BT_backup` 目录中现有 `.torrent` 文件的 `announce` 字段，直接判断某个种子是否已在目标站点做种：

```
qB 现有 .torrent → 提取 announce URL → 解析域名 → 匹配站点
种子已在站点 [A, B] 做种 → 跳过站点 A、B 的匹配 → 仅添加新站点
```

**这是避免重复添加的核心机制**。无需下载站点 `.torrent`、无需查询站点 API，完全在本地完成判断。

### 3. 辅种记录双重校验

对每条候选辅种记录，执行两层过滤：
1. **逻辑层**：查询 `reseed_history` 表，跳过已记录的成功辅种
2. **物理层**：对比历史记录中的 `pieces_hash` 是否仍在 qBittorrent 中存在；已被删除的种子自动重新尝试

这解决了 `reseed_history` 不完整的冷启动问题——即使历史记录为空，物理层校验仍能防止重复添加。

### 4. 增量缓存

已解析的 `.torrent` 文件的 `pieces_hash` 持久化到 SQLite。后续运行时仅需增量扫描新增文件，避免每次全量解析。

### 5. 智能站点跳过（严重错误累计）

下载种子时发生的错误被分为两类：

| 类型 | 错误码 | 处理方式 |
|------|--------|---------|
| **临时错误** | timeout, connection_error, rate_limit, http_5xx, too_short | 重试，不累计 |
| **严重错误** | http_401, http_403, http_404, http_410, auth_redirect, html_permission_denied 等 | 累计3次后跳过该站点 |

这避免了网络波动或服务器临时繁忙导致的误判——之前能正常辅种的站点不会因为网络抖动而被全部跳过。只有真正无法访问（认证过期、权限不足、种子已删除）的站点才会被自动跳过。

### 6. 自动开始已完成的种子

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
                 └────────┬────────┘
                          │
          ┌───────────────┼───────────────┐
          │               │               │
     ┌────┴────┐    ┌────┴────┐    ┌────┴────┐
     │ Site A  │    │ Site B  │    │ Site C  │
     │ /api/   │    │ /api/   │    │ /api/   │
     │ pieces- │    │ pieces- │    │ pieces- │
     │ hash    │    │ hash    │    │ hash    │
     └─────────┘    └─────────┘    └─────────┘
```

三阶段流水线：

1. **扫描阶段**：递归遍历 `torrent_dir`，解析 `.torrent` 文件提取 `pieces_hash`，增量更新缓存
2. **查询阶段**：异步批量 POST `pieces_hash` 到各站点 `/api/pieces-hash` 接口
3. **添加阶段**：匹配到的种子按站点分批添加到下载器，间隔 0.3s 防抖

## 依赖

```
aiohttp>=3.8
PyYAML>=6.0
aiosqlite>=0.20.0
```

## License

MIT