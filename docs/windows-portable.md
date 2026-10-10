# Windows x64 便携版

提供 CPU 和 NVIDIA GPU 两种便携版，内置 Python、依赖、六个生产基线模型和前端，不要求安装 Python、Node、Docker、PyTorch 或 CUDA Toolkit。GPU 版内置 CUDA/cuDNN 运行库，只要求电脑已有兼容的 NVIDIA 显卡驱动；没有可用驱动或运行库缺失时选 CPU 版，界面就绪状态显示实际执行后端。AMD/Intel 显卡不由此 CUDA 包加速。包内只含稳定的 InSwapper 基线，不含研究/修复模型。实测记录见 [windows-portable-validation.md](windows-portable-validation.md)，文件摘要见压缩包旁的 `.sha256`。

## 使用

1. 把整个压缩包解压到 Windows 本地磁盘的可写短路径（如 `D:\PW`）；不要在压缩包内、网络共享或 WSL 共享目录中运行。
2. 双击 `Start.cmd`。模型加载完成后自动打开浏览器，窗口显示本机访问地址。
3. 可载入随包样例，或上传参考照片和目标图。工程、历史、素材与结果保存在包内 `data/`、`outputs/`；启动前会检查这两个目录是否可写。
4. 使用 AI 精修前双击 `Configure-AI.cmd`，填写视觉模型、图像模型、API 地址和 Key 后重启。模型名需带 LiteLLM provider 前缀（OpenAI 兼容服务填 `openai/实际模型名`），前缀由 LiteLLM 处理，不会作为模型名发给服务商。AI 服务联网并由服务商计费；启动和自检不调用模型。
5. 在启动窗口按 `Ctrl+C` 停止（HTTP 连接最多等待 10 秒，随后执行应用关闭）；再次启动会打开已有实例，不会创建第二个后台服务。

分发包不含开发者凭据或工作记录。内置模型保持原许可证约束（见 [models/README.md](../models/README.md)），依赖许可证保留在 `runtime/Lib/site-packages/`，Python 许可证位于 `runtime/`。

升级前停止程序并备份整个旧目录，保留 `data/`、`outputs/` 和 `.env`。不能用新包的 `data/` 覆盖旧目录。复制已配置目录到另一台机器时要连同 `runtime/`、`api/`、`core/`、`utils/`、`config/`、`models/`、`web/dist/`、`manifest.json` 以及 `data/`、`outputs/` 一起复制，复制前先停止程序；`.env` 含 AI 凭据，只交给被授权的人。

## 图片与路径

- 上传支持 JPEG/PNG/WebP，最大 20 MiB、4000 万像素；内存不足会停止当前图片并提示。
- 带 MPF 信息的相机 JPEG（Pillow 识别为 MPO）按主图读取，不拆辅助帧；工程保留原始字节，回看以 `image/jpeg` 提供。实现 `core/image_security.py::image_mime_type`，回归 `tests/test_image_security.py`、`tests/test_projects.py`。
- 中文目录下的文件统一由 Python 打开再用 OpenCV 内存编解码，不把路径直接传给 `cv2.imread/imwrite`。实现 [utils/image_io.py](../utils/image_io.py)，回归 `tests/test_image_io.py`。
- 一次可选多张图，程序逐张读取和处理；显示“等待内存”时关闭占内存的软件，任务会继续，等待超过两分钟会停止并说明，已完成的图片保留。规则见 [memory-scheduling.md](memory-scheduling.md)。

## 构建

入口 `scripts/build_windows_portable.py`，使用 `uv.lock` 导出固定依赖，Windows 的 ONNX Runtime 固定在构建脚本中；CPU 与 GPU 使用独立输出目录，禁止混装。OpenCV headless 提供唯一的 `cv2`。

```sh
uv run python scripts/build_windows_portable.py --msvc-dir /path/to/verified/microsoft-vc-runtime
uv run python scripts/build_windows_portable.py --edition gpu --msvc-dir /path/to/verified/microsoft-vc-runtime
uv run python scripts/build_windows_gpu_upgrade.py
```

