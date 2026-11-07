# SeedHound 情况说明与改进建议（2026-11-07）

> 本文由 `project-audit` 流程产出，**只做诊断与建议，未修改任何代码或配置**。
> 目标读者：对项目零背景、需要按条开工的执行 Agent。所有路径相对仓库根目录 `D:\workspace\SeedHound`。
> 后续执行阶段请拼接 `software-improvement` 的改动纪律（最小改动 / 逐点验证 / 注释同步），
> 且**一次任务只走一个分支**（架构 / 代码质量 / 文档 / 测试），四条分支不混做。

---

## 0. 结论摘要

项目代码结构清晰、README 与 CHANGELOG 维护程度较高，qBittorrent 5.3.0 兼容部分的文档与实现一致，
属于可维护状态。但审计发现 **4 个已实测复现的缺陷**、**多处配置/开关静默失效**、
**测试门禁在干净环境不可执行**，以及一批文档与实现互相矛盾的内容。按风险排序，最需要优先处理的是：

1. `python run.py --dry-run reseed` 会静默变成真实添加种子（演练模式失效，已实测）。
2. 文档承诺的「双下载器模式」启动时必抛 `KeyError: 'host'`（已实测）。
3. Transmission 作为目标下载器时 `add_torrent` 签名不匹配，且异常被吞，辅种静默失败（签名已实测）。
4. `scheduler.run_on_start` 配置项取值后从不参与控制流，`false` 完全无效（已实测）。
5. `requirements.txt` 未声明 `pytest` / `pytest-asyncio` / `flake8`，`.github/workflows/` 无测试工作流，
   AGENTS.md 给出的两条门禁命令在干净环境无法执行（已实测）。

---

## 1. 现状描述

### 1.1 规模（客观计数）

| 项目 | 数量 |
|---|---|
| Python 文件（不含 `__pycache__`） | 合计 28 个：`src/` 下 22 个（7 个 `__init__.py` + 15 个实质模块）、`run.py`、`scripts/weekly_report.py`、`tests/` 下 4 个 |
| Python 代码行数 | 约 4,100 行（含测试约 390 行），最大单文件 `src/engine/seeder.py` 724 行 |
| 测试文件 / 测试函数 | 3 个测试模块（`test_core.py` / `test_integration.py` / `test_report.py`）+ 1 个 `__init__.py`；共 **17** 个测试函数（10 / 4 / 3） |
| 文档 | `README.md` 665 行、`CHANGELOG.md` 158 行、`AGENTS.md` 66 行 |
| 配置与构建 | `config.example.yaml` 83 行、`sites.example.yaml` 21 行、`requirements.txt` 7 行、`Dockerfile` 23 行、`.github/workflows/docker.yml` 59 行、`.flake8` 2 行、`.gitignore` 27 行、`.dockerignore` 34 行、`SeedHound.bat` 1 行 |
| CI 工作流 | 1 个（`docker.yml`，仅构建并推送 GHCR 镜像） |

本地工作区另有 `config.yaml`（57 行）、`sites.yaml`（612 行）、`sites.backup.yaml`（576 行）、
`sites.yaml.bak`、`logs/`、`data/`，均在 `.gitignore` 内。这些文件含真实凭据，本报告不复述其值。

### 1.2 结构

单一入口 `run.py`，业务代码集中在 `src/` 下按职责分层：
`engine/`（seeder / parser / downloader / jackett）、`network/`（client / cookiecloud）、
`storage/`（config / cache）、`notify/`（feishu）、`report/`（generator）、`utils/`（_bencode / hash / logger）；
另有 2 个横切模块 `src/scheduler.py`（定时循环）与 `src/cookie_sync.py`（CookieCloud 同步）。

运行链路：`run.py` → `Config`（YAML 单点加载）→ `ReseedEngine` 三阶段流水线
（扫描本地 `.torrent` → 并发查站点 / Jackett → 下载并添加进下载器）→ `ReportGenerator` + `FeishuNotifier`。
持久化有三套：SQLite（`aiosqlite`，缓存与辅种历史）、JSON（`data/jackett_dl_cache.json`）、文本日志（`logs/`）。
部署路径为 `Dockerfile` + `docker.yml`（GHCR 发布）+ `SeedHound.bat`（Windows 本机）。

### 1.3 质量水平（可观察事实）

- 存在 `AGENTS.md` 项目约定、`CHANGELOG.md` 按日期倒序维护到 2026-11-07、README 覆盖部署 / 配置 / 方法学。
- 存在 `.flake8`（`max-line-length = 120`）与 17 项测试；`CHANGELOG` 记录 2026-11-07 曾把测试扩充至 17 项并全套通过。
- 实测：当前会话默认解释器为 Python 3.14.3，**未安装项目依赖**。
  `python -m pytest tests/ -q` → `No module named pytest`；
  `python tests/test_core.py` 前 5 项通过、第 6 项 `ModuleNotFoundError: No module named 'yaml'` 中断；
  `python tests/test_report.py` → `ModuleNotFoundError: No module named 'aiohttp'`。
  `requirements.txt` 中不存在 `pytest` / `pytest-asyncio` / `flake8`。
- 仓库内 Python 版本声明共 3 处且互不相同：README 第 23 行 `Python 3.9+`、
  AGENTS.md 第 27 行 `Python 3.13`、Dockerfile 第 1 行 `python:3.11-slim-bookworm`。
- `.github/workflows/` 下只有 1 个构建推送工作流，无测试 / lint 工作流，无 `pytest.ini` / `pyproject.toml`。

---

## 2. 问题清单

严重程度定义：**高** = 已造成功能错误、数据写入风险或门禁不可执行；
**中** = 文档误导、配置项静默失效、维护陷阱；**低** = 局部写法或工程细节。

### 维度 1 · 文档一致性

