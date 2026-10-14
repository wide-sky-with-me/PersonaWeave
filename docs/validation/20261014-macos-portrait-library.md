# 2026-10-14 Mac 素材库部署验收

## 范围与故障证据

目标为 `mac@100.88.228.28` 的既有 CPU Docker 实例。容器 `personaweave-personaweave-1` 将 `/Users/mac/services/personaweave/data` 读写挂载到 `/app/data`；素材根为 `/app/data/portrait_library`。修复前目录和 `library.sqlite3` 均缺失，素材接口返回 HTTP 200，但 `available=false`、身份与素材数量均为零，界面显示“素材目录不可用”。这不是 API 连接故障，也没有发现挂载只读问题。

仅补入人物库，不复制个人凭据、工程、历史或整个 `data/`。操作要求见 [素材库维护](../portrait-library.md#新实例与迁移)；容器环境见 [首次部署验收](20261013-macos-cpu-docker.md)。

## 输入与校验

来源是管理机既有发布包 `dist/release-20261004/PW-CPU/data/portrait_library`，不是正在使用的完整个人数据库。该快照含当前 64 个合成人物，数据库中有 1088 条素材（含拼图），界面可选参考应为 1024 张。当前人物和素材 ID 与本机可选库一致；发布快照不含本机已归档的其他身份。

- 文件数 2177，总字节数 283946582；全部与发布包 manifest 的 SHA-256 一致。
- SQLite `integrity_check=ok`，`foreign_key_check` 为空；原图摘要与素材行的 `content_hash` 一致。
- 数据库 SHA-256：`3f18090726d945df3a455434c722a6835dae7b5d436bed38f51ed2a24597e162`。
- 排序后的身份 ID 集合 SHA-256：`87f957e9e717c9c3dd74f7a5f07b1d8e8421d5ced9122c9f037197f91e747f2b`。
- 排序后的素材 ID 集合 SHA-256：`c0cd86fd1ff47863a4564b19ef818db7d1493e5b702729325463e1f2674301b9`。

管理机证据暂存于 `outputs/deployment/20261014-portrait-library/`。远端在数据挂载内的独立隐藏目录暂存，完成全文件校验后才原子启用；不修改发布包。

## 传输、启用与网页验证

性能排障期间曾暂停并保留暂存文件，避免与图片上传争用同一 Tailscale 链路；测量及分流结果见 [识别性能排障](20261014-macos-face-performance.md)。随后六组 `rsync --checksum --partial` 续传全部完成，无 `--delete`，发布包原件未修改。

启用前在远端容器内重算全部 2177 个文件 SHA-256、文件总数与字节数，检查 SQLite 完整性及外键，并核对数据库摘要和两个稳定 ID 集合摘要。目标目录仍不存在时，将挂载内暂存的 `portrait_library` 原子重命名为正式目录；没有重启容器或覆盖现有工程/历史。

API 随即返回 `available=true`、64 个身份、1024 张可选参考。真实 Chromium 的素材窗口显示相同计数，64 张人物卡均存在，选中人物默认选择 8 张参考；窗口 81 个图像元素均完成真实解码，“添加到人物素材”可用且无浏览器控制台错误。验证等待图像 `complete && naturalWidth>0`，不是把 `networkidle` 当作图片已解码。

管理机 `outputs/deployment/20261014-production/` 保留 `library-verified.json`、`library-activated.json`、`library-ui.json` 与截图。完整导入、执行及保存恢复验收另见本次生产网页记录；未调用付费 AI。
