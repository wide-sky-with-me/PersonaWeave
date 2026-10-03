# 本地换脸离线评测契约

本文件只约束非生产评测脚本：

- scripts/compare_swap_variants.py：单个明确目标人物的受控变体生成。
- scripts/evaluate_swap_quality.py：原图与结果的同画布几何关联、期望素材特征审计。
- scripts/summarize_swap_benchmarks.py：完整声明样本集的覆盖率与期望特征汇总。
- scripts/swap_benchmark_utils.py：哈希、严格 JSON 输出、图像索引、IoU/匈牙利关联纯评测工具。

不修改 API 任务历史，不调用付费模型，不构成生产验收闸门。身份余弦相似度、参考库相对 top-1 和画质/自然度是不同概念；本工具不把它们合并成未经验证的“质量总分”。权重测试不需要 ONNX 文件，正式 CLI 推理才初始化现有本地模型。

## 1. 调用方式

从项目根目录通过 uv 运行。以下命令是用法示例，不代表已经对这些文件做过真实推理。

### 1.1 生成有效变体

~~~bash
uv run python -m scripts.compare_swap_variants \
  --target /path/to/originals/group.png \
  --source /path/to/source/person-front.png \
  --source /path/to/source/person-profile.png \
  --person-index 2 \
  --output-dir outputs/benchmarks/group-variants \
  --swapper-model inswapper_128
~~~

- person-index 是原目标图检测结果按 bbox 的 x/y/右/下坐标字典序排列的 1-based 编号。必须落在实际检测数内；0、负数、越界或零检测直接拒绝，不猜第一张脸。
- 从这个真实目标 face 提取 target_embedding 并放入 mapping，而不是只传 target_index。原脚本漏传该字段会令当前身份分配跳过全部人物，该行为已修复。
- source 仍走现有生产 source-reference 提取规则；manifest 同时列出全部请求 source 哈希、成功加载 source 路径与实际 uniform gallery 原始权重。这里用未加权 build_gallery_embedding，故 gallery_reference_weights 明确为每张 1.0；不是伪装成 quality 加权。
- 变体只有 production 和 production_no_boundary_color。后者只覆盖 enable_color_transfer=false 与 color_transfer_mode=off，其余参数取当前流水线函数默认值并显式记录；swapper_model 按 CLI 原值传递。不能根据脚本变量名推断实际模型，需看 model_routing。
- 输出目录必须为空，拒绝复用含旧结果的目录，避免失败重跑留下历史成功图冒充本次输出。
- 每个变体必须 faces_swapped==1、画布形状与输入完全一致、输出数组不与原图完全相同。任何一项不满足都标 failed，不写有效图片路径；不允许原图算有效消融。
- 成功写无损 PNG：group_production.png、group_production_no_boundary_color.png。不用 JPEG 重编码差异充当效果差异。已有消费者应改读 manifest 中的 output，而不是硬猜 .jpg。
- 一项变体失败仍尽量记录其错误并继续其余变体；任意失败使 CLI 退出码为 1。参数/文件初始化错误也非零退出。不能只看生成了 manifest 就判定实验成功。

### 1.2 标注期望人物

expected.json 使用保留的明确结构：图片 stem → 原图 face_index → 单脸参考图片 stem。

~~~json
{
  "group": {"1": "person-a", "2": "person-b"},
  "another-photo": {"1": "person-a"}
}
~~~

图片 key 是不含扩展名的文件名；编号字符串必须是规范正整数（例如 "1"，不是 "01" 或 "0"）。同一原图里未提供标签的检测脸仍进入 cohort，但 label_status=unlabeled。声明了图名但原图缺失，或声明的人物编号超过原图实际检测数，仍保留期望行并明确 original_face_missing；不能默默缩小分母。

这些编号基于原图检测，不是人类标注几何的替代。若原图本身漏检且没有额外期望标注，工具不可能推知未知人物；需人工检查原图脸表并固定样本集。为避免对训练输入过拟合，推荐使用未参与 source gallery 聚合的独立、单脸参考照片做标签。

### 1.3 审计完整目录