| 编号 | 位置 | 问题描述 | 严重 | 影响范围 |
|---|---|---|---|---|
| Q-01 | `README.md:93-95` vs `run.py:106-194,199-207` | README 声明「硬编码零配置原则 / 不接受命令行临时参数注入。所有配置收敛于 YAML 文件」，实现却有 `--dry-run`/`--no-feishu`/`--site`/`--mode`/`--config`/`--sites-path`、3 个子命令及 `SEEDHOUND_MODE` 环境变量注入 | 中 | 使用者误判配置优先级；文档可信度 |
| Q-02 | `README.md:23` / `AGENTS.md:27` / `Dockerfile:1` | Python 版本三处矛盾（3.9+ / 3.13 / 3.11）；README「仅需 Python 3.9+、aiohttp、SQLite 即可运行」与 `requirements.txt` 的 7 个依赖及 `qbittorrent-api>=2026.8.1` 下限不符 | 中 | 环境准备、镜像构建、依赖兼容性判断 |
| Q-03 | `README.md:309-365` vs `config.example.yaml:4-83` | 配置示例数值与字段不一致：`api_timeout` 10 vs 60、`download_timeout` 30 vs 15、`log.file` `logs/seedhound.log` vs `logs/reseed.log`、`port` 8080 vs 9091；README 示例缺 `mode` / `scheduler` / `cookiecloud` 三个块 | 中 | 按 README 复制配置会得到与模板不同的行为 |
| Q-04 | `CHANGELOG.md:84-85` / `README.md:370-375` / `sites.example.yaml:8,15,21` / `src/storage/config.py:109` | CHANGELOG 声称「已兼容 `enable`/`enabled` 两种写法，优先读取 `enable`」且「`sites.example.yaml` 补充了 `enable` 字段」，但 `config.py` 的 `sites` 属性只读 `s.get("enabled", True)`，模板与本地 `sites.yaml` 用的也是 `enabled` | 中 | 用户按文档写 `enable: false` 时站点仍参与辅种，且无任何报错 |
| Q-05 | `config.example.yaml:25` / `README.md:421` / `CHANGELOG.md:70` vs `src/scheduler.py:98,104` | 三处文档均承诺 `run_on_start: false` 会「先等待一个完整间隔」，实现中该值取出后仅用于日志打印，`run()` 的 `while` 循环无条件先执行一轮 | 高 | 错峰启动策略失效；定时模式下首轮立即全量扫描 |
| Q-06 | `README.md:604` vs `src/engine/parser.py:88` | 文档称「递归遍历 `torrent_dir`」，实现是 `dir_path.glob("*.torrent")`，只扫当前一层 | 中 | 种子按子目录组织时静默扫描到 0 个 |
| Q-07 | `README.md:35` vs `src/network/client.py:163` / `src/engine/jackett.py:118` | 文档称「`aiohttp` 连接池复用」，实现的两处 `TCPConnector` 都设 `force_close=True` | 中 | 性能预期与实际不符，长连接收益不成立 |
| Q-08 | `README.md:59,63-68` vs `src/report/generator.py:31-36,96` / `src/notify/feishu.py:83-92` | 文档称站点表「按成功率排序」且文本报告状态灯为 🟢🟡🔵⚪ 四档；实现中文本报告只有 🟢🔴⚪ 三档、按匹配数排序；飞书卡片是四档、按 `(无失败优先, -成功数, -匹配数)` 排序 | 中 | 报告语义与文档不符，两套实现规则还互不相同 |
| Q-09 | `README.md:106-138` | 目录结构树缺 `src/cookie_sync.py`、`src/engine/jackett.py`、`src/network/cookiecloud.py`、`AGENTS.md`、`CHANGELOG.md`、`.github/workflows/docker.yml` | 中 | 接手者无法从文档建立完整模块地图 |
| Q-10 | `README.md:210-213,236` vs `.github/workflows/docker.yml:45-49` | 文档称 `latest` = 最新发布版本、由 `v*` tag / Release 触发；工作流用 `type=raw,value=latest` **无条件**生成 `latest`，`workflow_dispatch` 手动触发也会覆盖 | 中 | 非发布构建污染 `latest`，与文档承诺冲突 |
| Q-11 | `README.md:109` vs `SeedHound.bat:1` | 文档称 Windows 一键启动「等价于 `python run.py`」，脚本只有一行 `python .\run.py`，无目录切换、无暂停、无错误提示 | 中 | 双击后窗口立即关闭；出错不可见 |
| Q-12 | `README.md:657-661` vs `requirements.txt:6` | 文档称 Transmission 是「可选依赖」需额外 `pip install transmission-rpc`，但 `requirements.txt` 已强制声明 | 低 | 安装指引重复 |
| Q-13 | `README.md:17,637` | 「飞书仪卡片式推送」错别字；第 637 行末段「虽然配置简单，但我们已在后端内置了两种模式…」与前文重复、语体不一致 | 低 | 文档专业度 |
| Q-14 | `README.md:564-575` / `CHANGELOG.md:3-20` / 实现 | **本维度正面结论**：qBittorrent 5.3.0 的 `torrents/add` 返回体、`seedMode` 改名、`stoppedUP` 改名、`is_paused=False` 折叠、日志 UTF-8 重配置等描述与 `downloader.py` / `seeder.py` / `logger.py` 实现一致，无需改动 | — | 保持现状 |

### 维度 2 · 文档缺漏

| 编号 | 位置 | 问题描述 | 严重 | 影响范围 |
|---|---|---|---|---|
| Q-15 | 全仓库 | 无 `doc/` 目录、无架构决策或接口文档；配置字段没有权威说明。**配置默认值散落 4 处且互相矛盾**：`api_timeout` 在 `seeder.py:78` 默认 10、`config.example.yaml:7` 60、README 10、本地 `config.yaml` 60；`download_timeout` 在 `seeder.py:79` 30、模板 15、README 30、本地 45 | 高 | 无法判断任一字段的真实缺省行为；改一处必漏其余 |
| Q-16 | `sites.example.yaml` / `src/storage/config.py` | 缺 `api_url`、`cookie` / `cookies` 优先级、`download_interval`、`enable` vs `enabled` 语义的字段说明 | 中 | 站点配置靠猜 |
| Q-17 | `src/network/client.py:190-315` / `src/engine/seeder.py:521-534` | 缺错误码（`error_reason`）与 `SERIOUS_ERRORS` / `MAX_SERIOUS_ERRORS` / `MAX_TIMEOUT_ERRORS` 的清单与语义文档 | 中 | 站点跳过策略不可解释、不可调参 |
| Q-18 | `README.md:336-351` | 双下载器模式只有配置片段，没有「主下载器 = destination」的运行语义说明，也没有该模式当前不可用的提示 | 中 | 与 Q-27 叠加，用户无法自行定位 |
| Q-19 | `.github/workflows/docker.yml:45-49` / `README.md:206-213` | 缺镜像 tag 生成规则文档（`type=ref,event=tag` 与 `type=semver` 在 `v*` 上会产生 `v1.0.0` 与 `1.0.0` 两个标签） | 低 | 版本选择困惑 |
| Q-20 | `AGENTS.md:5-13` / `requirements.txt` | 缺测试运行前置条件（需另装 `pytest` / `pytest-asyncio` / `flake8`）与 `asyncio_mode` 配置说明 | 中 | 门禁命令在干净环境直接失败 |

### 维度 3 · 架构优化点

| 编号 | 位置 | 问题描述 | 严重 | 影响范围 |
|---|---|---|---|---|
| Q-21 | `src/engine/seeder.py`（724 行） | `ReseedEngine` 同时承担扫描、查询、添加、Jackett、统计、自动开始 6 类职责；`__init__:93-110` 把 `--mode` 语义与 `jackett_enabled` 耦合，当 `jackett.enabled=false` 时 `--mode jackett/both` 静默降级为 `pieces_hash` 且无告警 | 中 | 模式切换不可预期；单文件改动风险集中 |
| Q-22 | `run.py:45-72` vs `src/scheduler.py:71-93` | `cmd_reseed` 与 `ReseedScheduler._run_once` 是逐行复制的同一流程（建引擎 → start → run → 生成报告 → print → 飞书 → close）；`_run_once` 的 docstring 却称「与一次性运行完全一致的代码路径」，实际是两份实现 | 中 | 已出现改一处漏一处：`cmd_reseed` 自带 `except` 与返回码，`_run_once` 内没有异常捕获（由外层 `run()` 兜底），两处行为已不等价 |
| Q-23 | `src/storage/config.py` / `seeder.py:77-89` / `client.py:133-141` / `jackett.py:93-98` | 无配置 schema 校验与默认值单一来源；字段拼错静默回退（Q-04 已踩坑）。`cookiecloud.enable` 与 `scheduler.enabled` / `jackett.enabled` / `sites[].enabled` 同一语义两种拼写 | 中 | 配置错误无提示，排障成本高 |
| Q-24 | `src/engine/downloader.py:9-46` vs `:251-257` | `DownloaderBase.add_torrent` 契约与 `TransmissionDownloader` 实现不一致（详见 Q-28），抽象层未起到约束作用 | 高 | 与 Q-28 同一后果 |
| Q-25 | `src/report/generator.py` / `src/notify/feishu.py` | 报告排版与站点表在两处独立实现，状态判定、排序规则、无匹配站点展示阈值（飞书 `len<=10`）各不相同 | 中 | 同一轮辅种，文本报告与飞书卡片结论可能不一致 |
| Q-26 | `scripts/weekly_report.py:29-41,97-157` | 周报用 9 个正则解析自由文本日志，与日志格式强耦合；`RUN_MARKER`（`logger.py:6`）与 `LOG_MARKER`（`weekly_report.py:29`）同值不同名，两处各维护一份 | 中 | 日志文案一改，周报静默失真 |

