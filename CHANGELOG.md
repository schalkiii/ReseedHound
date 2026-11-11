# Changelog

## 2026-11-12 — 失效站点禁用 + savept 死亡名单对照 + 凭据失效提醒 + 重试策略优化

### 配置：禁用 8 个失效站点
`昆仑`、`传道院`、`raingfh`、`星陨阁`、`伞`、`a4apt`、`Moment`、`cdfile` 标记
`enabled: false`（保留条目便于恢复）。依据 13 轮日志全 0 成功 + 只读探测定性
（DNS 解析失败 / 自签名证书 / API 404-405 持续）。

### 新增：`check-dead` 对照 savept.icu 死亡名单并推荐禁用
- 新模块 `src/network/savept.py`：拉取 `savept.icu/api/history/dead`（分页 JSON），
  与本地 `sites.yaml` 启用中的站点对照，按域名 → 注册域 → 站点名/别名三级匹配。
- 匹配置信度：域名/注册域一致为 `high`（可直接禁用）；仅名称/别名一致为 `medium`
  （多为站点换域名，建议人工核对）。
- CLI：`python run.py check-dead` **只打印建议不改配置**；确认后加 `--apply` 才写入
  `enabled: false`。可用 `--savept-url` 覆盖数据源，或在 `config.yaml` 的 `savept` 块配置。
- `load_local_sites()` 只取 name/url、不触碰任何凭据；`disable_sites_in_yaml()` 用文本行插入
  保留注释与顺序。
- 实测：对照 67 个启用站点命中 3 个（52movie / ptlao / 铂金学院，均 high 置信）。

### 新增：凭据失效提醒推送
- 新错误集合 `CREDENTIAL_ERRORS`（`http_401`/`http_403`/`html_permission_denied`/
  `html_forbidden`/`html_auth_required`/`auth_redirect`），与网络抖动区分。
- 引擎记录 `ReseedStats.credential_failures`（站点 → 首个凭据类原因）；
  一轮结束若非空，飞书新增独立红色卡片 `send_credential_alert()`，列出站点、原因
  与更新指引（重拿 passkey 或 `sync-cookies`）。

### 优化：重试策略与日志（O1/O2/O3）
- **O1 硬失败不重试**：404/405/410/401/403 等状态码，以及 `getaddrinfo failed`（域名已死）、
  `certificate verify failed`（证书已废）类异常，直接短路返回，不再按 `retry_count` 白试。
  429 限流与 5xx/超时仍按原策略重试。
- **O2 HTTP 200 但返回 HTML 单独归类**：响应非 JSON（表单错误页/登录跳转）时记为
  `html_invalid_response` 并不重试，不再混进网络错误里反复重试。
- **O3 日志区分「批次」与「重试」**：失败日志带上 `[第 X/Y 批]`，`共尝试 N 次`只表示单请求
  内部尝试次数。示例：`请求失败 https://... [第 1/43 批] (HTTP 404，硬失败不重试)`。
  HDArea 通道批次同样标注。

### 测试与门禁
- 新增 `tests/test_savept.py`（归一化/解析/匹配/配置写入）、`tests/test_retry_policy.py`
  （硬失败不重试、HTML 归类、批次号日志）、`tests/test_credential.py`（错误集合与飞书卡片）。
- 全量回归：`pytest tests/ -q` → **189 passed**（v1.2.0 的 156 项 + 新增 33 项）；
  `flake8 src tests run.py scripts` → 0 错误。
- 本次改动随 **`v1.3.0`** 发布；推送 `v*` 标签会触发 `.github/workflows/docker.yml`
  构建并推送 GHCR 镜像（`1.3.0` / `1.3` / `latest`）。

## 2026-11-11 — HDArea REST API info_hash 辅种通道

依据 HDArea `api_docs.php` §2.3 / §4 新增第三条辅种通道：按站点自动分流，
`POST /api/v1/torrents/hashes` 一次最多传 200 个本地做种的 40 位 `info_hash`，
毫秒级拿到站内种子 ID 与带 `dl_token` 的短效下载链接，零网页爬虫开销。