~~~bash
uv run python -m scripts.evaluate_swap_quality \
  --original-dir /path/to/originals \
  --result-dir outputs/benchmarks/group-variants \
  --reference-dir /path/to/single-face-references \
  --expected-map /path/to/expected.json \
  --result-suffix _production \
  --minimum-iou 0.3 \
  --output outputs/benchmarks/audit-production.json
~~~

对另一个变体使用 --result-suffix _production_no_boundary_color，输出独立 audit-no-boundary.json。原图目录应是预先确定的完整实验范围：目录包含更多原图而结果只生成一张时，其余图片必须统计为 result_missing，不能省掉。若只做单图实验，使用明确单图输入目录，不宣称它覆盖整个数据集。

所有目录支持 jpg/jpeg/png/webp；同 stem 的多个扩展名明确拒绝（如 group.jpg 和 group.png 同时存在），不挑第一个。参考目录每张图必须恰好检测出一个脸；零脸、多脸或解码失败都会拒绝整个审计，用户应先准备独立单脸参考图。expected 指向不存在的参考 stem 会标 reference_missing，不默认成功。空参考目录可做几何覆盖率检查，但没有身份测量。

### 1.4 按完整 cohort 汇总

~~~bash
uv run python -m scripts.summarize_swap_benchmarks \
  --audit production=outputs/benchmarks/audit-production.json \
  --audit no-boundary=outputs/benchmarks/audit-no-boundary.json \
  --output outputs/benchmarks/summary.json
~~~

每个 VARIANT 名必须唯一。各报告必须有相同完整 inputs（原图精确字节 SHA-256、期望标签）、cohort、reference_hashes、模型元数据和 minimum_iou；不同范围、原图、标签、参考库或审计识别模型不能悄悄合榜。行顺序不影响结果，关联键固定为 (image, face_index)，不能使用 JSON 数组的第 N 行。

summary 按 variant 名确定性排序，不输出“赢家”或按任意最相似参考身份排序。scope=complete_declared_cohort 只表示覆盖了报告共同声明的范围，不等于全数据集、全部真人或自然度达标。必须预注册并公开输入清单；同时人为删改所有报告的输入清单不可能由汇总器单独发现，不能把这种子集冒充全覆盖。

## 2. 图像与人脸关联

1. 原图和结果必须同尺寸同通道画布；不自动 resize、缩放 bbox 或重建缺失结果。
2. 原图/结果独立检测后只通过 bbox IoU 做一对一关联，不使用结果身份分数寻找最像的原脸，以免污染评测。
3. 低于 minimum_iou 的边在优化前屏蔽；每个原脸有 dummy 未匹配出口。匈牙利先最大化有效匹配数，再最大化 IoU 总和。阈值只接受 (0,1]，默认 0.3。
4. 没有按检测列表 zip、最近顺序或身份分数回退。结果漏掉最左脸时，后面的脸不会错误顶替其编号。
5. face cohort 是原图全部检测编号与 expected-map 全部声明编号的并集；结果缺图、解码失败、形状不符、漏检，都保留对应原图/期望行。
6. 结果额外检测的人脸写 images[].unmatched_result_faces；额外结果图片写 unexpected_result_images，不冒充原脸样本。

## 3. 输出字段

### 3.1 manifest.json：swap-variants v1

顶层：schema_version=1、kind=swap-variants、target、sources、person_index、target_bbox、source_mode、gallery_reference_weights、loaded_source_paths、variants。

- target.path / sha256 / pixel_sha256 / shape：请求路径、原始文件哈希、解码 BGR 数组字节哈希、实际画布形状。
- sources[]：全部请求素材 path / sha256；loaded_source_paths 是实际加载参考顺序。uniform gallery 权重与该加载顺序对齐。
- variants[]：variant、显式 parameters、status(valid/failed)、output（成功才有相对路径）、output_sha256、error，以及有流水线结果时的 faces_detected / faces_swapped / person_outcomes / model_routing / elapsed_time_sec。
- 有效仅表示实际产生受控单脸换脸输出，不代表身份、自然度或版权验收。输出哈希、输入哈希和参数用于复验；本格式不嵌入图片或 embedding，也不声称每次跨硬件推理逐字节一致。