### 维度 4 · 代码写法（含已实测缺陷）

| 编号 | 位置 | 问题描述 | 严重 | 影响范围 |
|---|---|---|---|---|
| Q-27 | `src/engine/seeder.py:124-125` | `start()` 用 `self._config.downloader_config` 创建主下载器；双下载器模式下该 dict 只有 `source` / `destination` 两个键，`create_downloader` 的 `config["host"]` 直接抛 **`KeyError: 'host'`**（已实测） | 高 | README 与 CHANGELOG 承诺的双下载器 / 跨下载器辅种完全不可用 |
| Q-28 | `src/engine/downloader.py:251-257` + `seeder.py:665-671,713` | `TransmissionDownloader.add_torrent` 签名缺 `torrent_files`，且 `download_url`/`save_path` 为必填位置参数；调用方以 `torrent_files=...` 关键字传入，签名比对已实测不一致，必然 `TypeError`；该异常发生在参数绑定阶段、位于 `try` 之外，随后被 `asyncio.gather(..., return_exceptions=True)` 吞掉。方法内 `skip_hash_check` / `tag` 也未使用 | 高 | Transmission 作为目标下载器时辅种静默失败，该种子既不计成功也不计失败 |
| Q-29 | `run.py:106-194` | 顶层参数与子命令参数同名。实测最小复现 `parse_args(['--dry-run','reseed'])` → `Namespace(command='reseed', dry_run=False)`：子解析器默认值覆盖父解析器已解析的值 | 高 | `python run.py --dry-run reseed` 演练模式静默失效，会真实添加种子；`--no-feishu` / `--site` / `--mode` / `--config` / `--sites-path` 同理 |
| Q-30 | `src/scheduler.py:98,104,115-119` | `run_on_start` 取值后仅出现在日志中，`while` 无条件先跑一轮（Q-05 的实现侧） | 高 | 配置项静默失效 |
| Q-31 | `src/notify/feishu.py:14,137-154` | `self._secret` 保存后从未使用，`_send` 未实现飞书签名（`timestamp` + `sign`）；README 与模板都提供 `secret` 字段 | 中 | 机器人开启签名校验时推送必然失败，且无针对性报错 |
| Q-32 | `seeder.py:459,713` / `client.py:460` | 三处 `asyncio.gather(..., return_exceptions=True)` 静默吞异常；站点查询失败不进入 `failed_sites`、种子添加异常不进入 `failed_count` | 中 | 统计口径失真，故障无痕 |
| Q-33 | `src/storage/cache.py:24-31,111-119` | `reseed_history` 表无索引、无唯一约束；`seeder.py:657-663` 的「hash 已在客户端」分支每次都会再插一条记录 | 中 | 4 万种子规模下历史表单调膨胀，`get_reseeded_hashes_by_site` 全表扫描 |
| Q-34 | `cache.py:51,88` / `feishu.py:16` / `downloader.py:43-46,196-210` | 死代码：`is_cached`、`get_reseeded_combos`（CHANGELOG 称已移除调用但方法未删）、`send_text`、`add_trackers_to_torrent`（全项目无调用，TR 实现直接 `return False`） | 低 | 阅读噪音，误以为功能存在 |
| Q-35 | `src/network/client.py:59-79` | `RATE_LIMIT_KEYWORDS` 中「过于频繁」重复出现 2 次；`RATE_LIMIT_HINT_PATTERNS` 第 1 条 `(\d+)\s*分钟后` 已覆盖第 2 条 `(\d+)\s*分钟后重试`，第 2 条永不生效 | 低 | 维护噪音 |
| Q-36 | `run.py:20` / `src/utils/logger.py:53-81` | `cmd_sync_cookies` 用 `logging.basicConfig`，其余用 `setup_logger`；`setup_logger` 每次调用都 `addHandler` 而不清理既有 handler | 低 | 单进程多入口时日志重复输出 |
| Q-37 | `src/scheduler.py:107` | `asyncio.get_event_loop()` 在 Python 3.12+ 已弃用、3.14 行为变化；与 AGENTS.md 声明的 3.13 及 Dockerfile 的 3.11 均不吻合 | 低 | 未来版本升级风险 |
| Q-38 | `src/engine/parser.py:76` / `seeder.py:226-227` | `parse_bytes` 用 `except Exception: return None` 吞掉全部异常；`torrent_dir` 缺省 `""` 时 `Path("")` 指向当前目录，`glob` 返回空列表且无告警 | 低 | 损坏种子 / 配置缺失时静默扫描 0 个 |
| Q-39 | `src/cookie_sync.py:81-83` | 用 `re.sub(r"\\\n    \\ ", " ", raw)` 正则修补 YAML 折行输出，脆弱、无注释、无测试 | 低 | Cookie 值含特殊字符时可能被改写 |
| Q-40 | `client.py:366-367` / `:450` / `downloader.py:108` / `weekly_report.py:259` | 细节兜底：`float(retry_after)` 遇到 HTTP-date 形式会抛 `ValueError` 且不在 except 内；`response.get("data")` 假定 JSON 为 dict；`_is_add_success` 对未知类型一律返回 `True`；周报一次性 `readlines()` 全量载入日志 | 低 | 边界场景静默失败或内存占用 |
| Q-41 | `src/engine/downloader.py:300` | `create_downloader` 仍保留 `password` 默认值 `"adminadmin"`；CHANGELOG 的安全承诺只覆盖 CookieCloud 凭据 | 低 | 与「凭据不写进代码默认值」的原则不一致 |

### 维度 5 · 测试覆盖率

| 编号 | 位置 | 问题描述 | 严重 | 影响范围 |
|---|---|---|---|---|
| Q-42 | `tests/` 全体 | 17 项测试实际覆盖：`_bencode`、`hash`、`parser.parse_file/scan_directory`、`Config` 基本读取、`_is_add_success`、`STOPPED_COMPLETE_STATES`、`redact_secrets`/filter、`_ensure_started`、`cache.init/filter_uncached`、`ReportGenerator.generate/generate_markdown`、`FeishuNotifier.from_config` | — | 以下为零覆盖清单 |
| Q-43 | 零覆盖模块 | `src/scheduler.py`(139)、`src/cookie_sync.py`(93)、`src/network/cookiecloud.py`(111)、`src/network/client.py`(472)、`src/engine/jackett.py`(433)、`scripts/weekly_report.py`(280) 全部无测试；`run.py`(231) 的 CLI 解析无测试；`TransmissionDownloader` 整个类无测试；`seeder.py` 的 `run` / `_phase_scan` / `_phase_query_sites` / `_phase_add_torrents` / `_phase_jackett_search` 无测试 | 高 | Q-27 / Q-29 / Q-30 等缺陷均是「无测试区域」里发现的，说明缺口已造成实际漏网 |
| Q-44 | 零覆盖分支 | `--mode` 三模式切换、双下载器创建、`run_on_start`、三层过滤、错误累计跳过（3 次严重 / 30 次超时）、死种持久化 15 天阈值、Jackett 尺寸过滤与下载缓存、CookieCloud 解密与域名匹配、飞书签名、CLI 参数组合 | 中 | 回归无保护 |