### 新增
- `src/network/hdarea.py`：`is_hdarea_site()` / `hashes_url()` / `split_batches()` /
  `parse_hashes_response()` / `HdareaMatcher`。统一 envelope 校验、非法哈希过滤、
  200 条硬上限分批；`X-API-Key` 经 `SiteClient.post_json(..., headers=...)` 传入，不污染通用请求。
  读取限流 60 次/分，用全局节流锁保证相邻批次间隔，`concurrency` 只限制在途响应。
- `src/engine/seeder.py`：查询阶段建立 `info_hash ↔ pieces_hash` 双向索引，`api_type: hdarea`
  的站点走 info_hash 通道，其余站点继续走 `pieces_hash`；命中结果回落统一的片段级统计口径。
  添加阶段优先用接口返回的 `download_url`，短效链接返回 `http_403` 时用 `?apikey=` 直链兜底一次。
- `src/storage/config.py`：`DEFAULTS["hdarea"]`（`request_interval=1.1` / `concurrency=2`）、
  `KNOWN_KEYS["hdarea"]`、`KNOWN_SITE_KEYS` 增补 `api_type` / `api_key`；`Config.sites`
  改为「有 `passkey` 或有 `api_key`」才保留站点，避免只有 API Key 的站点被静默丢弃。
- `config.example.yaml` / `sites.example.yaml`：HDArea 配置块与站点模板；README / AGENTS 新增
  通道专节、字段说明与约定，并同步测试数量与目录树。

### 实测（已登录浏览器上下文 + Python 直连）
- API Key 有效、scope 为 `read/download/upload/user`；无命中时返回 `data={"count":0,"items":[]}`。
- 用本地 `BT_backup` 前 200 个真实种子回查，命中 4 个（2%）——印证 `info_hash` 是
  `pieces_hash` 匹配的子集：各站改写 info 字典，只有 info 完全一致才命中，召回率天然低于通用通道。
- 短效 `download_url` 以 `credentials:'omit'`（不带 Cookie）请求返回
  `HTTP 200 / application/x-bittorrent / 94008 bytes`，无需 passkey 或站点 Cookie。
- 205 个真实种子的 `dry-run` E2E 通过：只查询不添加，临时目录内复制种子与配置，未改动本地凭据文件。

### 安全
- `src/utils/logger.py` 的 `redact_secrets()` 从只脱敏 `passkey=` 扩展为
  `passkey=` / `api[_-]?key=` / `dl_token=` / `X-API-Key:` 四类统一打码，仍挂在文件与控制台两个 handler。
- API Key 只从本地 `sites.yaml` 读取（`sites.yaml` 已 gitignore）；代码、模板与文档只用占位符。
  API Key 与 tracker passkey 是两套独立凭据，下载时服务端会动态写入个人 passkey，不混用。

### 测试与门禁
- 新增 `tests/test_hdarea.py` **21 项**：分流判定、URL 推导、分批与非法哈希过滤、envelope 解析、
  `X-API-Key` 发送、批次异常隔离、双通道引擎分流、短效链接优先与 403 兜底、旧站点仍用 `passkey=`、
  四类凭据脱敏。全部用假客户端与 `tmp_path`，不依赖真实站点、不读取本地凭据。
- 全量回归：`pytest tests/ -q` → **156 passed**（v1.1.0 的 135 项 + 新增 21 项）；
  `flake8 src tests run.py scripts` → 0 错误。
- 本次改动随 **`v1.2.0`** 发布；推送 `v*` 标签会触发 `.github/workflows/docker.yml`
  构建并推送 GHCR 镜像（`1.2.0` / `1.2` / `latest`）。

## 2026-11-07 — 全面审计修复与文档一致性收口

依据 `AUDIT-REPORT-2026-11-07.md` 的 60 条问题分阶段修复（功能 → 测试基线 → 架构 → 测试 → 工程/文档）。