- 产物 `dist/PersonaWeave-Windows-x64.zip`（CPU）、`dist/PersonaWeave-Windows-GPU-x64.zip`，均附 SHA-256；`manifest.json` 记录全部文件和模型摘要。模型只从已验证的 `scripts/download_models.py::MODEL_SHA256` 复制，Python 来源与官方摘要固定在构建脚本中，构建者提供来源已核实的 Microsoft VC 运行库。
- 构建前会重新构建前端并整体替换 `web/dist`；VC 运行库五个组件必须齐全且版本一致：`msvcp140.dll`、`msvcp140_1.dll`、`vcruntime140.dll`、`vcruntime140_1.dll`、`concrt140.dll`。
- 构建在同盘临时目录组装，ZIP 先写临时文件并逐项核对摘要，全部成功后才一起发布；发布失败会恢复原目录、ZIP 和摘要（不承诺抵御突然断电）。发行目录和发布目标拒绝符号链接。
- `--skip-deps` 只接受依赖输入指纹、requirements、完整 runtime 文件集合和逐文件摘要都匹配的成功构建；清单没有指纹时必须不带该参数重建。公共构建检查私有路径时忽略 Windows 大小写及分隔符差异，含 `.env`、个人数据库或结果的目录必须先保留并另选输出目录；不收集凭据。`--no-archive` 只更新目录，原 ZIP 与摘要保持不变。
- 构建时补齐 `cl100k_base` 离线分词缓存，使离线启动不尝试下载；只接受规范化后匹配既定 SHA-256 的缓存。
- 个人完整包可额外带素材库和现有 AI 配置，清单标记 `private_configuration_included`，只用于授权的本机，不得公开分发。

构建成功不等于 Windows 运行验证通过。必须在原生 Windows 验证：内置 Python 导入、CPU 模型加载、换脸 API、网页打开、工程恢复与结果下载。`scripts/smoke_windows_portable.py` 用一次性测试副本执行真实换脸、单实例保护、重启恢复、逐字节下载和删除验证，不对用户正在使用的目录运行。GPU 另需 `scripts/verify_windows_gpu.py`（六个模型的真实 CUDA kernel profile）和 `smoke_windows_portable.py --gpu`（强制 CUDA，并核对加载的 NVIDIA 运行库位于便携目录内）；禁止仅据健康检查宣布 GPU 验证成功。`scripts/verify_windows_dependencies.py` 在清除 Python/Conda/CUDA 路径的子进程里运行并枚举已加载 DLL 的来源（包内、Windows 系统、外部）；同一台机器的隔离测试不能称为另一台全新 Windows 的验收。

## GPU 选择