### 维度 6 · 测试严谨性

| 编号 | 位置 | 问题描述 | 严重 | 影响范围 |
|---|---|---|---|---|
| Q-45 | `tests/test_core.py:87-91` / `tests/test_integration.py:100-105` | 测试直接读取 `.gitignore` 内的 `config.yaml` / `sites.yaml`，并断言 `concurrency == 20`、`batch_size == 100`、`log.level == "INFO"` 等本地具体值；这两个文件含真实 passkey / Cookie / 密码 | 高 | 干净克隆与 CI 必然 `FileNotFoundError`；测试过程触碰生产凭据；改配置即红 |
| Q-46 | `tests/test_integration.py:27,45,78,96` | 使用 `@pytest.mark.asyncio`，但 `requirements.txt` 无 `pytest` / `pytest-asyncio`，仓库无 `pytest.ini` / `pyproject.toml` 配置 `asyncio_mode`；`.flake8` 存在但 `flake8` 同样未声明。实测 `python -m pytest tests/ -q` → `No module named pytest` | 高 | AGENTS.md 的两条门禁命令在干净环境不可执行 |
| Q-47 | `test_core.py:90-91` / `test_integration.py:35` / `test_report.py:53-62` | 弱断言：`sites = config.sites` 后只 `print` 无断言；`assert stats["cached_torrents"] >= 0` 恒真；`print(report)` 后仅 6 个子串 `in` 断言，无结构化比对；`assert "启用 14 个" in report` 硬编码排版 | 中 | 「跑通即通过」，排版或数值错一位不易发现 |
| Q-48 | `test_core.py:41-62` | `info_hash` / `pieces_hash` 用自身实现 `sha1_hex(bencode(info))` 自证，bencode 只做 roundtrip；无外部 golden 值比对 | 中 | 自洽性测试不等于正确性测试，哈希实现共同出错时全绿 |
| Q-49 | `tests/` | 无边界 / 异常用例：损坏 torrent、空目录、HTTP 非 200 / 429 / 超时重试、CookieCloud 解密失败、DB 并发、GBK 控制台 emoji、双下载器、TR 路径 | 中 | 与 Q-43 叠加 |
| Q-50 | `tests/test_core.py:168-182` / `test_report.py:85-89` / `test_integration.py:108-120` | 三个测试模块各保留一套 `if __name__ == "__main__"` 手工调用块与 `print("PASS: ...")`，与 pytest 收集并存，容易与真实套件脱节。本次实测 `python tests/test_core.py` 前 5 项各输出「PASS」后在第 6 项中断，手工运行方式会给出「部分通过」的假象 | 中 | 假阳性 |
| Q-51 | `tests/test_integration.py:31,49` | 测试在项目 `data/` 下创建 `test_cache.db` / `test_filter.db`，靠 `try/except OSError` 删除，无 `tmp_path` fixture | 低 | 并发执行或异常中断时残留 |
| Q-52 | `.github/workflows/` | 只有 `docker.yml`，无执行 `pytest` / `flake8` 的工作流 | 中 | 门禁不自动执行，回归只能靠人工 |

### 维度 7 · 工程规范

| 编号 | 位置 | 问题描述 | 严重 | 影响范围 |
|---|---|---|---|---|
| Q-53 | `requirements.txt` / `AGENTS.md:5-13` | 7 个运行时依赖齐全，但未声明 `pytest` / `pytest-asyncio` / `flake8`；无 `requirements-dev.txt` 或可选依赖组 | 高 | 与 Q-46 同一根因 |
| Q-54 | `.github/workflows/` / 仓库根 | 无测试 / lint 工作流，无 `pytest.ini` / `pyproject.toml` / `setup.cfg` / `tox.ini`；`pytest-asyncio` 的 `asyncio_mode` 未配置 | 中 | 与 Q-52 同一根因 |
| Q-55 | `SeedHound.bat:1` | 单行 `python .\run.py`：无 `cd /d "%~dp0"`、无 `pause`、无 Python 存在性检查、无错误码回显、不激活虚拟环境。且当前工作区本地 `config.yaml`（该文件不入库）的 `scheduler.enabled=true`，该脚本在本机实际启动的是**常驻定时模式**而非一次性辅种 | 中 | 双击后窗口立即关闭；出错不可见；行为与「一键辅种」直觉不符 |
| Q-56 | `.github/workflows/docker.yml:45-49` | `type=raw,value=latest` 无条件生成 `latest`；`type=ref,event=tag` 与 `type=semver,pattern={{version}}` 在 `v*` tag 上重复产出版本标签。`on.workflow_dispatch` 允许任意分支手动触发并覆盖 `latest` | 中 | 与 Q-10 同一根因 |
| Q-57 | `Dockerfile:13-16` / `.dockerignore:34-35` | 镜像只 `COPY src/` 与 `run.py`，`scripts/weekly_report.py` 不在镜像内（README 未说明）；`.dockerignore` 排除 `*.md` 后又 `!README.md`，但 Dockerfile 不 COPY 任何 md，该例外无意义 | 低 | 容器内周报脚本不可用；配置噪音 |
| Q-58 | `.flake8` | 仅 `max-line-length` 与 `extend-ignore`，无 `exclude` / `per-file-ignores`；三个测试文件因此各自手写 `# noqa: E402` | 低 | 门禁覆盖面不清 |
| Q-59 | `CHANGELOG.md:76` / `README.md:157,204` / `Dockerfile:5` | 历史条目仍写 `ghcr.io/reseed-puppy/seedhound`，当前 README 写 `ghcr.io/schalkiii/reseedhound`，Dockerfile LABEL 写 `github.com/schalkiii/ReseedHound` | 低 | 去历史化遗留，易误导复制命令 |
| Q-60 | 仓库根 | 本地存在 `sites.backup.yaml`(576 行) / `sites.yaml.bak` 等备份文件；`.gitignore` 已覆盖，无入库风险 | 低 | 工作区噪音 |

### 2.1 已核实并排除的假设（防止后续 Agent 误改）

| 假设 | 核实结果 | 结论 |
|---|---|---|
| `.github/workflows/docker.yml` 的 `IMAGE_NAME: ${{ github.repository }}` 会得到含大写的 `ghcr.io/schalkiii/ReseedHound`，导致 GHCR 推送因「repository name must be lowercase」失败 | `docker/metadata-action` 官方 README 的「Image name and tag sanitization」明确写明该 action 会自动 **Lowercase the image name** | **不构成缺陷**。实际推送地址与 README 的 `ghcr.io/schalkiii/reseedhound` 一致，不要为此修改 `IMAGE_NAME` |
| README 的 qBittorrent 5.3.0 兼容描述过时 | 逐条比对 `downloader.py:92-108,120-134`、`seeder.py:21,488-506,690-697`、`logger.py:53-58` | 全部属实，保持现状 |
| `SeedHound.bat` 不存在 | 文件存在，1 行 | 见 Q-11 / Q-55，需加固而非新建 |

