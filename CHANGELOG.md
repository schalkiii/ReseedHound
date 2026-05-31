# Changelog

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