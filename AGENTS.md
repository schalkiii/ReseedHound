# AGENTS.md

## 项目定位

PT 站跨站辅种工具：扫描本地 `.torrent` 的 `pieces_hash`，向各站点 API（或 Jackett）批量匹配，
把匹配到的种子下载并添加进 qBittorrent / Transmission。带原生定时调度与飞书卡片报告。

## 怎么跑起来

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml      # 填下载器地址、CookieCloud
cp sites.example.yaml sites.yaml        # 填站点 passkey

python run.py reseed                    # 一次性辅种
python run.py reseed --dry-run          # 只查询不添加（改动前先跑这个）
python run.py schedule                  # 按 config 中 scheduler 循环运行
python run.py sync-cookies              # 单独同步 CookieCloud
python -m pytest tests/ -q              # 15 项测试
python -m flake8 src tests run.py       # 门禁：max-line-length=120（见 .flake8）
```

`config.yaml`、`sites.yaml`、`logs/`、`data/` 含 passkey 与站点清单，已 gitignore，禁止入库。

## 技术栈

Python 3.13 + asyncio；`qbittorrent-api` / `transmission-rpc` 操作下载器，`aiohttp` 并发查站点，
`aiosqlite` 缓存，`pycryptodome` 走 CookieCloud，纯 Python bencode（无第三方解析依赖）。

## 目录与约定

| 路径 | 作用 |
|---|---|
| `run.py` | 唯一入口（`reseed` / `schedule` / `sync-cookies`） |
| `src/engine/seeder.py` | 三阶段流水线：扫描 → 查询 → 添加；统计与站点跳过 |
| `src/engine/downloader.py` | 下载器抽象层；`_is_add_success` 负责兼容不同 WebAPI 返回体 |
| `src/engine/parser.py` | `.torrent` → info_hash / pieces_hash |
| `src/storage/cache.py` | 增量缓存、辅种记录、死种持久化 |
| `src/report/generator.py` | 报告排版；改排版要同步 `tests/test_report.py` |

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

## 当前状态

- 生产下载器：qBittorrent v5.3.0 / WebAPI 2.16.2，约 4.1 万种子（地址见本地 `config.yaml`，不写入仓库）。
- 2026-11-07 完成 5.3.0 兼容修复（见 CHANGELOG 同名小节）。
- 待处理：`scripts/weekly_report.py` 与 `SeedHound.bat` 仍未纳入版本管理，README 的目录结构也未提它们。