### 2.2 本次审计的验证方法与局限

- 通读了全部 22 个 `src/*.py`、`run.py`、`scripts/weekly_report.py`、3 个测试模块、
  README / CHANGELOG / AGENTS.md、全部配置与构建脚本。
- **实测**：`python -m pytest tests/ -q`；`python tests/test_core.py`；`python tests/test_report.py`；
  argparse 最小复现；`create_downloader` 双下载器调用；
  `inspect.signature(DownloaderBase.add_torrent)` vs `TransmissionDownloader.add_torrent`；
  `sites.yaml` 中 `enable|enabled` 字段分布；`run_on_start` 全仓引用；
  `docker/metadata-action` 官方 README（小写化规则）。
- **局限**：当前会话默认解释器为 Python 3.14.3 且未安装项目依赖，因此 **17 项测试的全绿基线无法在本环境复现**。
  Q-27 / Q-28 / Q-29 的结论来自静态分析 + 最小复现（不依赖项目依赖），置信度高；
  涉及真实 qBittorrent / Jackett / CookieCloud 服务端的行为未做端到端验证，相关建议在「验证方式」中给出实测步骤。
- 本报告不记录任何 `config.yaml` / `sites.yaml` 中的真实凭据值。

---

## 3. 改进建议

> 每条建议均可独立派发给一个执行 Agent。执行前先按 `software-improvement` 确认测试基线全绿；
> 每完成一个改动点立即跑测试，全绿再进入下一点。

### S-01 修复 CLI 参数被子命令默认值覆盖（对应 Q-29）
- **做法**：删除 `run.py:185-194` 的顶层重复参数；把 `main()` 里的 parser 构造抽成模块级 `build_parser()` 函数
  （便于测试直接调用），将公共参数注册到 parent parser（`argparse.ArgumentParser(add_help=False)`），
  用 `parents=[common]` 传给 `reseed` / `schedule` 子解析器；顶层只保留子命令分派与 `SEEDHOUND_MODE` 逻辑。
  若必须保留无子命令写法，则在 `parse_args()` 后显式合并：顶层已为真的开关不得被 `False` 覆盖。
- **文件清单**：`run.py`；`README.md`（运行章节参数表，如需同步）；`tests/test_core.py`（新增用例）。
- **验证方式**：`from run import build_parser`，断言
  `build_parser().parse_args(['--dry-run','reseed']).dry_run is True`；再断言
  `--no-feishu reseed`、`--site A reseed`、`--mode jackett reseed` 的对应字段同样生效，
  且 `parse_args(['reseed'])` 各开关仍为默认 `False`；把上述断言写成单元测试；`python -m pytest tests/ -q` 全绿。

### S-02 修复双下载器启动崩溃（对应 Q-27、Q-18）
- **做法**：`seeder.py:124` 把 `self._config.downloader_config` 改为 `self._config.destination_downloader_config`；
  `start()` 里双下载器分支的日志同时打印 destination 而非未解析的 `dl_config`。
  在 `README.md` 补一句「主下载器取 `destination`，`source` 仅提供种子库」。
- **文件清单**：`src/engine/seeder.py`；`README.md:336-351`；新增测试文件（或 `tests/test_core.py`）。
- **验证方式**：构造 `{"source":{...},"destination":{...}}` 调用 `create_downloader(cfg.destination_downloader_config)`
  断言不再抛 `KeyError` 且 `host` 取自 destination；用 mock downloader 跑 `ReseedEngine.start()` 断言两个连接都被创建；全量测试全绿。

### S-03 对齐 Transmission 下载器接口并让异常可见（对应 Q-28、Q-24、Q-32）
- **做法**：把 `TransmissionDownloader.add_torrent` 签名改成与 `DownloaderBase` 完全一致
  （`download_url=None, torrent_files=None, save_path="", skip_hash_check=True, paused=True, tag=""`），
  内部按 `torrent_files` 优先处理 bytes，否则用 URL；处理或显式忽略 `skip_hash_check` / `tag`。
  同时在 `seeder.py:713` 把 `asyncio.gather(..., return_exceptions=True)` 的结果逐个检查，
  对 `Exception` 记录日志并计入 `failed_count`；`seeder.py:459` 的站点查询异常同理写入 `failed_sites`。
- **文件清单**：`src/engine/downloader.py`；`src/engine/seeder.py`；`tests/`（新增 TR 与异常统计用例）。
- **验证方式**：`inspect.signature` 断言两个 `add_torrent` 一致；以 `torrent_files=b"..."` 调用 TR 实现断言不抛 `TypeError`；
  构造一个会抛异常的 fake downloader，跑 `_phase_add_torrents` 断言 `failed_count` 增加且日志有记录；全量测试全绿。

### S-04 实现 `run_on_start` 语义（对应 Q-05、Q-30）
- **做法**：`scheduler.py:115` 前，当 `run_on_start=False` 且 `self._cycle == 0` 时先 `await self._wait_with_stop(interval)`，
  收到退出信号则直接返回；保持 `startup_delay` 与 `run_on_start` 的叠加语义并在日志中说明。
- **文件清单**：`src/scheduler.py`；`tests/`（新增调度器用例）。
- **验证方式**：用 mock 引擎 + 极小 `interval_minutes`，断言 `run_on_start=False` 时首轮执行时间晚于一个间隔、
  `True` 时立即执行；断言等待期间收到停止信号可退出；全量测试全绿。

### S-05 建立可执行的测试与 lint 门禁（对应 Q-46、Q-53、Q-54、Q-52、Q-20）
- **做法**：新增 `requirements-dev.txt`（`pytest`、`pytest-asyncio`、`flake8`，版本固定下限）；
  新增 `pytest.ini` 配置 `asyncio_mode = auto` 与 `testpaths = tests`；
  新增 `.github/workflows/ci.yml`，在 `push` / `pull_request` 上跑 `pip install -r requirements.txt -r requirements-dev.txt`
  → `python -m flake8 src tests run.py` → `python -m pytest tests/ -q`；
  在 `README.md` 与 `AGENTS.md` 补测试前置条件说明。
- **文件清单**：`requirements-dev.txt`（新增）、`pytest.ini`（新增）、`.github/workflows/ci.yml`（新增）、
  `README.md`、`AGENTS.md`。
- **验证方式**：在干净虚拟环境执行上述三条命令全部通过；CI 工作流在 PR 上实际跑绿一次；
  `flake8` 报 0 错误（现有 `# noqa: E402` 可保留）。

### S-06 解耦测试与本地私密配置（对应 Q-45、Q-51、Q-50）
- **做法**：`tests/test_core.py:84-91` 与 `tests/test_integration.py:96-105` 改为用 `tmp_path` 生成临时
  `config.yaml` / `sites.yaml`，断言从临时值读取；删除对 `concurrency==20` 等本地值的依赖。
  `test_integration.py` 的数据库路径改用 `tmp_path`。删除三个模块的 `if __name__ == "__main__"` 手工块与 `print("PASS")`，
  统一由 pytest 收集。
- **文件清单**：`tests/test_core.py`、`tests/test_integration.py`、`tests/test_report.py`。
- **验证方式**：把 `config.yaml` / `sites.yaml` 临时改名（不删除），`python -m pytest tests/ -q` 应仍全绿；
  恢复文件名；检查 `data/` 下无残留 `test_*.db`。

