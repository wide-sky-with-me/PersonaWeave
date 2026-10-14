# PersonaWeave model assets

这是本机模型缓存目录，不是源码资产目录；ONNX 权重和压缩包由 Git 忽略。首次运行先准备默认受管模型，可选研究模型与修复模型按各自的条件提供。

下载摘要以 [下载器](../scripts/download_models.py)为可执行来源，生产使用规则见 [本地换脸](../docs/facefusion-local-swap.md)。

## Setup

在仓库根目录执行：

```bash
uv run python scripts/download_models.py
```

有效的同名文件会复用，缺失文件会下载并校验。预期布局如下，注释区分必需资产、可选增强与研究后端：

```text
models/
├── buffalo_l/
│   ├── det_10g.onnx          # 必需：人脸检测
│   ├── w600k_r50.onnx        # 必需：身份特征
│   └── 2d106det.onnx         # 必需资产：单脸推理失败时关键点掩码降级
├── inswapper_128.onnx        # 必需：本地换脸
├── xseg_1.onnx               # 可选增强：手、道具等遮挡保护
├── bisenet_resnet_34.onnx    # 可选增强：眼镜等语义区域保护
├── hyperswap_1c_256.onnx     # 可选 ResearchRAIL 研究后端
├── ghost_1_256.onnx          # 可选 Apache-2.0 实验后端
├── crossface_ghost.onnx      # 与 Ghost 配套的特征转换器
└── codeformer.onnx           # 可选、显式开启的掩码内细节修复
```

`buffalo_l.zip` 还可能包含其他文件。`gfpgan_1.4.onnx` 若存在于本机缓存，也不是受管或生产资产：下载器、启动预检和流水线均不要求或加载它；其模型缺少身份保真控制，不应直接接入生产路径。

## Managed download integrity

下载器在下载后及复用同名文件前核对 SHA-256。默认受管集合的摘要如下：

| 资产 | SHA-256 |
| --- | --- |
| `buffalo_l.zip` | `80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f` |
| `buffalo_l/det_10g.onnx` | `5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91` |
| `buffalo_l/w600k_r50.onnx` | `4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43` |
| `buffalo_l/2d106det.onnx` | `f001b856447c413801ef5c42091ed0cd516fcd21f2d6b79635b1e733a7109dbf` |
| `inswapper_128.onnx` | `e4a3f08c753cb72d04e10aa0f7dbe3deebbf39567d4ead6dce08e98aa49e16af` |
| `xseg_1.onnx` | `c4d1498b8a03b5fe2a3a5d2ef2a0402ab03bd51edaf5b2d8d5fb764702a97dd3` |
| `bisenet_resnet_34.onnx` | `4a0b8c958a3c938913bd06a8365dbb3c8761afba6ecbf0d14b3b1f77eb230c96` |

CodeFormer 不在默认固定下载集合中。经过有限评估的本地文件摘要是 `21710e7ab61c82683576c428e9c1b6fe1ed419586b7b39e394c3449c294b550f`；它不是下载器的通用校验值。新增受管下载时须固定实际取得的资产摘要，不能把这条本地记录直接套到任意同名文件。

## Optional evaluation model

可选后端不随默认下载器自动准备，提供资产前须核对许可、来源与配套文件：

- **HyperSwap 1c**：`hyperswap_1c_256.onnx` 来自 `facefusion/models-3.3.0`，FaceFusion 元数据标为 ResearchRAIL。评估文件摘要为 `5528c2d76fe9986c99d829278987ef9f3a630cb606db7628d02b57b330f406a5`；启用须显式选择研究模型，并遵循 [路由规则](../core/model_router.py)。分发或商用前另行审查许可，开关不替代许可审查。
- **Ghost 与转换器**：`ghost_1_256.onnx` / `crossface_ghost.onnx` 必须成对使用，元数据为 Apache-2.0。评估文件摘要分别是 `4eac28021ae80128e9262080cf0da850e3190e41862650c9e805f9a516b3e924`、`9ec5862d9ff1f723a7380ea89baf87cddec9c56670d4db766702657939284957`；再分发仍须核对上游声明。
- **BiSeNet**：`bisenet_resnet_34.onnx` 是默认受管的 19 类 CelebAMask-HQ 模型，来自 `facefusion/models-3.0.0`（vendor yakhyo，MIT）。它保护眼镜等 XSeg 不判作遮挡的区域；摘要见上表，实现见 [region_parser.py](../core/region_parser.py)。

## Masked post-swap restoration model

[CodeFormerRestorer](../core/restorer.py)是可选生产能力，不是另一条诊断流水线。`enable_face_restoration=false` 默认关闭；显式开启且资产可用时，修复换脸工作裁剪的细节。

保护与强度分开控制：

- 修复输出仅通过现有 `crop_mask` 混合，沿用关键点、遮挡和语义保护交集，不扩展到背景或受保护像素。
- `restoration_fidelity_weight` 默认 `0.75`，控制 CodeFormer 身份保真，偏向换脸后的面部。
- `restoration_strength` 默认 `0.5`，控制原换脸输出与修复输出的混合。

GFPGAN 不具备相同保真控制，不属于支持路径。生产对齐、掩码与回贴边界见 [face_execution.py](../core/face_execution.py)和 [工程架构](../docs/engineering-architecture.md)；历史变化只查 [Changelog](../CHANGELOG.md)。

## Version-control policy

- ONNX、TensorRT engine、压缩包等生成二进制不强制加入普通 Git。
- 模型文件名或下载源变化，同步更新下载器；生产下载须固定完整性摘要。
- 可复现交付使用源码仓库外的版本化模型包，并附经核实的 SHA-256。
- 分发或商用前逐项审查上游许可与用途限制，源码公开不代表权重许可相同。

## Evaluation-only weights (`models/eval/`, git-ignored)

以下文件只用于 [离线评测适配器](../scripts/swap_eval_adapters.py)，API、CLI 和便携包不加载。来源为 FaceFusion 的 Hugging Face 发布；CRC32 与发布的 `.hash` 核对，SHA-256 为本项目评估时固定的值。任何再分发或商用须单独核对许可。

| 文件 | 发布集 | 许可（FaceFusion 元数据） | SHA-256 |
| --- | --- | --- | --- |
| `hyperswap_1a_256.onnx` | models-3.3.0 | ResearchRAIL | `c0e98a8a03a238f461ed3d2570e426b49f46745ee400854a60dceeb70c246add` |
| `hyperswap_1b_256.onnx` | models-3.3.0 | ResearchRAIL | `5124031789c42f71b9558fb71954ef7aedb6da7ed9fac79293e23c61a792a73e` |
| `alphaface_256.onnx` | models-3.9.0 | Non-Commercial | `efcca3ffa1c28b75a007f689b39f7d4716c02810e7fa72d892e175f0b058a2e2` |
| `ghost_2_256.onnx` | models-3.0.0 | Apache-2.0 | `27ae9d0b174b22c0426393e8239ca8d75e6ff47ec5dc596b002b182832a9454e` |
| `ghost_3_256.onnx` | models-3.0.0 | Apache-2.0 | `308d87f565e881b8872a5cbe711f97faeda6643e5d2b95ef757cc92a58662abd` |
