# 本地换脸离线评测契约

**结论与边界。** 本页只约束非生产脚本，不改 API 任务历史、不调付费模型，也不作为生产验收闸门。身份余弦、参考库相对 top-1（期望参考是否唯一最高）与自然度是不同概念，不合成未经验证的“质量总分”。测试不用 ONNX 权重，正式 CLI 推理才初始化本地模型。

工具分工：

- `scripts/compare_swap_variants.py`：为一个明确目标人物生成受控变体。
- `scripts/evaluate_swap_quality.py`：关联同画布原图/结果，审计期望素材特征。
- `scripts/summarize_swap_benchmarks.py`：汇总完整声明样本集的覆盖与期望特征。
- `scripts/swap_benchmark_utils.py`：哈希、严格 JSON、图片索引、IoU/匈牙利关联的纯评测工具。

术语：stem 是无扩展名文件名；bbox 是脸框；IoU 是框交并比；cohort 是固定的声明/检测样本分母；provenance 是输入摘要、标签、模型与参数等来源证据。

## 1. 调用方式

在项目根目录用 uv 执行。以下仅为用法示例，不代表已对所列文件真实推理。

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

**前提与输入。** `person-index` 是原图检测 bbox 按 x/y/右/下字典序排列的 1-based 编号，须在实际检测数内；0、负数、越界或零检测均报错，不猜第一脸。mapping 绑定该真实 face 的 `target_embedding`，不能只传 `target_index`。

source 沿用生产参考提取规则。manifest 记录全部请求 source 哈希、成功加载路径和实际 uniform gallery（均匀图库）原始权重：未加权 `build_gallery_embedding` 对应 `gallery_reference_weights` 每张 1.0，不伪装质量加权。输出目录须为空，避免复跑残留结果冒充本次成功。

**受控变量。** 仅 `production` 与 `production_no_boundary_color` 两项；后者只覆盖 `enable_color_transfer=false`、`color_transfer_mode=off`。其余参数读取流水线函数默认并显式记录，`swapper_model` 按 CLI 原值传入；实际模型看 `model_routing`，不按脚本变量名猜。

**有效结果。** 同时满足 `faces_swapped==1`、画布形状与输入一致、输出数组不同于原图，才标有效；否则记 failed 且不给有效图片路径，原图不算消融结果。成功保存无损 `group_production.png`、`group_production_no_boundary_color.png`，不以 JPEG 重编码差异充当效果。读取 manifest 的 `output`，不硬猜 `.jpg`。

**失败。** 单项失败记录错误后尽量继续；任一变体失败则退出码 1，参数/文件初始化错误也非零。生成 manifest 本身不证明实验成功。

### 1.2 标注期望人物

`expected.json`：图片 stem → 原图 `face_index` → 单脸参考 stem。

~~~json
{
  "group": {"1": "person-a", "2": "person-b"},
  "another-photo": {"1": "person-a"}
}
~~~

编号须为规范正整数字符串，如 `"1"`，不是 `"01"`/`"0"`。未标注检测脸仍进入 cohort，`label_status=unlabeled`；声明图缺失或编号超过实际检测数时保留期望行，明确 `original_face_missing`，不缩分母。

编号来自原图检测，不代替人工几何标注。原图漏检且无额外期望标注时，工具推知不了未知人物；须人工查脸表并固定样本集。标签建议用未参加 source gallery 聚合的独立单脸参考，减少对训练输入的过拟合。

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

**范围。** 另一变体用 `--result-suffix _production_no_boundary_color`，独立输出 `audit-no-boundary.json`。原图目录必须是预定完整范围；仅生成一张时，其余仍统计 `result_missing`。单图实验用明确单图目录，不宣称覆盖全数据集。

**输入检查。** 目录支持 jpg/jpeg/png/webp；同 stem 多扩展名报错，如 `group.jpg` 与 `group.png`，不选第一个。每张参考须成功解码且恰有一脸；零脸、多脸或解码失败使整个审计停止。期望参考 stem 缺失记 `reference_missing`，不默认成功。空参考目录仅可查几何覆盖，没有身份测量。

### 1.4 按完整 cohort 汇总

~~~bash
uv run python -m scripts.summarize_swap_benchmarks \
  --audit production=outputs/benchmarks/audit-production.json \
  --audit no-boundary=outputs/benchmarks/audit-no-boundary.json \
  --output outputs/benchmarks/summary.json
~~~

**可比前提。** 每个 VARIANT 名唯一；报告须有相同完整 `inputs`（原图精确字节 SHA-256、期望标签）、`cohort`、`reference_hashes`、模型元数据和 `minimum_iou`。不同范围/原图/标签/参考库/识别模型不合榜。行序无关，固定键为 `(image, face_index)`，不用数组第 N 行。

**结果边界。** summary 按 variant 名确定性排序，不选赢家或按任意最相似参考排序。`scope=complete_declared_cohort` 只表示报告共同声明范围完整，不等于全数据集、全部真人或自然度达标。须预注册并公开输入清单；汇总器识别不了人为同步删改全部报告的输入清单，不能将子集冒充全覆盖。