### 功能修复
- **CLI 公共开关被子解析器默认值覆盖**：`--dry-run` / `--no-feishu` / `--site` / `--mode` / `--config` / `--sites-path` 改用 `argparse.SUPPRESS` + 解析后补齐，写在子命令前后都生效
- **双下载器启动崩溃**：`ReseedEngine.start()` 改用 `destination_downloader_config`（主下载器 = destination），缺 `host` 时给可读 `ValueError` 而非 `KeyError`
- **Transmission 静默失败**：`add_torrent` 签名与 `DownloaderBase` 完全对齐、支持 bytes payload，显式忽略不支持的参数
- **`run_on_start=false` 无效**：首轮前等待一个完整间隔；修正 `_wait_with_stop` 返回值判断
- **异常被吞**：三处 `gather(return_exceptions=True)` 逐个检查结果，计入 `failed_sites` / `failed_count`
- **飞书签名未实现**：按官方 HMAC-SHA256 + Base64 算法补 `timestamp` / `sign`
- 其他：缓存去重与唯一索引、日志重复 handler、死代码清理、限流规则去重、`Retry-After` HTTP-date 兜底、未知返回类型判定、默认密码移除、周报流式读取

### 架构
- **配置默认值单一来源**：新增 `DEFAULTS`，消除散落 4 处且互相矛盾的硬编码；`enable` / `enabled` 双写法兼容且未知字段启动告警
- **单轮流程收敛**：新增 `src/runner.py`，`cmd_reseed` 与调度器每轮共用
- **报告口径收敛**：新增 `src/report/site_table.py`，文本 / Markdown / 飞书卡片共用四档状态灯、按成功率排序与无匹配站点截断
- **轮次标志收敛**：`RUN_MARKER` 单一常量，周报不再另维护 `LOG_MARKER`

### 测试与门禁
- 新增 `requirements-dev.txt` / `pytest.ini` / `.github/workflows/ci.yml`（Python 3.11 / 3.13 执行 flake8 + pytest）
- 测试由 **17 项扩到 135 项**：全部用 `tmp_path` 生成临时配置，不再读取含真实凭据的本地 `config.yaml` / `sites.yaml`；哈希改用外部 golden 值；补齐 CLI、调度器、双下载器 / TR、网络、CookieCloud、Jackett、周报、引擎各阶段用例

### 工程与文档
- `SeedHound.bat` 加固（切目录 / Python 检查 / 参数透传 / 退出码 / pause）
- GHCR `latest` 仅由 semver tag 的 `flavor=latest=auto` 生成，删除重复版本标签；`IMAGE_NAME` 保持不变
- `.flake8` 增补 `exclude` / `per-file-ignores`；`.dockerignore` 删除无意义例外
- README / AGENTS / 配置模板与实现对齐：Python 版本、配置示例与默认值、目录树、报告规则、标签规则、错误码清单、扫描层级、`force_close=True` 取舍、容器内不含 `scripts/`、测试前置条件

### 核销
- Q-01 ~ Q-60 **全部核销（60/60，未核销 0）**；逐条断言脚本以动态行为验证优先，结论与不改动说明见 `AUDIT-REPORT-2026-11-07.md` §6
- 最终回归：`pytest tests/ -q` → `135 passed`；`flake8 src tests run.py scripts` → 0 错误；文档一致性与问题核销脚本均 ALL PASS
- 三条显式不改动：Q-14（审计正面结论属实）、Q-60（本地备份已被 `.gitignore` 覆盖，保留用户数据）、`IMAGE_NAME`（metadata-action 自动小写化）
- 本次改动随 `v1.1.0` 发布；推送 `v*` 标签会触发 `.github/workflows/docker.yml` 构建并推送 GHCR 镜像（`1.1.0` / `1.1` / `latest`）

## 2026-11-07 — qBittorrent 5.3.0 兼容性修复

### 修复
- **辅种种加进去后一直不做种**：`qbittorrent-api` 会把 `is_paused=False` 折叠成「不发送该参数」
  （合并逻辑是 `is_paused or is_stopped`），qB 于是按全局 `add_stopped_enabled` 把种子加成 `stoppedUP`。
  现在两个参数一起传，并新增 `_ensure_started` 兜底：添加后轮询确认，仍停在停止态就补一次 `resume`
  （2026-11-07 实测一轮：匹配 123、成功 8，8 个种子初始全部 `stoppedUP`，补 resume 后全部转为 `stalledUP`）
- **`auto_start=false` 实际无效**：原逻辑在添加成功后无条件 `resume`，绕过了该开关；现在仅在 `auto_start=true` 时执行
- **报告打印在 GBK/重定向环境崩溃**：报告含 emoji，`print(report)` 抛 `UnicodeEncodeError`
  并连带跳过飞书推送。`setup_logger` 现在把 stdout/stderr 重配置为 UTF-8（不可编码字符降级）
