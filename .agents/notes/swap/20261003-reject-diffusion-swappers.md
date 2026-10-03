# 不采用扩散换脸（REFace、DiffSwap++）

状态：有效
日期：2026-10-03

## 背景

评估两个可本地运行的扩散换脸模型：`Sanoojan/REFace`（提交 `f73bd8d`，权重 CC-BY-NC 4.0）与 `WestonBond/DiffSwapPP`（提交 `4b8f788`），代码 MIT，均基于 Stable Diffusion 1.4、50 步 DDIM。

## 决策

不进入生产路径或便携包，仅保留评估工具（`scripts/diffusion_swap_prepare.py`、`diffusion_pair_patch.py`、`evaluate_diffusion_swaps.py`）。

## 被放弃的方案

- **用扩散替换 ONNX 换脸**：在同一张正面源图下，REFace 质心相似度 0.313、DiffSwap++ 0.337，对 InSwapper 0.530、HyperSwap 0.531–0.540、AlphaFace 0.504（12 个共同可评分脸）；没有扩散结果达到 1% 误识阈值。
- **作为极端侧脸的补救**：扩散模型在 6 个被单参考守卫拒绝的侧脸上能出图，但身份迁移同样低（约 0.28），且常有嘴唇偏粉紫、肤色斑块等伪影。
- **补全对齐覆盖**：两者靠 dlib 正脸检测，35 个目标脸里只检出 18 个（51%），侧脸、低头进不了流程。

## 影响与验证

成本：单对约 5.5 分钟（GPU 无其他占用），内存紧张时 7–16 分钟，显存 7.7–7.9 / 8 GB；ONNX 为秒级。DiffSwap++ 因此只跑了前 6 对，并且发布的 `inference_swap_selected.py` 与 REFace 相同、不传 3D 特征文件名，3D 分支未被使用，**不等于论文完整配置**，结论不能外推到论文指标。安全做法：检查点是 pickle，加载前用 `pickletools` 静态扫描，运行时用白名单 `torch.load`；OpenAI CLIP 固定提交 `dcba3cb2`；训练专用的 `taming`、`wandb` 用本地占位模块；权重 SHA-256：REFace `last.ckpt` `194081de532a6bab92c5e135e6d950f859aafd4720e0a491152a3469f0762d10`，DiffSwap++ `last.ckpt`（12.9 GB）`90088755d2c727d7688042740be02c80f3262a8dcaf299c5d2471e2c5efd6a86`。报告 SHA-256：ONNX 基线 `d61c78241495ec621d3080839bc82d0749e5fdba897d3570224425690b894c13`、单参考对照 `3ee7e0d58d9499c3a619841062780781ac5bade2e03c9c322054bfba833e8e13`、REFace `df999ec7fda97da97ecea9948ba90bfac51cc96b2660c316ab92a816f82c8a6d`、DiffSwap++ `cae3b8dca5e24247203f87774ab150a8596a8a591453ae8433487baa6c38f01a`。样本小，不是自然度验收。
