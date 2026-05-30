# Changelog

## 2026-05-30 — Jackett 辅种策略重构

### 变更
- **Jackett 尺寸过滤**：从百分比容差（±10%）改为绝对精度（≥1GB 时 ±0.01GB, ≥1MB 时 ±0.01MB）
- **Jackett 下载缓存**：pieces_hash 不匹配的 torrent URL 写入 `data/jackett_dl_cache.json`，后续运行跳过重复下载
- **qBittorrent 添加策略**：Jackett 模式下固定 `skip_hash_check=false`, `paused=true`，由 qB 自行 recheck 确认文件匹配
- **Jackett 日志增强**：每轮搜索显示搜索结果数 + 尺寸匹配数，进度行新增下载量与尺寸命中统计
- 移除无效的 `torznab:info_hash` 属性匹配逻辑（验证：Jackett 不返回 infohash 属性，`<guid>` 为网站链接非 info_hash）

### 新增
- `--mode` 命令行参数：支持 `pieces_hash` / `jackett` / `both` 三种模式
- `data/jackett_dl_cache.json`：pieces_hash 不匹配结果缓存

### 文档
- README 新增双模式辅种说明与架构图
- README 新增 pieces-hash API 检测方法（404/405）
- README 新增 Jackett 配置示例
- README 新增 transmission-rpc 可选依赖安装说明

### 移除
- `config.yaml` 中 `jackett.size_tolerance` 配置项（改为内置绝对精度）