- **添加结果判定不再只认 `"Ok."`**：WebAPI v2.14.0（qB 5.2）起 `torrents/add` 返回 JSON 计数
  （`success_count` / `failure_count` / `pending_count`），旧判定把 5.2/5.3 上每一次成功添加都记成失败，
  且静默无日志。后果链是：`succeeded_count` 恒为 0；本轮不写辅种记录；尤其是「自动开始」只在添加成功分支里执行，
  于是这批种子长期停在暂停态、不做种；下一轮它们会命中「hash 已在客户端」分支被补记成功，但该分支同样不 resume。
  现由 `QbittorrentDownloader._is_add_success` 同时兼容文本与字典两种形态，未受理时打印原始返回值
- **「自动开始」兼容 5.x 状态改名**：`pausedUP` 在 qBittorrent 5.x 更名为 `stoppedUP`，只判旧名会让加进去的
  种子长期停在暂停态；状态集合提取为 `STOPPED_COMPLETE_STATES`（含旧名、新名与 Transmission 的 `completed`）
- **测试断言与现役报告格式对齐**：`test_report.py` 仍断言早已废弃的 `启用站点:      14` 等旧排版（改动前即为红）

### 安全
- **移除代码里的 CookieCloud 凭据**：`run.py` 的 argparse 默认值与 `src/scheduler.py` 的兜底默认值都硬编码了
  真实 uuid / 密码，且早已进入公开 git 历史。现改为只从本地 `config.yaml` 的 `cookiecloud` 块读取，
  缺失时 CLI 报错退出、调度器告警跳过；**这两个值必须到 CookieCloud 侧轮换**，删代码回收不了历史泄露
- **日志统一脱敏 passkey**：下载链接形如 `.../download.php?id=1&passkey=xxx`，此前会随「下载失败/请求失败」
  日志落盘（历史日志中 5859 行）。现在在 `setup_logger` 的两个 handler 上挂 `_RedactSecretsFilter`，
  把 README 的「passkey 不出现在日志中」从约定变成不变量

### 依赖
- `qbittorrent-api` 下限由 `>=2024.2.59` 提到 `>=2026.8.1`：该版本起对 `torrents/add` 同时发送
  `skip_checking` 与 `seedMode`。5.3.0 只认 `seedMode`，且对不认识的参数静默忽略，更早的版本会让
  `skip_hash_check` 悄悄失效（种子照加，但会重新完整校验）

### 新增
- 单元测试扩充到 **17 项**（新增添加结果判定、日志脱敏、自动开始补 resume 三组）；全套通过
- `scripts/weekly_report.py` 纳入版本管理：按站点聚合近 N 天辅种指标，并可选清理远期日志（默认 dry-run）
- `SeedHound.bat` 纳入版本管理：Windows 一键启动入口（`python run.py`）
- 历史日志就地脱敏：`logs/reseed.log` 中 53 处 passkey 已改为 `passkey=***`（本地文件，不入库）

## 2026-09-25 — 调度器 CookieCloud 自动同步 + 统计/超时/死种优化

### 新增
- **调度器运行前自动同步 CookieCloud**：`cookiecloud.enable=true` 时，原生调度器在每轮辅种引擎启动前自动执行一次 CookieCloud 同步；同步失败仅告警并继续本轮，不中断辅种
- **可复用同步模块**：抽出 `src/cookie_sync.py`，CLI `sync-cookies` 子命令与调度器共用同一实现（`run.py` 不再重复同步逻辑）
- **死种缓存持久化**：连续 15 天判定为死种的记录持久化到 `cache.db`，启动时加载为已知死种

### 优化 / 修复
- **统计去重**：`add_one` 改为"片段级"累计失败数，仅当某源种子最终没有任何站点成功辅种时才计入 `failed_count` / `site_failed`，重试成功前的失败不再被统计（避免夸大真实失败数）
- **超时跳过阈值**：累计下载超时跳过阈值由 20 次调整为 30 次（`MAX_TIMEOUT_ERRORS`）
- **阶段3静默跳过补全日志**：源种子下载中/离线、已知死种等跳过场景均输出计数与日志，消除诊断盲区

