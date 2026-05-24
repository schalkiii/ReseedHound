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

### 飞书实时报告

辅种完成后自动推送结构化卡片消息到飞书机器人，包含：

- 扫描统计（本地种子数、新增数、匹配数）
- 运行耗时
- 各站点成功辅种明细（按数量降序排列）
- 失败站点列表
- 无匹配站点列表
- 站点汇总（启用数 / 命中数 / 未命中数）

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
reseed_puppy_standalone/
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
  file: logs/reseed.log        # 日志文件路径

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
# 正常运行
python run.py

# 演练模式（只查询不添加）
python run.py --dry-run

# 强制全量重建缓存
python run.py --no-cache
```

## 技术架构

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