## 2. 图像与人脸关联

1. 原图/结果须同尺寸、同通道，不自动 resize、缩放 bbox 或重建缺失结果。
2. 两图独立检测，只按 bbox IoU 一对一关联，不以结果身份分数寻找原脸，避免污染身份评测。
3. 优化前屏蔽低于 `minimum_iou` 的边，每个原脸保留 dummy（未匹配）出口。匈牙利算法先最大化有效匹配数，再最大化 IoU 总和；阈值仅 `(0,1]`，默认 0.3。
4. 不按检测列表 zip、最近顺序或身份分数回退；最左结果脸漏检时，后脸不顶替其编号。
5. cohort 为原图全部检测编号与 expected-map 声明编号的并集；缺图、解码失败、形状不符或漏检均保留原图/期望行。
6. 额外结果脸记 `images[].unmatched_result_faces`，额外结果图记 `unexpected_result_images`，不冒充原脸样本。

## 3. 输出字段

以下是评测报告契约，不是生产 API 字段清单；实现见各脚本。

### 3.1 manifest.json：swap-variants v1

顶层：`schema_version=1`、`kind=swap-variants`、`target`、`sources`、`person_index`、`target_bbox`、`source_mode`、`gallery_reference_weights`、`loaded_source_paths`、`variants`。

- `target.path / sha256 / pixel_sha256 / shape`：请求路径、原文件哈希、解码 BGR 数组字节哈希、画布形状。
- `sources[]`：全部请求素材 `path / sha256`；`loaded_source_paths` 为实际加载顺序，uniform gallery 权重与之对齐。
- `variants[]`：`variant`、显式 `parameters`、`status(valid/failed)`、`output`（成功才有相对路径）、`output_sha256`、`error`；有流水线结果时附 `faces_detected / faces_swapped / person_outcomes / model_routing / elapsed_time_sec`。

有效只表示产生受控单脸输出，不证明身份、自然度或版权验收。输入/输出哈希与参数用于复验，不嵌图片/embedding，也不承诺跨硬件逐字节一致。

### 3.2 audit JSON：swap-quality v2

顶层：`schema_version=2`、`kind=swap-quality`、`minimum_iou`、`reference_hashes`、`inputs`、`cohort`、`rows`、`images`、`unexpected_result_images`。真实 CLI 另写 `models.detector_sha256 / recognizer_sha256`；测试 helper 可无 `models`，但有/无或值不同的报告不混合汇总。

- **输入与分母：** `inputs[]` 含 `image(stem)`、`sha256`（缺原图为 null）、`expected` 标签；`cohort[]` 含每个声明/检测脸的 `image`、`face_index`、`expected_reference`（未标注 null），是独立于 rows 的分母。
- **检测状态：** `rows[].status` 为 matched、result_missing、result_decode_failed、canvas_mismatch、result_face_missing、original_face_missing、original_unavailable。
- **标签状态：** `label_status` 为 labeled、unlabeled、reference_missing，与检测状态独立。
- **几何证据：** `original_bbox`（可用时）、`result_face_index`、`match_iou`；`face_index` 永远是原图编号，`result_face_index` 只属于结果图。
- **身份证据：** `original_result_similarity`、`expected_similarity`、`expected_is_best`、`best_reference`、`best_similarity`；缺测 null，不用 0 冒充余弦值。
- **相对排名：** 几何匹配且期望参考存在时才算 `expected_is_best`，期望分数严格高于其他参考才 true，并列 false。无标签始终 null；单参考的相对 top-1 意义有限，不证明身份通过。`best_*` 仅诊断，不替代 `expected_similarity` 汇总。
- **逐图证据：** `images[]` 含 status、original_faces_detected、result_faces_detected、unmatched_result_faces、result_path、result_sha256；零原脸也保留，不从图片覆盖率消失。
- **CSV：** 与 JSON 同名、仅扩展名为 `.csv`，是 rows 扁平投影，不含完整来源/cohort/图片诊断。汇总读 JSON，不以 CSV 重建分母。

### 3.3 summary JSON：swap-benchmark-summary v2

顶层保留共同 provenance 和 `variants[]`，每个 variant 含：

- `total_images / cohort_faces`：共同图片/人脸分母。
- `labeled_faces / unlabeled_faces / reference_missing_faces`：标注覆盖，缺参考也留在已声明 expected 的 labeled 分母。
- `matched_faces / missing_or_unknown_faces`：几何覆盖。
- `expected_evaluable_faces / labeled_without_measurement`：具有合法期望余弦及布尔相对 top-1 的测量数/未测标签数。
- `expected_unique_top1_count / expected_unique_top1_fraction_all_labeled`：期望参考唯一相对 top-1 数/全部 labeled 占比；缺图、漏脸、缺参考、未知不得抬高比例，无标签时 null。
- `mean_expected_similarity_available_only`：仅合法已测 labeled 样本的条件均值，须连覆盖数解释，不单独排榜；缺测不记为负余弦或零分。
- `status_counts`：全部状态，未知记 unknown；row 缺失但 cohort 在时记 audit_row_missing，分母不变。
- `image_status_counts`：独立图片覆盖，含 audit_image_missing；合法零检测图仍统计。