### 配置
- `config.yaml` 新增 `cookiecloud` 配置块：`enable`、`url`、`uuid`、`password`
- `config.yaml` `global.download_timeout` 由 15 调整为 45（配合超时跳过阈值）
- `sites.yaml`：TorrentHub 已 `enable: false`（站点关闭）；cdfile 维护中暂未禁用

## 2026-07-07 — 原生定时运行

### 新增

- **原生定时调度器**：内置 `src/scheduler.py`，无需外部 cron 即可循环运行辅种引擎；每轮重新创建引擎保证状态干净，支持 `SIGINT`/`SIGTERM` 优雅退出（Windows 下自动回退到 `signal` 模块）
- **`schedule` 子命令**：`python run.py schedule` 按 `config.yaml` 中 `scheduler` 配置循环运行
- **配置自动切换**：`scheduler.enabled=true` 时，默认 `reseed` 运行自动切换为定时模式（契合"配置驱动"原则，无需命令行注入）
- **Docker 入口逻辑并入 `run.py`**：移除独立的 `docker-entrypoint.sh`；`run.py` 在无子命令时直接读取 `SEEDHOUND_MODE` 环境变量（`reseed`/`schedule`/`sync-cookies`，默认 `reseed`）决定运行模式，同时修复了 `scheduler.enabled=true` 会错误劫持 `sync-cookies` 的隐患
- **GHCR 发布工作流**：新增 `.github/workflows/docker.yml`，在打 `v*` 标签 / 发布 Release 时通过 GitHub Actions 自动构建并推送镜像到 GHCR（`ghcr.io/<owner>/<repo>`），使用仓库自带 `GITHUB_TOKEN` 鉴权

### 配置

- `config.example.yaml` 新增 `scheduler` 配置块：`enabled`、`interval_minutes`(默认 360)、`startup_delay`(默认 0)、`run_on_start`(默认 true)

### 文档

- README 新增"定时运行（原生调度器）"章节与三种触发方式
- README Docker 部署新增 `SEEDHOUND_MODE` 说明表与常驻定时运行示例
- README Docker 部署新增"从 GHCR 拉取镜像（推荐）"小节：镜像地址、标签说明、docker 与 wslc 的 pull/run 示例、GitHub Actions 自动发布说明（该条目中的 `ghcr.io/reseed-puppy/seedhound` 是当时的历史地址，现已统一为 `ghcr.io/schalkiii/reseedhound`）
- README Docker 部署新增"使用 wslc 部署（微软原生 WSL 容器）"小节：`wslc.exe` 是微软 Build 2026 随 WSL 预览版内置的容器 CLI（语法同 docker、`container.exe` 别名），含安装、构建、运行（`-e SEEDHOUND_MODE=schedule`）、运维命令对照表，以及宿主机下载器用 `host.wsl.localhost` 而非 `127.0.0.1` 的提示
- README 目录结构补充 `src/scheduler.py`

## 2026-06-01 — 过滤精简 + enable 字段修复

### 修复

- **`enable: false` 不生效**：`config.py` 读取 `sites.yaml` 时字段名为 `enabled`，但实际配置文件中使用的是 `enable`，字段名不匹配导致设为 `false` 的站点仍参与辅种。现已兼容两种写法，优先读取 `enable`
- **示例配置缺失 `enable` 字段**：`sites.example.yaml` 补充了 `enable` 字段及其说明

> **修正注记**：以上两条是当时的计划，实际代码长期只读 `enabled`，承诺直到 2026-11-07 审计修复才真正落地。
> 当前实现同时兼容 `enable` / `enabled`（`enable` 优先），模板统一推荐 `enabled` 并保留兼容说明。

### 精简

- **过滤从五层精简为三层**：原下载阶段的 `valid_reseeded` 检查和 `tracker 同站检查` 与查询阶段预过滤完全重复，合并后逻辑更清晰
  - 第一层: tracker 预过滤（查询前）
  - 第二层: reseed_history 预过滤（查询前，新增 qB 存在性校验）
  - 第三层: info_hash 去重（下载后兜底）
- 移除 `_phase_add_torrents` 中的 `get_reseeded_combos` 调用和 `_qb_site_seeding` 属性

