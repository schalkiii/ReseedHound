# AGENTS.md

## 项目定位

PT 站跨站辅种工具：扫描本地 `.torrent` 的 `pieces_hash`，向各站点 API（或 Jackett）批量匹配，
把匹配到的种子下载并添加进 qBittorrent / Transmission。带原生定时调度与飞书卡片报告。
另有按站点分流的 HDArea REST API 通道：用 `info_hash` 批量匹配，凭据与下载链接都不同于通用通道。

## 怎么跑起来

```bash
pip install -r requirements.txt -r requirements-dev.txt
cp config.example.yaml config.yaml      # 填下载器地址、CookieCloud
cp sites.example.yaml sites.yaml        # 填站点 passkey / HDArea api_key

python run.py reseed                    # 一次性辅种
python run.py reseed --dry-run          # 只查询不添加（改动前先跑这个）
python run.py schedule                  # 按 config 中 scheduler 循环运行
python run.py sync-cookies              # 单独同步 CookieCloud
python run.py check-dead                # 对照 savept.icu 死亡名单，推荐禁用失效站点（--apply 才写）
python -m pytest tests/ -q              # 189 项用例（含 test_hdarea.py 21、savept/重试/凭据 33 项）
python -m flake8 src tests run.py       # 门禁：max-line-length=120（见 .flake8）
```

**门禁前置条件**：`pytest` / `pytest-asyncio` / `flake8` 不在运行时依赖里，必须额外装
`requirements-dev.txt`；`pytest.ini` 设 `asyncio_mode = auto`、`testpaths = tests`。
`.github/workflows/ci.yml` 在 push / PR 上用 Python 3.11 / 3.13 跑同样两条命令。
测试用 `tmp_path` 生成临时配置与数据库，**不读取本地 `config.yaml` / `sites.yaml`**。

`config.yaml`、`sites.yaml`、`logs/`、`data/` 含 passkey 与站点清单，已 gitignore，禁止入库。

## 技术栈

Python 3.11+（Docker 镜像 3.11，CI 覆盖 3.11 / 3.13）+ asyncio；
`qbittorrent-api` / `transmission-rpc` 操作下载器，`aiohttp` 并发查站点，`aiosqlite` 缓存，
`pycryptodome` 走 CookieCloud，纯 Python bencode（无第三方解析依赖）。

## 目录与约定

| 路径 | 作用 |
|---|---|
| `run.py` | 唯一入口（`reseed` / `schedule` / `sync-cookies`）；公共开关写在子命令前后都生效 |
| `src/runner.py` | **单轮辅种唯一实现**，一次性 `reseed` 与调度器每轮都调它，改流程只改这一处 |
| `src/storage/config.py` | `DEFAULTS` 是全部可缺省配置值的唯一来源；未知字段启动告警；`enable`/`enabled` 兼容 |
| `src/engine/seeder.py` | 三阶段流水线：扫描 → 查询 → 添加；按 `api_type` 分流 HDArea / 通用通道；统计与站点跳过 |
| `src/engine/downloader.py` | 下载器抽象层；`_is_add_success` 兼容不同 WebAPI 返回体；`add_torrent` 签名是接口契约 |
| `src/engine/jackett.py` / `src/engine/parser.py` | Jackett 搜索与尺寸过滤 / `.torrent` → info_hash、pieces_hash |
| `src/network/client.py` / `src/network/hdarea.py` | 站点 HTTP 与错误码（`post_json` 支持自定义头 + label）/ HDArea `info_hash` 批量匹配与全局节流 |
| `src/network/savept.py` | savept.icu 死亡名单拉取与本地站点对照（`check-dead` 用，只取 name/url 不碰凭据） |
| `src/network/cookiecloud.py` | CookieCloud 解密与域名匹配 |
| `src/storage/cache.py` | 增量缓存、辅种记录、死种持久化；`reseed_history` 有唯一索引 |
| `src/report/generator.py` / `src/report/site_table.py` | 报告排版 / **站点表统一口径**（行、四档状态灯、按成功率排序、截断） |
| `src/notify/feishu.py` | 飞书卡片与签名；站点表复用 `site_table.py` |
| `src/utils/logger.py` | 日志初始化、`redact_secrets()` 统一脱敏 passkey / API Key / `dl_token`、`RUN_MARKER` |
| `scripts/weekly_report.py` | 日志周报 + 远期日志清理（默认 dry-run，`--apply` 才写盘；不在镜像内） |