驱动检查使用系统 `nvcuda.dll` 的初始化和设备数。`utils/gpu_helper.py` 通过 ONNX Runtime `preload_dlls(directory="")` 和进程内 DLL 搜索目录加载随包运行库，并验证 CUDA provider 的 DLL 依赖链；不把 wheel 列出的 provider 当作真实执行证据。依据：[官方预加载与 CUDA/cuDNN 兼容说明](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html#preload-dlls)。健康接口返回各模型实际加载的后端和队列占用阶段，界面显示 CPU 模式、CUDA 已加载或部分模型 CPU，不以 `gpu_available` 单独判定。

## 升级、修复与自检

- **CPU 升级到 GPU**：关闭工作台，把 `PersonaWeave-Windows-GPU-Upgrade.zip` 解压到 CPU 目录（与 `Start.cmd` 同层），双击 `Upgrade-GPU.cmd`，之后仍用 `Start.cmd`。升级器核对已交付 CPU manifest、现有程序和全部升级文件摘要，运行中拒绝替换；只移除旧 CPU 包文件，保留 `.env`、工程、历史、素材与结果；中断后重新执行可继续，版本不匹配或程序文件被改动时拒绝覆盖，应改用完整 GPU 包。差分包必须用用户已拿到的 CPU manifest 构建。实测记录见 [windows-gpu-upgrade-validation.md](windows-gpu-upgrade-validation.md)。
- **源码修复包**：`scripts/build_windows_fix.py` 为已有原始 CPU/GPU 清单生成只含差异源码的修复包，清单与 ZIP 成员统一使用正斜杠；临时 ZIP 的成员集合、摘要和校验和全部验证后才发布，发布失败恢复原 ZIP 与校验和。安装复用 `scripts/upgrade_windows_gpu.py::apply_update` 的版本/摘要/运行锁校验，不改模型和用户数据；已单独打过补丁的版本可能不匹配，必须停止而不是强行覆盖。
- **升级中断**：源码修复/GPU 升级在替换前记录 `data/portable-update.json`；带检查的便携启动器发现该标记时停止，不加载模型或打开工程数据库。重新执行同一升级包可继续；其他升级包不能替换未完成事务。清单发布成功后清除标记；清单已发布但清理中断时，重跑须校验已替换文件和删除结果后才解除保护。不要手动删除标记。未带启动检查的旧启动器须先完成源码修复或使用完整包；此机制不承诺抵御突然断电。
- **`Repair-Runtime.cmd`**：用 Windows 自带 PowerShell 更新程序目录的 VC++ DLL（避免用程序自身 Python 更新其已加载的库），备份原文件和 manifest 到 `data/support_backups/`，不改系统库；运行中、载荷损坏或 DLL 被改动时拒绝更新。
- **`Check-Install.cmd`**：运行 `scripts/check_windows_portable.py`，核对文件、补丁标记、素材库引用和临时图片归档，输出不含 Key 和图片内容的 `install-check.json`；缺失/不可读文件、清单错误或配置加载故障时保留已收集的诊断，报告无法写入时返回失败并在控制台显示。素材路径通过 SQLite `mode=ro` 查询，不迁移表、不切换日志模式、不写入已有素材库或工程数据；[WAL 只读查询](https://sqlite.org/wal.html#read_only_databases)可能生成 SQLite 协调用的 `-wal`/`-shm` 文件。它不执行模型，不能据 `missing_files: []` 判定 GPU 或队列正常。
- **`Check-Performance.cmd`**：保持故障时的工作台运行后执行，`scripts/collect_windows_performance.py` 只读物理内存、Windows 提交额度、NVIDIA 查询和本地活动任务，输出 `performance-check.json`；不读 `.env` 或图片，不启动第二份模型，不调用 AI。旧服务没有 `model_providers/local_inference` 时报告为空，不补造硬件结论。
- 回归见 `tests/test_windows_fix.py`、`tests/test_windows_gpu_upgrade.py`、`tests/test_windows_portable.py`、`tests/test_projects.py`；针对性验证见 [交付保护实测](validation/windows-portable-safety-20261008.md)，提交前检查见 [收尾记录](validation/windows-closeout-20261010.md)。

## 排查

HTTP 422 表示请求被接口校验或归档逻辑拒绝，不能单凭状态码认定缺驱动或缺模型；需结合具体 POST/PUT 路由区分缺失缩略图、过期程序、无法访问的素材或无效工程字段。缺 VC 运行库通常使启动或导入失败，不能把所有 422 都归因于它。原始干净包只含四张示例图，不含个人素材库和工程；不能用干净包的示例目录覆盖已配置目录。未签名程序的 Windows 信任提示由系统控制，打包不能保证消除，也不会关闭系统防护；包内 Python 启动前的解压错误无法由程序兜底。

本地 AI Router 初始化失败（如无法识别模型协议）会单独显示为“AI 路由配置错误”，不报成上游 HTTP 400，修复配置后须重启（见 [model-catalog.md](model-catalog.md)）。便携版各项处理方式的原因与被否定的方案见决策笔记：[五个 VC++ 组件](../.agents/notes/windows/20260923-vc-runtime-five-components.md)、[中文路径与相机 JPEG](../.agents/notes/windows/20260929-unicode-paths-and-camera-jpeg.md)、[只发布校验过的构建](../.agents/notes/windows/20260930-publish-only-verified-builds.md)、[中断保护与只读自检](../.agents/notes/windows/20261008-guard-interrupted-updates-and-readonly-checks.md)。