### 修复

- **reseed_history 预过滤加入 qB 校验**：原先按 (pieces_hash, 站点) 过滤时未检查种子是否仍在 qB 中，导致已删除的种子被错误跳过。现已校验 `pieces_hash in self._qb_pieces_hashes`

## 2026-05-31 — 下载重试机制修复 + Cookie Cloud 同步

### 修复

- **下载重试机制**：`try_download` 方法原先直接调用 `get_bytes` 无重试，导致超时种子直接失败。现改为根据 `retry_count`/`retry_delay` 配置进行指数退避重试，大幅提升下载成功率
- **重复下载浪费**：下载后发现 info_hash 已在 qB 中存在时，原先仅跳过不缓存，导致下次运行重复下载。现同步写入 `reseed_history`，下次运行直接跳过下载
- **查询阶段过滤**：`_phase_query_sites` 在向各站点发送 API 查询前，先通过 tracker 匹配和 reseed_history 过滤掉已知已辅种的 pieces_hash，从源头减少 API 请求量和不必要的匹配结果
- **Cookie 格式修正**：`sites.yaml` 中 cookie 字段改为单行双引号包裹，避免 YAML 多行解析问题
- **日志乱码修复**：同步 Cookie 日志中的 `✓`/`✗` 符号替换为 `[OK]`/`[--]` 文本标识

### 新增

- **Cookie Cloud 同步**：`run.py sync-cookies` 命令，从自部署的 Cookie Cloud 服务端自动拉取加密 Cookie 数据，按域名匹配后写入 `sites.yaml`
  - 支持 AES-256-CBC 解密（EVP_BytesToKey 密钥派生）
  - 新增依赖 `pycryptodome>=3.20.0`

### 配置

- `api_timeout` 从 10 秒增至 15 秒（config.example.yaml），适应慢响应站点
- `download_timeout` 从 60 秒降至 15 秒，torrent 文件通常 KB 级，15s 足够
- 下载并发信号量从 10 提升至 20，充分利用 I/O 带宽

### 性能

- 日志分析发现下载阶段占耗时 90%+，根因为 60s 超时 + 并发度 10 偏低
- 优化后预估：下载阶段从 5h+ 降至 ~1.5h（缩短 70%）

### 文档

- **README 辅种方法学重构**：新增"五层过滤机制"架构图，完整描述查询前/下载前/下载后的逐层拦截策略

## 2026-05-30 — 跨下载器辅种 + Jackett 策略重构

### 新增

- **双下载器模式**：支持 `source→destination` 跨下载器辅种，源下载器提供种子库，目标下载器接收辅种结果
  - 配置格式：`downloader.source` + `downloader.destination`，缺省时自动回退为单下载器模式
  - 支持跨类型（qB→TR、TR→qB、qB→qB 等）
  - `source_downloader_config` / `destination_downloader_config` 属性自动解析
- **Jackett 下载缓存**：pieces_hash 不匹配的 torrent URL 写入 `data/jackett_dl_cache.json`，后续运行跳过重复下载
- `--mode` 命令行参数：支持 `pieces_hash` / `jackett` / `both` 三种模式

### 变更

- **Jackett 尺寸过滤**：从百分比容差（±10%）改为绝对精度（≥1GB 时 ±0.01GB, ≥1MB 时 ±0.01MB）
- **qBittorrent 添加策略**：Jackett 模式下固定 `skip_hash_check=false`, `paused=true`
- **Jackett 日志增强**：每轮搜索显示搜索结果数 + 尺寸匹配数，进度行新增下载量与尺寸命中统计
- 移除无效的 `torznab:info_hash` 属性匹配逻辑
- `_phase_scan()` 改用 `source_downloader_config` 读取 torrent_dir
- `_phase_add_torrents()` 和 Jackett 阶段改用 `destination_downloader_config` 读取参数

### 移除

- `config.yaml` 中 `jackett.size_tolerance` 配置项（改为内置绝对精度）

### 文档

- README 新增双模式辅种说明 + 架构图 + pieces-hash API 检测方法（404/405）
- README 新增双下载器配置示例 + transmission-rpc 安装说明
- `config.example.yaml` 新增双下载器注释模板