统计口径以「片段级」累计：只有某源种子没有任何站点成功时才计入 `failed_count`。

## 必守的坑（qBittorrent 版本相关）

- **`torrents/add` 的返回体分版本**：WebAPI ≥ 2.14.0（qB 5.2）返回 JSON 计数，更早才是文本 `Ok.`。
  只判 `== "Ok."` 会把成功全记成失败；「自动开始」只在添加成功分支里执行，所以这些种子会长期停在暂停态不做种。
- **`skip_checking` 在 5.3.0（WebAPI 2.16）改名 `seedMode`**，未识别参数被服务端**静默忽略**——
  HTTP 依然成功但校验照做。靠 `qbittorrent-api>=2026.8.1` 双写新旧名字，不要降版本或自己拼 HTTP。
- **`pausedUP` 在 5.x 改名 `stoppedUP`**（`paused`→`stopped` 同一波改名）；状态判断要用
  `STOPPED_COMPLETE_STATES`，不要写死旧名。
- **`is_paused=False` 传不出去**：qbittorrent-api 用 `is_paused or is_stopped` 合并这两个参数，
  单独传 `is_paused=False` 会变成 None（参数直接不发送），qB 按全局 `add_stopped_enabled` 把种子加成
  `stoppedUP`。要显式启动必须两个都传（见 `QbittorrentDownloader.add_torrent`），`_ensure_started` 再兜一层。
- **`print` 含 emoji 必须先 reconfigure**：Windows 控制台 GBK、或 stdout 被重定向时都会抛
  `UnicodeEncodeError`，还会让报告之后的飞书推送被整段跳过。`setup_logger` 已统一把 stdout/stderr 设为 UTF-8。
- **服务端对不认识的参数不报错**：任何「调了没反应」先去看 WebAPI 版本号，别怀疑业务逻辑。
- **凭据不写进代码默认值**：CookieCloud 的 uuid / password 只从 `config.yaml` 的 `cookiecloud` 块读。
  历史上的 argparse 默认值已随公开仓库泄露，删代码回收不了，只能在 CookieCloud 侧轮换。
  日志统一由 `_RedactSecretsFilter` 脱敏 `passkey`，新增日志点无需各自处理。
- **配置默认值只改 `DEFAULTS`**：`api_timeout` / `download_timeout` / `retry_*` /
  `download_interval` / `log.file` / 下载器行为开关都在 `src/storage/config.py`；业务模块与构造函数
  必须引用它，不要再写字面量默认值，并同步 `config.example.yaml` 与 README 示例。
- **单轮流程只改 `src/runner.py`**：`run.cmd_reseed` 与 `ReseedScheduler._run_once` 都是薄封装，
  不要在两处分别加逻辑（历史上正是这样改一漏一）。
- **报告站点表只改 `src/report/site_table.py`**：文本 / Markdown / 飞书卡片共用同一份行构造与排序。
- **日志轮次文案只改 `RUN_MARKER`**：`trim_log_file` 与 `scripts/weekly_report.py` 都引用它。

## HDArea 通道约定

- **按 `api_type` 分流，不要改通用站点的请求格式**：只有 `is_hdarea_site()` 为真才走
  `src/network/hdarea.py`；其余站点继续用 `passkey` + `pieces_hash`。`X-API-Key` 通过
  `post_json(..., headers=...)` 传入，不要塞进通用 `query_site()`。
- **凭据与下载链接都不同**：HDArea 站点只有 `api_key` 没有 `passkey`，`Config.sites` 按
  「有 `passkey` 或有 `api_key`」过滤站点。下载优先用接口返回的 `download_url`
  （`dl_token` 短效 1800 秒），仅 `http_403` 时用 `?apikey=` 直链兜底；不要改成 Cookie 方案。
- **200 是接口硬上限**：`MAX_HASHES_PER_REQUEST` 不可配，`split_batches()` 过滤非法 40 位 hex
  并分批；它与 `global.batch_size`（通用通道默认 100）是两套东西，不要复用。