### S-07 统一配置 schema、默认值单一来源与字段命名（对应 Q-04、Q-15、Q-16、Q-23）
- **做法**：在 `src/storage/config.py` 集中定义 `DEFAULTS` 字典（含 `api_timeout` / `download_timeout` / `retry_*` /
  `download_interval` / `log.file` 等），所有属性与调用方从该字典取值，消除 `seeder.py:77-89`、
  `client.py:133-141`、`jackett.py:93-98`、`run.py:93` 各自硬编码的默认值。
  `sites` 属性同时兼容 `enable` 与 `enabled`（优先 `enable`，与 CHANGELOG 一致），并对未知字段给出告警。
  统一 `cookiecloud.enable` → `enabled`（保留旧键读取兼容），或明确在文档中标注例外。
- **文件清单**：`src/storage/config.py`、`src/engine/seeder.py`、`src/network/client.py`、
  `src/engine/jackett.py`、`run.py`、`config.example.yaml`、`sites.example.yaml`、`README.md:299-375`。
- **验证方式**：新增用例断言 `enable: false` 与 `enabled: false` 都能跳过站点；
  断言不提供某字段时各模块拿到的默认值完全一致；用本地 `config.yaml` 跑一次 `--dry-run` 比对日志中的生效参数；
  全量测试全绿。

### S-08 文档一致性批量修订（对应 Q-01、Q-02、Q-03、Q-06、Q-07、Q-08、Q-09、Q-10、Q-11、Q-12、Q-13、Q-19、Q-59）
- **做法**（逐项，改动仅限文档）：
  1. 删除或改写 `README.md:93-95`「硬编码零配置原则」，改为如实描述「配置以 YAML 为主，CLI / 环境变量仅用于运行模式与临时覆盖」。
  2. 统一 Python 版本为 Dockerfile 实际值（或把 Dockerfile 升到 AGENTS.md 声明的版本，二选一并写进 CHANGELOG），
     改写「仅需 aiohttp、SQLite」为真实依赖列表。
  3. 用 `config.example.yaml` 的实际内容替换 `README.md:309-365` 的配置示例，补齐 `mode` / `scheduler` / `cookiecloud`。
  4. 「递归遍历」改为「扫描目录当前层」或实现 `rglob`（若选实现则归入架构分支，不与本条混做）。
  5. 删除「连接池复用」表述或说明 `force_close=True` 的取舍。
  6. 状态灯与排序描述对齐 `generator.py` / `feishu.py`，或先按 S-12 统一实现后再改文档。
  7. 补全目录结构树；修正「飞书仪卡片」；删除 `:637` 末段冗余话术；删除 Transmission「可选依赖」段；
     补 `workflow_dispatch` 触发说明与 tag 规则说明。
  8. `CHANGELOG.md:76` 的历史 GHCR 地址标注为历史值或去历史化。
  9. `README.md:109` 与 `SeedHound.bat` 的描述在 S-09 完成后同步。
- **文件清单**：`README.md`、`AGENTS.md`、`CHANGELOG.md`、`config.example.yaml`、`sites.example.yaml`。
- **验证方式**：逐条比对文档示例字段与 `config.example.yaml` / `Config` 默认值一致；
  全文搜索 `3.9+` / `enable:` / `递归` / `连接池复用` / `reseed-puppy/seedhound` 确认无残留；
  按 README「快速开始」在干净环境实际走一遍配置复制与 `--dry-run --help`。

### S-09 加固 Windows 一键启动脚本（对应 Q-11、Q-55）
- **做法**：`SeedHound.bat` 增加 `@echo off`、`cd /d "%~dp0"`、检查 `python` 可用性、
  `python run.py %*`、按 `%ERRORLEVEL%` 输出失败提示、`pause`（或仅在失败时暂停）；
  README 明确该脚本继承 `config.yaml` 的 `scheduler.enabled`，需要一次性辅种时如何退出/切换。
- **文件清单**：`SeedHound.bat`、`README.md:109`。
- **验证方式**：在资源管理器双击，确认窗口保持、能看到启动日志；故意把 `config.yaml` 临时改名，
  确认脚本给出可读错误而非一闪而过；`python` 不在 PATH 时给出提示。

### S-10 修正 GHCR latest 标签策略（对应 Q-10、Q-56）
- **做法**：删除 `.github/workflows/docker.yml:49` 的 `type=raw,value=latest`，依赖 `flavor: latest=auto`
  在 tag / semver 事件自动生成 `latest`；或改为 `type=raw,value=latest,enable=${{ startsWith(github.ref, 'refs/tags/v') }}`。
  去掉 `type=ref,event=tag` 与 `type=semver,pattern={{version}}` 的重复，保留一套并同步 README 标签表。
  **不要改动 `IMAGE_NAME: ${{ github.repository }}`**（见 2.1 已排除假设）。
- **文件清单**：`.github/workflows/docker.yml`、`README.md:206-213,236`。
- **验证方式**：手动 `workflow_dispatch` 一次，确认未推送 `latest`；打一个 `v*` tag 确认生成 `latest` 与版本标签且无重复；
  在 GHCR 页面核对标签列表。

### S-11 实现飞书签名或移除该配置项（对应 Q-31）
- **做法**：二选一。(A) 在 `FeishuNotifier._send` 里按飞书自定义机器人协议，当 `secret` 非空时生成
  `timestamp` 与 `sign`（HMAC-SHA256 后 base64），写入 payload；(B) 若项目不需要签名，删除 `secret` 参数、
  模板字段与 README 说明，避免承诺不存在的能力。建议 (A)。
- **文件清单**：`src/notify/feishu.py`、`config.example.yaml:65`、`README.md:75-82`、`tests/test_report.py`（新增用例）。
- **验证方式**：用固定 `secret` 与固定时间戳比对官方算法给出的 `sign`；断言 `secret=""` 时 payload 不含签名字段；
  全量测试全绿。

### S-12 收敛重复实现（对应 Q-22、Q-25、Q-26，属「架构」分支，需单独排期）
- **做法**：把 `cmd_reseed` / `_run_once` 的单轮流程抽成 `run_once(config, args)` 单一实现，两处调用；
  把报告站点表的行构造与排序抽成共享函数，供 `generator.py` 与 `feishu.py` 复用；
  把 `RUN_MARKER` / `LOG_MARKER` 合并为 `src/utils/logger.py` 导出的单一常量，`weekly_report.py` 引用之。
- **文件清单**：`run.py`、`src/scheduler.py`、`src/report/generator.py`、`src/notify/feishu.py`、
  `src/utils/logger.py`、`scripts/weekly_report.py`、`tests/test_report.py`。
- **验证方式**：一次性 `reseed` 与 `schedule` 首轮输出逐字节比对（除时间戳）；文本报告与飞书卡片站点表顺序一致；
  周报在修改日志文案后仍能解析；全量测试全绿。

### S-13 补齐测试覆盖与严谨性（对应 Q-43、Q-44、Q-47、Q-48、Q-49，属「测试」分支，需单独排期）
- **做法**：按零覆盖清单补用例，优先级：`run.py` CLI 参数组合 → 双下载器 / TR 添加 → 调度器 `run_on_start`
  → 三层过滤与错误累计跳过 → `client.py` 的限流 / 错误码 → `jackett.py` 尺寸过滤与缓存
  → `cookiecloud.py` 解密与域名匹配 → `weekly_report.py` 解析与 `--apply`。
  为 `info_hash` / `pieces_hash` 引入外部 golden 值（已知 `.torrent` 的固定哈希），替代自证式断言；
  把弱断言换成结构化比对；补边界 / 异常用例。
