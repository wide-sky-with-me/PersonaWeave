# Windows 便携版实测（2026-09-11）

本记录验证 Windows x64 CPU 和 NVIDIA GPU 便携构建。测试副本位于 Windows 本地临时目录，使用内置解释器启动，不使用系统 Python、Node 或 CUDA Toolkit。模型与运行时摘要由交付包 `manifest.json` 记录；构建及使用见 [便携版说明](windows-portable.md)。

## GPU 实测

RTX 4070 Laptop（8 GB），驱动 591.59；内置 ONNX Runtime GPU 1.23.2，CUDA 12.9 / cuDNN 9.24 依赖按 `uv.lock` 固定。`scripts/verify_windows_gpu.py` 对全部模型使用实际照片构造图像输入并运行推理，输出均为有限值；ONNX profiling 的 CUDA kernel 数如下，包含必要 CPU 节点，不承诺全部算子都在显卡上。

| 模型 | CUDA kernel 数 |
| --- | ---: |
| SCRFD det_10g | 141 |
| ArcFace w600k_r50 | 130 |
| 106 点关键点 2d106det | 61 |
| InSwapper 128 | 226 |
| XSeg | 392 |
| BiSeNet ResNet34 | 142 |

相同 source-01/group-01 样例通过完整 API 换脸，检测 5 人、指定替换 1 人，单次 2.15 秒；输出 SHA-256：`52b3c3f3678f19a49fa71b8f145d4eea9c0f070238371b44e533430f2e9ecb1c`。工程保存、重启恢复、原字节下载、删除和重复启动保护均通过。这是单机单次观测，不能作为通用性能保证。GPU 报告、profile 与图片位于本地 `build/windows/gpu-kernels.json`、`gpu-smoke.json` 及相邻文件。

GPU 差分升级的数据保留、路径约束、完整性预检、中断重试及运行锁回归见 `tests/test_windows_gpu_upgrade.py`。Windows 选择 GPU/CPU 的回归见 `tests/test_gpu_helper.py`。构建剔除开发文件及可重建字节码，不删除运行 DLL 或许可证，不改变模型精度。

## CPU 实测（原交付包）

- 宿主系统：Windows `10.0.26340.9354`；内置 Python `3.13.15`，CPU ONNX Runtime `1.23.2`。
- 原生导入与模型加载成功，健康检查 `pipeline_ready=true`、`gpu_available=false`、providers 为 `CPUExecutionProvider`。
- 生产前端和四张随包样例可访问，重复启动被单实例锁阻止。
- 通过 `scripts/smoke_windows_portable.py`，使用 `tests/fixtures/source-01.png` 替换 `group-01.png` 的第 1 人：检测 5 张脸、替换 1 张，单次推理 4.15 秒。这是该机器的单次观测，不是其他电脑的性能保证。
- 输出 SHA-256：`f291a0035c9786461318aadf729b53c0500835e311cfd02d39568f78be8fa178`。完整结果已人工查看，画布完整；此冒烟测试不代表所有侧脸、遮挡或多人场景的换脸质量验收。
- 保存包含原图及结果的工程，终止并重启后恢复完整文档；下载结果与原生成字节相同，随后删除工程成功。
- 测试未配置 AI Key，`ai_refinement_ready=false` 符合预期；此次 Windows 验证未调用付费 AI。精修需要首次填写服务配置。

可重复运行的脚本为 [`scripts/smoke_windows_portable.py`](../scripts/smoke_windows_portable.py)；本次 JSON、日志、图片与浏览器截图保存在本地 `build/windows/`，不会进入用户数据目录或交付包。源码回归覆盖便携配置、单实例、样例缺失和私有文件打包拦截，见 [`tests/test_windows_portable.py`](../tests/test_windows_portable.py)。

## 真实 AI 端到端（2026-10-03，GPU 包）

用户明确授权后，在 `PersonaWeave-Windows-GPU-20261003` 的一次性副本上运行
[`scripts/smoke_portable_real_ai.py`](../scripts/smoke_portable_real_ai.py)，使用包内 `.env` 的视觉与图像服务；
副本测试后删除，用户已安装目录和数据未参与。流程与结果：本地 GPU 换脸（5 人检出、1 人替换）→ 创建精修会话 →
`discuss` 轮（视觉诊断 1 次，状态 completed，版本数仍为 1，未生图）→ `generate` 轮（整图生图 1 次并复评，
状态 review_required、`review_error` 为空，生成候选 3,353,015 字节且摘要与回读一致）→ 校验 revision/摘要后接受并发布。
每个付费请求只发送一次、无重试；服务端日志无异常。人工查看：候选保持 5 人、姿态、构图与背景，
属整图重绘，细节（如背心文字）可能与基图略有差异——这是整图协调重绘的既定语义，不承诺其余区域逐像素相同。
诊断文本指出人物 1 的可见脸部特征与参考仍有明显差异，说明此流程验证的是链路可用与状态机正确，
**不代表换脸身份相似度或精修画质的验收**。耗时、费用及模型名不作记录；仅在这一台 Windows 机器、单次观测。
