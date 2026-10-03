# 默认换脸模型保持 InSwapper，HyperSwap 仅作显式研究选项

状态：有效
日期：2026-10-03

## 背景

要回答“有没有比 InSwapper 身份迁移更强的本地换脸”。在 13 张真实合影的 35 张人脸上，用素材库 64 位人物（每脸 3 个同性别随机身份，种子 20261003）走生产多人映射路径换脸，再用同一 ArcFace 与全部 64 位人物比较。FaceFusion 源码里的许可字段（2026-10-03 核对）：InSwapper、AlphaFace、SimSwap、BlendSwap 为 Non-Commercial；HyperSwap 1a/1b/1c 为 ResearchRAIL；Ghost 1/2/3 为 Apache-2.0。**本项目默认的 InSwapper 本身是非商用许可。**

## 决策

- 生产默认和 `auto` 路由仍只用 InSwapper；`hyperswap_1c_256` 只在显式请求并 `allow_research_models` 时使用，权重不进便携包。
- 评估用适配器只放 `scripts/swap_eval_adapters.py`，不进 `core/`，API/CLI/便携包不会加载。
- 分发或商用前先解决 InSwapper 授权，这比换模型更紧迫。

## 被放弃的方案

- **默认改用 HyperSwap**：身份迁移更强（质心相似度 0.562–0.573 对 0.523，排第 1 的比例 71–75% 对 58%，配对 1b/1c 比 InSwapper 平均高约 0.05），但 ResearchRAIL 不允许商用或分发；本地自用时可显式开启。
- **用 Ghost（唯一宽松许可）**：三个版本都不如 InSwapper（配对 −0.06 至 −0.08，几乎无结果达到 1% 误识阈值），口型和结构不稳定；Ghost 2 相对最好，Ghost 3 最差。
- **用 AlphaFace**：平均相似度略低（0.469，配对 −0.015），但跳过最少（7 对 18）、Top5 最高（87.6%）；非商用，不作为默认。其源向量缩放系数取 1/8/15.5/30 结果完全相同，模型内部会归一化。

## 影响与验证

复现：`uv run python -m scripts.evaluate_identity_similarity --target-list ... --arms inswapper_128:0.9 hyperswap_1c_256 ...`，评估权重放 `models/eval/`（已 gitignore），来源与 SHA-256 见 [models/README.md](../../../models/README.md)。报告 `report.json` 的 SHA-256：InSwapper 基线 `501715da36b716c76a1a0a1ced33083eb31f19d483026c8fab8201a50f373a0c`、三模型对比 `3592c4ff39591645d8fbc06bdd211d54a8b08f8ef30e70cb764b46ed11d6bd73`、八模型对比 `275c1021153c99c9161b05a11b65f26243f98bb4fac9a0f2f9c7dab99e8884bd`。含真人脸的图和报告只留本机 `outputs/diagnostics/`，不入库。限制：一台机器、一次、WSL 的 onnxruntime-gpu 1.20.2；像素差与检测分只是自然度代理，不是自然度验收。生产契约见 [facefusion-local-swap.md](../../../docs/facefusion-local-swap.md)。
