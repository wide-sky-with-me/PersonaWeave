# 人像素材库维护

素材库以 `data/portrait_library/library.sqlite3` 为事实源，原图与缩略图位于同目录。
新素材使用 [`api/portraits/importer.py`](../api/portraits/importer.py) 的 `manifest.csv` 幂等导入，
不通过扫描图片目录构造线上目录；接口继续只暴露不透明 ID。

## 新实例与迁移

素材库属于独立持久数据，不随 Docker 源码镜像安装。服务目录只挂载空的 `data/` 时，素材目录保持不可用；创建空目录或空数据库不会补出人物素材。
新实例可复制经校验的完整素材库快照，数据库、原图与缩略图必须来自同一版本，并保留身份和素材 ID。暂存目录中验证文件摘要及 SQLite 完整性后，再放入服务使用的 `data/portrait_library/`；已有目标目录时先停止写入并作一致性备份，禁止覆盖整个 `data/`。
已有库追加素材使用上述 `manifest.csv` 幂等导入入口，不以另一安装包的数据库替换已有身份记录。
Mac 空库补齐的实际输入、校验和与界面验证见 [素材库部署验收](validation/20261014-macos-portrait-library.md)。

## 年龄范围与归档

本次个人素材包按用户要求只展示 16–25 岁人物，女性 32 人、男性 32 人。
当前形象为虚构中国年轻人，采用用户确认的“自然上镜：清秀、阳光、五官协调、真实皮肤质感”风格。
中国人物设定来自创作提示词，不是根据照片推断国籍或族群；每人包含 16 张独立角度及表情参考。
年龄是合成人物的创作设定，不是通过人脸模型推断的真实年龄。
原有超出范围人物保留 ID、原图及缩略图，在 SQLite `archived_identities` 表记录归档。
新选择目录排除这些人物，但旧任务和工程的 ID 解析仍然有效；不会把老人物换成新面孔或复用其 ID。
后续导入不会自动恢复归档人物，也不会全局禁止其他年龄的用户素材。

维护命令在导入没有缺图、坏图或冲突时，才会执行显式年龄归档：

```bash
uv run personaweave-import-portraits --source data/portrait_refresh_20260923 --archive-outside-ages 16 25
```

归档不删除历史文件；备份或复制已有工作目录时仍须带上整个素材库。
本次全新形象使用 F45–F76 / M45–M76，不复用上一批人物 ID；旧可选人物作为明确批次归档。
为控制体积，新安装的完整包仅携带当前 64 位人物；不能用这个新包的 `data/` 覆盖已有工程目录。
回归测试见 [`tests/test_portrait_library.py`](../tests/test_portrait_library.py)。

## 生成素材质量检查

使用内置生图工具生成虚构人物。当前中国人物版本的原始图板、逐人物提示词、裁切图和校验报告
保存于 `data/portrait_chinese_20260923/`；补充旧人物角度的批次保留在
`data/portrait_refresh_20260923/`。用户已明确允许批量生成与自动裁切。
每张图板按规则网格裁切，不镜像冒充另一侧脸，不把裁切后的头像重复计为独立人物。

[`scripts/prepare_portrait_refresh.py`](../scripts/prepare_portrait_refresh.py) 是离线素材制作检查工具，
不进入生产换脸流水线，也不调用 AI。它使用现有 SCRFD 检测和 ArcFace 特征验证：

- 每张裁切图只有一张可检测人脸，并记录检测置信度、脸部尺寸和文件 SHA-256。
- 对照正脸记录身份余弦相似度；补充原有角色时还须对照原始身份参考图。
- 左右大侧脸分别核实五点 yaw 的符号和覆盖范围；标签里的角度表示生成目标，不是精确姿态测量。
- 不完整或低一致性图板不进入导入清单，须人工审阅并补做。

这些检查不保证所有复杂遮挡都能成功换脸。生产中的极端侧脸保护继续生效（见 [本地换脸契约](facefusion-local-swap.md)）。

## 默认选图

素材库界面确定性地预选最多 8 张非拼图素材：优先正脸、左右最强侧脸、左右低头侧脸和接近 30° 的左右视角，再补满剩余名额，不随机取图。策略的可执行实现是 `web/src/utils/portraitAssetSelection.ts` 的 `selectDiversePortraitAssets()`，回归 `portraitAssetSelection.test.ts`。

上一批素材的制作、检测数量和限制见 [旧素材实测记录](portrait-refresh-validation.md)，
当前中国人物版本见 [当前素材实测记录](portrait-chinese-validation.md)。
旧工作区已选的参考图保持不变；要使用新增侧脸，请重新从素材库选择该人物。
