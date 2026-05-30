# Changelog

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