- **文件清单**：`tests/` 下新增或扩充 3 个模块；可能需要 `tests/fixtures/`（新增，放不含凭据的样例 torrent）。
- **验证方式**：`python -m pytest tests/ -q` 全绿；`pytest --cov=src`（如引入 coverage）报告覆盖模块数明显上升；
  至少覆盖 Q-43 列出的全部零覆盖模块各 1 条主路径。

### S-14 代码质量清理（对应 Q-33、Q-34、Q-35、Q-36、Q-37、Q-38、Q-39、Q-40、Q-41，属「代码质量」分支）
- **做法**：为 `reseed_history` 加索引与 `(pieces_hash, site_name, torrent_id)` 唯一约束（配合 `INSERT OR IGNORE`）；
  删除死代码；合并重复关键词与冗余正则；`setup_logger` 先清理既有 handler；
  `asyncio.get_event_loop()` 改 `asyncio.get_running_loop()`；`torrent_dir` 为空时显式报错；
  给 `cookie_sync.py:82` 的正则补注释并加用例；修 `Retry-After`、JSON 类型判断、`_is_add_success` 未知类型兜底、
  周报流式读取；移除 `create_downloader` 的默认密码。
- **文件清单**：`src/storage/cache.py`、`src/notify/feishu.py`、`src/engine/downloader.py`、
  `src/network/client.py`、`src/utils/logger.py`、`src/scheduler.py`、`src/engine/parser.py`、
  `src/cookie_sync.py`、`scripts/weekly_report.py`、`src/engine/seeder.py`。
- **验证方式**：每个改动点后单独跑 `python -m pytest tests/ -q`；缓存改动额外用 1 万条记录跑一次
  `get_reseeded_hashes_by_site` 并对比耗时；周报改动用 `--clean --keep-days 30`（不带 `--apply`）确认预览行数不变。

### S-15 工程细节收尾（对应 Q-17、Q-57、Q-58、Q-60）
- **做法**：补错误码与站点跳过策略文档；`.dockerignore` 删除无意义的 `!README.md` 并在 README 说明容器内不含
  `scripts/`；`.flake8` 增补 `exclude` / `per-file-ignores = tests/*:E402` 后移除测试文件里的 `# noqa: E402`；
  把本地备份文件移出工作区或保持现状（已 gitignore，可不处理）。
- **文件清单**：`README.md`、`.dockerignore`、`.flake8`、`tests/test_core.py`、`tests/test_integration.py`、`tests/test_report.py`。
- **验证方式**：`python -m flake8 src tests run.py` 0 错误；`docker build` 后 `docker run --rm <image> ls /app` 确认目录与文档一致。

---

## 4. 优先级与并行性

### 4.1 优先级排序

| 优先级 | 建议 | 对应问题 | 理由 |
|---|---|---|---|
| **P0** | S-01 | Q-29 | 演练模式静默失效会真实写入下载器，数据风险最高 |
| **P0** | S-02 | Q-27 / Q-18 | 文档承诺的双下载器模式启动即崩 |
| **P0** | S-03 | Q-28 / Q-24 / Q-32 | TR 路径静默失败且统计失真 |
| **P0** | S-04 | Q-05 / Q-30 | 定时错峰配置无效 |
| **P0** | S-05 + S-06 | Q-45/46/52/53/54/20 | 无门禁则以上修复无法验证与防回归；两条建议需连做 |
| **P1** | S-07 | Q-04 / Q-15 / Q-16 / Q-23 | 配置默认值与字段命名混乱，是多个缺陷的共同土壤 |
| **P1** | S-08 | Q-01/02/03/06/07/08/09/10/12/13/19/59 | 文档与实现大面积不一致 |
| **P1** | S-09 / S-10 / S-11 | Q-11/55, Q-10/56, Q-31 | Windows 体验、发布标签、飞书签名 |
| **P1** | S-12 | Q-22 / Q-25 / Q-26 | 重复实现导致改一漏一 |
| **P1** | S-13 | Q-43/44/47/48/49 | 补齐回归保护 |
| **P2** | S-14 / S-15 | Q-33~41, Q-17/57/58/60 | 局部质量与工程细节 |

### 4.2 并行性标注

**组 A（可独立并行，涉及文件两两无重叠）** — 建议在 P0 修复合并后同时派发：

| 建议 | 独占文件 |
|---|---|
| S-09 | `SeedHound.bat` |
| S-10 | `.github/workflows/docker.yml` |
| S-11 | `src/notify/feishu.py` |
| S-15 的纯配置部分 | `.flake8`、`.dockerignore` |

> 注意：S-09 / S-10 会顺带改 `README.md`，S-05 会改 `README.md` 与 `AGENTS.md`，S-08 与 S-15 的文档部分也会改 README。
> 若并行执行，**README / AGENTS.md 的修改必须串行合并**（或把全部文档改动拆出、统一交给 S-08）。
> 上表「可并行」仅指各自的核心文件无重叠；S-15 只有 `.flake8` / `.dockerignore` 两处可参与并行，
> 其 README 与 `tests/` 改动必须留到文档分支串行处理。

**组 B（可并行，依赖见表内）**：

| 建议 | 文件 | 依赖 |
|---|---|---|
| S-04 | `src/scheduler.py` | 与 S-01/S-03 无文件重叠，允许并行；§4.3 为稳妥仍安排在第 1 轮末尾串行执行 |
| S-13（新增测试文件部分） | `tests/` 新增文件 | 依赖 S-01/02/03/04 的行为定稿 |
| S-05（新增文件部分） | `requirements-dev.txt` / `pytest.ini` / `.github/workflows/ci.yml` | 无 |

**必须串行的条目**：

1. `S-05 → S-06 → S-13`：先建立门禁与依赖声明，再解耦测试配置，最后补用例；顺序颠倒会导致测试仍跑不起来。
2. `S-01 / S-02 / S-03 → S-13`：测试用例必须针对修复后的行为编写。
3. `S-02 / S-03 / S-07 / S-12 / S-14` 都涉及 `src/engine/seeder.py` 或 `src/engine/downloader.py`，
   **这五条彼此不可并行**，建议按 S-02 → S-03 → S-07 → S-12 → S-14 顺序推进，每条完成后全量测试全绿再进下一条。
4. `S-08` 依赖 S-07（默认值定稿）与 S-10（tag 规则定稿）与 S-09（bat 描述定稿），建议放在 P1 末尾统一执行。
5. `S-12` 属 `software-improvement` 的「架构」分支，`S-14` 属「代码质量」分支，`S-13` 属「测试」分支，
   `S-08` 属「文档」分支 —— 按该技能纪律**四次任务分别执行，不混做**。

### 4.3 派发建议（可直接照做）

1. 第 1 轮（功能修复，按代码质量分支纪律串行执行）：S-01 → S-02 → S-03 → S-04，每步跑测试。
2. 第 2 轮（工程/测试基线）：S-05 → S-06，确认干净环境 17 项全绿、`flake8` 0 错误。
3. 第 3 轮（并行 4 个 Agent）：S-09 / S-10 / S-11 / S-15 的纯配置部分；
   其中 S-09 / S-10 / S-15 的 README 改动**不在本轮落地**，统一登记后交由第 6 轮 S-08。
