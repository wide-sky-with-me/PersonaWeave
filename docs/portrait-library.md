# 人像素材库维护

素材库以 `data/portrait_library/library.sqlite3` 为事实源，原图和缩略图存于同一素材库目录。接口只暴露不透明 ID，即不含磁盘路径的身份/素材标识。

新增素材使用 [`api/portraits/importer.py`](../api/portraits/importer.py) 的 `manifest.csv` 清单导入。导入具有幂等性：重复导入未变化的来源不重复建立素材。线上目录不通过扫描图片文件生成。

## 新实例与迁移

**前提：**素材库是独立持久数据，不随 Docker 源码镜像安装。只挂载空 `data/` 时，素材目录保持不可用；创建空目录或空数据库不会补出人物。

**完整快照迁移：**数据库、原图和缩略图必须来自同一版本，保留身份和素材 ID。先在暂存目录验证文件摘要与 SQLite 完整性，再放入服务使用的 `data/portrait_library/`。

目标已有库时，先停止写入并作一致性备份，不覆盖整个 `data/`。追加素材走同一 `manifest.csv` 入口，不用另一安装包的数据库替换已有身份记录。缺文件或校验未通过的快照不作为完整库迁入。

Mac 空库补齐的输入、校验和及界面证据见 [素材库部署验收](validation/20261014-macos-portrait-library.md)。

## 年龄范围与归档

个人素材包按用户要求展示创作设定为 16–25 岁的人物，女性 32 人、男性 32 人。当前形象为虚构中国年轻人，风格是用户确认的“自然上镜：清秀、阳光、五官协调、真实皮肤质感”；每人含 16 张独立角度及表情参考。

中国背景与年龄来自创作设定，不是从照片推断国籍、族群或真实年龄。

**归档意味着隐藏选择，不删除引用：**范围外人物保留 ID、原图和缩略图，并在 SQLite `archived_identities` 表记录归档。选择目录排除归档人物，旧任务/工程仍可按 ID 解析；不替换面孔或复用 ID。导入不自动恢复归档人物，也不全局禁止其他年龄的用户素材。

维护命令只有在无缺图、坏图或冲突时，才执行显式年龄归档：

```bash
uv run personaweave-import-portraits --source data/portrait_refresh_20260923 --archive-outside-ages 16 25
```

**复制与打包：**备份/复制已有工作目录须带完整素材库，包含归档文件。当前形象编号为 F45–F76 / M45–M76，独立于归档批次，不复用其 ID。新安装完整包为控制体积仅带当前 64 位人物；它的 `data/` 不用于覆盖已有工程目录。

回归见 [`tests/test_portrait_library.py`](../tests/test_portrait_library.py)。

## 生成素材质量检查

虚构人物由内置生图工具制作，批量生成与自动裁切已有用户授权。中国人物版本的原始图板、逐人物提示词、裁切图和校验报告位于 `data/portrait_chinese_20260923/`；补充归档人物角度的素材批次保留于 `data/portrait_refresh_20260923/`。

图板按规则网格裁切，不镜像冒充另一侧脸，不把裁切头像重复计为独立人物。

[`scripts/prepare_portrait_refresh.py`](../scripts/prepare_portrait_refresh.py) 是离线素材制作检查工具，不进入生产换脸流水线，也不调用 AI。它使用既有 SCRFD（人脸检测）和 ArcFace（人脸特征）检查：

- 每张裁切图恰有一张可检测人脸，记录检测置信度、脸部尺寸和 SHA-256 文件摘要。
- 对照正脸记录身份余弦相似度；补充已有角色时，还对照原始身份参考。
- 左右大侧脸分别核实五点 yaw（由五点关键点得到的左右偏转指标）的符号与覆盖范围。标签角度是生成目标，不是精确姿态测量。

不完整或一致性低的图板不进导入清单，须人工审阅并补做。这些检查不保证复杂遮挡下都能换脸成功；极端侧脸的生产保护仍生效，见 [本地换脸契约](facefusion-local-swap.md)。

## 默认选图

素材库界面确定性预选最多 8 张非拼图素材，不随机取图。优先覆盖正脸、左右最强侧脸、左右低头侧脸及接近 30° 的左右视角，再补满剩余名额。

可执行策略为 `web/src/utils/portraitAssetSelection.ts` 的 `selectDiversePortraitAssets()`，回归见 `portraitAssetSelection.test.ts`。

素材制作、检测数量及限制分别见 [旧素材实测记录](portrait-refresh-validation.md)、[当前素材实测记录](portrait-chinese-validation.md)。已有工作区选中的参考保持不变；使用其他侧脸参考需重新从素材库选择。