### 3.2 audit JSON：swap-quality v2

顶层：schema_version=2、kind=swap-quality、minimum_iou、reference_hashes、inputs、cohort、rows、images、unexpected_result_images。真实 CLI 另外写 models.detector_sha256 / recognizer_sha256；纯测试 helper 报告可没有 models，但有/无或值不同的报告不能混合汇总。

- inputs[]：image(stem)、sha256（缺原图为 null）、expected 标签对象。
- cohort[]：每一个声明/检测原脸的 image、face_index、expected_reference（未标注为 null）。它是独立于测量 rows 的分母清单。
- rows[] 的 status：matched、result_missing、result_decode_failed、canvas_mismatch、result_face_missing、original_face_missing、original_unavailable。
- label_status 与检测状态正交：labeled、unlabeled、reference_missing。
- 几何证据：original_bbox（可用时）、result_face_index、match_iou。face_index 始终是原图编号，result_face_index 只是该结果图检测编号。
- 身份证据：original_result_similarity、expected_similarity、expected_is_best、best_reference、best_similarity。缺测为 null，不用 0 伪装真实余弦值。
- expected_is_best 仅在几何匹配且期望参考存在时计算：期望分数严格大于其他参考才 true；并列 false。没有 expected 标签永远 null，而不是旧脚本的 true。只有单个参考时相对 top-1 意义有限，不能宣称身份通过。
- best_* 仅辅助诊断；汇总不拿任意 best_similarity 代替 expected_similarity。
- images[]：逐图 status、original_faces_detected、result_faces_detected、unmatched_result_faces、result_path、result_sha256。零原脸图也保留 image 记录，不能从图片覆盖率中消失。
- CSV 与 JSON 同名，仅换 .csv 扩展名；CSV 是 rows 的扁平投影，不承载完整 provenance/cohort/图片诊断。汇总必须读 JSON，不能用 CSV 重建不完整分母。

### 3.3 summary JSON：swap-benchmark-summary v2

顶层记录共同 provenance 和 variants[]。每个 variant 保留：

- total_images / cohort_faces：共同声明图片与人脸分母。
- labeled_faces / unlabeled_faces / reference_missing_faces：标注覆盖；缺参考也算已声明 expected 人物，不能逃离 labeled 分母。
- matched_faces / missing_or_unknown_faces：几何覆盖情况。
- expected_evaluable_faces / labeled_without_measurement：有合法期望余弦值与布尔相对 top-1 的测量数，以及未能完成测量的标签数。
- expected_unique_top1_count / expected_unique_top1_fraction_all_labeled：期望参考唯一相对 top-1 数与全部 labeled 分母占比，缺图/漏脸/缺参考/未知不能提高该比例。没有任何标签时比例为 null。
- mean_expected_similarity_available_only：仅在合法已测量 labeled 样本上算的条件均值，必须与覆盖数一起解释，不能单独排榜；缺测并未被当成负余弦值或零分。
- status_counts：包含全部状态；未知状态记 unknown。若报告删除测量 row，但 cohort 仍保留，则记 audit_row_missing，分母不变。
- image_status_counts：独立保留图片覆盖，包括 audit_image_missing。合法但没有检测脸的图片也有统计。

重复稳定键、cohort 外的测量行、期望身份在 row 与 cohort 间不一致、expected 标签在 cohort 被删、重复图片声明，都明确拒绝。summary CSV 保留标量计数/指标；完整状态分布和共同 provenance 以 JSON 为准。

## 4. 报告兼容边界

汇总只接受带完整 provenance/cohort 的 v2 JSON，不接受旧裸列表、CSV 重建或 `--selection` 行号选择。旧报告缺少分母、标签或哈希时必须重新审计；不猜测补全，不跳过失败或未标注样本。

## 5. 回归验证