- **限流按发送间隔而非并发数**：读取接口 60 次/分，`HdareaMatcher._throttle()` 用全局锁保证
  相邻批次间隔；`concurrency` 只限制在途响应数量，不要拿它做并发发送提速。
- **info_hash 是 pieces_hash 匹配的子集**：各站会改写 info 字典，召回率天然偏低
  （实测本地 200 个种子命中 4 个）。不要把「命中少」当 bug 去放宽匹配条件，也不要用它替代通用通道。
- **新增日志点无需各自打码**：`redact_secrets()` 覆盖 `passkey=` / `api[_-]?key=` /
  `dl_token=` / `X-API-Key:`，挂在两个 handler 上；新增凭据形态要同步补正则与专项测试。
- **默认值只在 `DEFAULTS["hdarea"]`**：`request_interval` / `concurrency` 经 `Config.value()`
  注入，改动须同步 `config.example.yaml` 与 README 的 HDArea 专节。

## 重试与站点失效约定

- **硬失败不重试**：404/405/410/401/403 等状态码，以及 `getaddrinfo failed`（域名已死）、
  `certificate verify failed`（证书已废）类异常，一律直接返回；重试与退避只会白等。
  只有超时/5xx/连接错误/429 限流才按 `retry_count` 重试。改重试策略要同步
  `NON_RETRYABLE_HTTP_STATUS` / `NON_RETRYABLE_ERROR_HINTS` 与 `tests/test_retry_policy.py`。
- **200 但非 JSON 单独归类**：表单错误页 / 登录跳转记为 `html_invalid_response`，不重试，
  不要混进网络错误里反复试。
- **日志区分批次与重试**：`post_json(..., label="第 X/Y 批")`，失败日志形如
  `[第 X/Y 批] (HTTP 404，硬失败不重试)`；改日志文案时保持「批次」与「共尝试 N 次」语义分开。
- **凭据失效要提醒不要静默**：`CREDENTIAL_ERRORS`（401/403/permission/forbidden/auth）计入
  `ReseedStats.credential_failures`，由 `FeishuNotifier.send_credential_alert()` 单独推送；
  新增凭据类错误码要同步补进该集合与 `tests/test_credential.py`。
- **`check-dead` 只推荐不自动改配置**：写入必须显式 `--apply`；匹配用域名 → 注册域 → 名称三级，
  `medium` 置信度（域名不同）必须留给人工核对，别自动禁用。

## 当前状态

- 生产下载器：qBittorrent v5.3.0 / WebAPI 2.16.2，约 4.1 万种子（地址见本地 `config.yaml`，不写入仓库）。
- 2026-11-07 完成 5.3.0 兼容修复（见 CHANGELOG 同名小节）；实测一轮：匹配 123 / 成功 8（修复前恒为 0）。
- 2026-11-07 按 `AUDIT-REPORT-2026-11-07.md` 完成审计修复与文档同步；测试从 17 项扩到 135 项，
  新增 `requirements-dev.txt` / `pytest.ini` / `.github/workflows/ci.yml` 门禁。
- 2026-11-07 首次发布 `v1.0.0`；GHCR 镜像由 `v*` tag 触发的 `.github/workflows/docker.yml` 构建，
  `latest` 只在 semver tag 上由 `flavor=latest=auto` 生成，手动触发不会覆盖。
- 2026-11-11 新增 HDArea REST API `info_hash` 辅种通道（`src/network/hdarea.py` +
  `tests/test_hdarea.py`）；测试由 135 项扩到 **156 项**，`flake8` 0 错误；
  Python 直连与 205 个真实种子的 dry-run E2E 均已验证。
- 2026-11-12 禁用 8 个失效站点（昆仑/传道院/raingfh/星陨阁/伞/a4apt/Moment/cdfile）；
  新增 `check-dead`（对照 savept.icu 死亡名单推荐禁用）与凭据失效飞书提醒；优化重试策略
  （硬失败不重试、HTML 响应归类、日志区分批次/重试）。测试扩到 **189 项**，`flake8` 0 错误。