重复稳定键、cohort 外 row、row/cohort 期望身份不一致、声明标签从 cohort 删除或重复图片声明，均使汇总失败。summary CSV 只含标量计数/指标，完整状态与共同来源以 JSON 为准。

## 4. 报告兼容边界

只汇总完整 provenance/cohort 的 v2 JSON；裸列表、CSV 重建或 `--selection` 行号选择均不接受。缺分母/标签/哈希的兼容报告须重新审计，不猜补全、不跳失败或未标注样本。

## 5. 回归验证

~~~bash
uv run pytest -q tests/test_swap_benchmarks.py
uv run ruff format scripts/compare_swap_variants.py scripts/evaluate_swap_quality.py scripts/summarize_swap_benchmarks.py scripts/swap_benchmark_utils.py tests/test_swap_benchmarks.py
uv run ruff check scripts/compare_swap_variants.py scripts/evaluate_swap_quality.py scripts/summarize_swap_benchmarks.py scripts/swap_benchmark_utils.py tests/test_swap_benchmarks.py
~~~

测试使用内存 detector/recognizer/pipeline stub（模拟对象）及合成小图，不加载权重或外部网关。覆盖目标 embedding、无效 index、零换脸/no-op、输出隔离、IoU 全局分配、最左漏检、同尺寸、缺图/坏图、未标注/缺参考/多脸参考、跨图稳定键、完整分母、未知/缺行、不同 cohort、legacy selection/裸列表等失败边界。

## Source strategy comparison

**范围。** 非生产单图实验，对同一人物、素材、模型、参数比较 `best_only`、`gallery`、`production_blend`。不发布任务结果、不改默认、不选赢家。

```bash
uv run python -m scripts.compare_source_strategies \
  --target case/target.png --source case/ref1.png --source case/ref2.png \
  --person-index 3 --parameters case/parameters.json \
  --output-dir outputs/diagnostics/source-case --cpu
```

**前提。** 按原顺序重复传入全部 `--source`，输出目录为空。参考须全部解码成功且每张恰有一脸，不静默丢弃；漏检歧义仍须人工排除。参数默认读生产签名；参数 JSON 只含现有换脸参数，从请求副本移除 `source_mode` 并保存原请求。策略由实验控制；未知字段、非有限阈值、非空 `manual_mask` 在初始化模型前报错，不实现画笔/批任务协议。

**策略。** `best_only` 用生产选择器选出的单参考、权重 1；`gallery` 用全部原参考及 API 相同质量权重；`production_blend` 用同图库和生产 best-match 混合，系数见 [本地换脸](facefusion-local-swap.md)。每臂独立从原图开始，共用色彩/遮罩/强度/修复参数，绑定从左到右指定人的真实特征，通过生产全局分配、侧脸保护与可见变化检查。目标框真实变化且 `faces_swapped=1` 才有效，单参考未通过姿态保护则保留失败。

**后端。** `--cpu` 请求 CPU Provider，`requested_providers` 不证明执行了 CUDA；`--models-dir` 指定模型目录。不下载模型、不调付费服务，完整选项见 `--help`。

**证据与退出。** `manifest.json` 保存文件/像素 SHA-256、素材顺序与质量/姿态/清晰度、完整参数、Provider 请求、计划/实际选择、逐人结果和路由；`reference_indices` 将局部参考索引映射回原集合，计划不代表执行。有效臂输出完整 PNG/摘要，`comparison.png`、`face_comparison.png` 固定三个位置，失败显示有标签空位，不以原图冒充。分母固定 `declared_strategy_count=3`；任一失败仍写报告/拼图后退出 1。路径/私人图片只留忽略的本地诊断目录。

**素材体检。** `scripts/source_quality.py` 仅给无副作用提示，不输出 embedding/数组、不删素材或作身份认证。实验阈值由 CLI 显式保存、初值查 `--help`，未校准不作质量门槛。重复文件/像素哈希只提示，图库不去重，同伴共识不让重复投两票。疑似身份离群须至少三个独立有效、维度一致特征，且其他参考形成稳定共识；两张冲突、数据无效/缺失、同伴不一致均未知。清晰度受尺寸影响，身份仍需人工与独立参考。

回归：`uv run pytest tests/test_source_strategy_comparison.py tests/test_source_quality.py`，用合成图/模拟推理覆盖真实选择、分配、姿态保护、参数一致、失败保留及未知证据。默认强度 `0.9` 与保守选择 `0.72` 的实测依据及方案取舍见 [默认强度决策](../.agents/notes/swap/20260907-default-strength-072-to-09.md)。