~~~bash
uv run pytest -q tests/test_swap_benchmarks.py
uv run ruff format scripts/compare_swap_variants.py scripts/evaluate_swap_quality.py scripts/summarize_swap_benchmarks.py scripts/swap_benchmark_utils.py tests/test_swap_benchmarks.py
uv run ruff check scripts/compare_swap_variants.py scripts/evaluate_swap_quality.py scripts/summarize_swap_benchmarks.py scripts/swap_benchmark_utils.py tests/test_swap_benchmarks.py
~~~

测试用内存 detector/recognizer/pipeline stub 与合成小图片，不加载模型权重、不调用外部网关。覆盖目标 embedding 绑定、无效 index、零换脸/no-op拒绝、旧输出隔离、IoU 全局分配、最左脸漏检、同尺寸约束、缺图片/坏图片、未标注/缺参考、多脸参考拒绝、同编号跨图稳定键、完整分母、未知/缺行、不同 cohort 拒绝，以及 legacy selection/裸列表拒绝。

## Source strategy comparison

非生产单图实验：对同一目标人物、素材集合、模型和参数比较 `best_only`、`gallery`、`production_blend`。不发布任务结果、不调整生产默认，也不自动评选赢家。

```bash
uv run python -m scripts.compare_source_strategies \
  --target case/target.png --source case/ref1.png --source case/ref2.png \
  --person-index 3 --parameters case/parameters.json \
  --output-dir outputs/diagnostics/source-case --cpu
```

- 按原顺序重复传入全部 `--source`，输出目录必须为空。每张参考必须成功解码且仅检测到一张脸，不静默丢弃素材；仍需人工排除漏检造成的歧义。
- 参数默认从生产流水线签名读取。实验参数 JSON 只传现有换脸参数；从请求副本移除 `source_mode`，保留原请求作为证据。策略由实验控制；未知字段、非有限阈值或非空 `manual_mask` 在模型初始化前拒绝。脚本不实现画笔/批任务协议。
- `best_only` 使用生产选择器选出的单张参考与权重 1；`gallery` 使用全部原始参考及 API 相同的质量权重；`production_blend` 使用同一图库与生产 best-match 混合。具体系数见 [本地换脸](facefusion-local-swap.md)。
- 每臂独立从原图开始，共用颜色、遮罩、强度和修复参数；绑定原图从左至右指定人物的真实特征，仍通过生产全局分配、侧脸保护和可见变化检查。只有目标框实际变化且 `faces_swapped=1` 才算有效；单参考不满足姿态保护时保留失败。
- `--cpu` 请求 CPU Provider，`requested_providers` 不是 CUDA 已运行的证据。模型目录可通过 `--models-dir` 指定；脚本不下载模型或请求付费服务，全部选项见 `--help`。

`manifest.json` 保存输入文件/像素 SHA-256、素材顺序与质量/姿态/清晰度、完整参数、Provider 请求、计划与实际选择、逐人结果及模型路由。`reference_indices` 将各臂局部参考索引映射回原始集合；计划存在不代表推理已执行。

有效臂输出完整 PNG 与摘要；`comparison.png`、`face_comparison.png` 固定保留三个位置，失败显示有标签的空位，不用原图冒充结果。分母始终为 `declared_strategy_count=3`；任一臂失败仍写报告/拼图后以退出码 1 结束。路径和私人图片只留在忽略的本地诊断目录。

素材体检由 `scripts/source_quality.py` 提供无副作用的提示，不输出 embedding/图像数组、不删素材或作身份认证。实验阈值通过 CLI 显式保存，初值见 `--help`，未经校准不作为质量门槛。重复文件/像素哈希产生提示：实验图库不去重，计算同伴共识时重复不投两票。疑似身份离群至少需要三个独立有效且维度一致的特征及其他参考形成的稳定共识；两张意见不合、数据无效/缺失或同伴不一致均保留未知。清晰度受尺寸影响，身份判断仍需人工与独立参考证据。

回归：`uv run pytest tests/test_source_strategy_comparison.py tests/test_source_quality.py`，使用合成图片与模拟推理，覆盖真实选择/分配/姿态保护、参数一致、失败保留及证据未知。

默认强度从 0.72 改为 0.9 的实测依据与被否定的方案见决策笔记 [默认强度](../.agents/notes/swap/20260907-default-strength-072-to-09.md)。