4. 第 4 轮（测试分支）：S-13，补零覆盖模块与 golden 值。
5. 第 5 轮（架构分支，串行）：S-07 → S-12。
6. 第 6 轮（文档分支）：S-08 统一收口所有文档（含第 3 轮登记的 README 改动）与 S-15 的文档部分。
7. 第 7 轮（代码质量分支）：S-14 收尾；与第 6 轮分属不同分支，不得合并为同一任务。

---

## 5. 自查

- 四个必需块齐全：现状描述（§1）、问题清单（§2）、改进建议（§3）、优先级与并行性（§4）。
- 七个审计维度均有结论，其中维度 1 含一条明确的「正面结论」（Q-14），无遗漏维度。
- 每条问题均含位置 / 描述 / 严重程度 / 影响范围；标为「高」的 11 条（Q-05、Q-15、Q-24、Q-27、Q-28、Q-29、Q-30、Q-43、Q-45、Q-46、Q-53）均写明了具体后果。
- 每条建议均含具体做法 / 文件清单 / 验证方式；文件路径具体到行号或模块。
- 所有「已实测」结论标注了复现方式；推断性内容集中在 2.1「已排除假设」与 2.2「局限」，不冒充事实。
- 未记录任何真实凭据值；未修改任何代码或配置。

> 说明：§1–§5 是 **审计阶段** 的原始记录，按 `project-audit` 纪律保持原样（诊断、不改代码）。
> 下节 §6 是审计结束后按 `software-improvement` 分分支推进的 **修复与核销** 记录。

---

## 6. 修复核销（2026-11-07 收尾）

**结论：Q-01 ~ Q-60 共 60 条，全部核销，未核销 0 条。**

### 6.1 核销方法

不采用「人工勾选」，而是编写逐条断言的核销脚本，以**动态行为验证优先**：

- 动态复现类：`Config.sites` 的 `enable` / `enabled` 兼容与优先级、`parse_args` 的 5 组参数组合、
  `inspect.signature` 比对三个 `add_torrent` 契约、`FeishuNotifier._gen_sign` 与独立 `hmac` 复算比对、
  `Config._validate` 未知字段告警、`hashlib.sha1` 独立复核外部 golden 哈希、
  `SiteClient` / `JackettClient` 默认参数与 `DEFAULTS` 逐值比对。
- 静态验证类：文档与 `DEFAULTS` / 模板 / 工作流 / `.flake8` / `.dockerignore` 的一致性，
  死代码残留、`get_event_loop()` 残留、手工 `__main__` 块、`data/test_*.db` 残留等。
- 执行门禁类：`pytest`、`flake8`、CI workflow、文档一致性脚本。

脚本执行结果：`审计问题总数: 60   已核销: 60   未核销: 0   缺失: 0`。

### 6.2 按维度汇总

| 维度 | 编号区间 | 条数 | 处置 |
|---|---|---|---|
| 1 文档一致性 | Q-01 ~ Q-14 | 14 | 13 条修复；Q-14 为审计正面结论，核实属实后保持现状 |
| 2 文档缺漏 | Q-15 ~ Q-20 | 6 | 全部修复（`DEFAULTS` 单一来源、字段说明、错误码清单、双下载器语义、tag 规则、门禁前置条件） |
| 3 架构优化 | Q-21 ~ Q-26 | 6 | 全部修复（模式回退告警、`src/runner.py` 收敛、schema 告警、契约对齐、`src/report/site_table.py` 收敛、`RUN_MARKER` 单一来源） |
| 4 代码写法 | Q-27 ~ Q-41 | 15 | 全部修复，含 4 个已实测 P0 缺陷（Q-27 / Q-28 / Q-29 / Q-30） |
| 5 测试覆盖率 | Q-42 ~ Q-44 | 3 | 全部修复：10 个测试模块 / 94 个测试函数 / 135 项用例（参数化后） |
| 6 测试严谨性 | Q-45 ~ Q-51 | 7 | 全部修复：`tmp_path` 解耦、外部 golden、边界异常用例、删除手工块 |
| 7 工程规范 | Q-52 ~ Q-60 | 9 | 8 条修复；Q-60 核实为本地工作文件、`.gitignore:22,23` 已覆盖，不入库故无需改动 |

### 6.3 三条「不改动」的显式结论

1. **Q-14**：qBittorrent 5.3.0 的 `torrents/add` 返回体、`seedMode` / `stoppedUP` 改名、`is_paused=False` 折叠、UTF-8 日志重配置等描述与实现一致 —— 保持现状。
2. **Q-60**：`sites.backup.yaml` / `sites.yaml.bak` 经 `git check-ignore -v` 实测由 `.gitignore:22`（`sites.backup.yaml`）与 `.gitignore:23`（`*.bak`）覆盖，属含真实凭据的本地备份，**保留用户数据不动**；如需物理清理须由用户显式确认。
3. **§2.1 已排除假设**：`.github/workflows/docker.yml` 的 `IMAGE_NAME: ${{ github.repository }}` 仍保留原样 —— `docker/metadata-action` 自动小写化镜像名，改动反而引入错误。

### 6.4 最终回归

| 门禁 | 命令 | 结果 |
|---|---|---|
| 全量测试 | `python -m pytest tests/ -q` | `135 passed in 3.01s` |
| 静态检查 | `python -m flake8 src tests run.py scripts` | 0 错误 |
| 文档一致性 | 逐项断言脚本（60+ 项检查） | `S-08 DOC VALIDATION ALL PASS` |
| 问题核销 | Q-01 ~ Q-60 逐条断言 | `60 / 60 CLOSED` |

基线对比：修复前 `17 passed`（且干净环境因缺依赖无法执行）→ 修复后 `135 passed`。

### 6.5 变更清单

- **修改 26 个文件**：`README.md`、`AGENTS.md`、`CHANGELOG.md`、`config.example.yaml`、`sites.example.yaml`、
  `run.py`、`SeedHound.bat`、`src/storage/config.py`、`src/engine/seeder.py`、`src/engine/downloader.py`、
  `src/engine/parser.py`、`src/engine/jackett.py`、`src/network/client.py`、`src/scheduler.py`、
  `src/storage/cache.py`、`src/notify/feishu.py`、`src/report/generator.py`、`src/utils/logger.py`、
  `src/cookie_sync.py`、`scripts/weekly_report.py`、`.flake8`、`.dockerignore`、
  `.github/workflows/docker.yml`、`tests/test_core.py`、`tests/test_integration.py`、`tests/test_report.py`。
- **新增 15 个文件**：`src/runner.py`、`src/report/site_table.py`、`tests/conftest.py`、`tests/helpers.py`、
  `tests/test_cli.py`、`tests/test_config.py`、`tests/test_cookie.py`、`tests/test_downloader.py`、
  `tests/test_engine.py`、`tests/test_network.py`、`tests/test_scheduler.py`、
  `pytest.ini`、`requirements-dev.txt`、`.github/workflows/ci.yml`、`AUDIT-REPORT-2026-11-07.md`。

> 本轮改动已按用户确认走「提交 → Tag → 推送」流程发布为 `v1.1.0`。
> 推送 `v*` 标签会触发 `.github/workflows/docker.yml` 构建并推送 GHCR 镜像
> （`1.1.0` / `1.1` / `latest`）。
