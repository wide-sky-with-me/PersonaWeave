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
- 成功写无损 PNG：group_production.png、group_production_no_boundary_color.png。不再用 JPEG 重编码差异充当效果差异。已有消费者应改读 manifest 中的 output，而不是硬猜 .jpg。
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

## Recorded strength comparison

以下为 2026-09-07 的有限样本历史证据，保留输入摘要、复现路径与测量解释；当前行为以 [本地换脸契约](facefusion-local-swap.md) 为准。私人输入不随仓库分发，缺少对应本机任务和素材时不能直接复现。

### 诊断可靠性修复复核（2026-09-07）

本轮最终证据目录：`outputs/diagnostics/pw_task_7e24362539-diagnostics-v2-final/`（`-diagnostics-v2/` 是保留的首遍复核，最终版进一步明确了白色表示允许换入、request值不是最终alpha）。原任务和原诊断目录保留。使用同一请求与四张输入重新跑 0.72/0.9/0.95，十二张完整结果与原诊断 JPEG 解码后逐像素差均为 **0**；人数仍分别为 **4/2/0/0、4/3/0/0、4/3/1/1**。这证明诊断改造没有改变这组样本的生产结果，**不是本轮又提升了画质**。四图尝试的脸均使用256工作裁剪，不能验证128尺度修正的真实小脸画质；128/256保护尺度由合成同几何回归覆盖，尚需更多真实小脸样本。

- `report.json` 升为 schema_version 2，逐人包含 matching/source_selection/mask 证据。`image_N_face_K_sources.jpg` 展示该mapping实际全部素材、真实best索引及聚合权重，不再跨人物库寻找最近照片。
- **更正旧拼图标注**：原“Selected source”只是在所有素材中与混合输入最接近的一张，不代表生产选择器挑中的best，也不是完整模型输入；不得据此判断“像不像那张照片”。best-match 的输入为80%归一化gallery +20%best单张，再归一化。
- v2 的 `similarity_to_mixed_source_before/after` 明确指与实际混合输入特征的余弦相似度，不是对所展示照片的相似度，也不是独立识别器/未参与输入的素材评测。旧 `source_similarity_*` 数值应按此语义理解。
- `aligned_output_mae` 替代容易误读的 `raw_crop_mae`；脸框右/下边界改为与生产一致的向上取整。指标尾数变化源自测量修正，不是输出图片变化。
- 当前仍不以ArcFace诊断分强制通过换脸，也不减弱遮挡/眼镜保护、提高默认值或修改旧任务。新的 [评测契约](swap-benchmarks.md) 区分预期身份、几何关联、未标注和漏检分母。

实际 API 另建默认90%验证任务 `pw_task_db9874dc5a`，终态 PARTIAL、有结果2张/失败2张；逐图检测数为4/3/2/1，换入4/3/0/0。第3/4图的no_visible_change均保留约1.0的目标匹配分和完整source/mask诊断，没有再把它误报成“检测不到人”。浏览器在现有 http://127.0.0.1:3000 刷新后，已验证失败图真实计数、最佳参考#7、80/20系数、mask证据、失败无下载、成功可查看，以及查看前后useStudioStore状态不变；截图 `outputs/diagnostics/history-ui-failed.png`。任务历史轻量缓存会正常更新，这不等于回灌编辑工作区。

### 原始实测：输入与复现

这是四张私人图片的一次限定样本诊断，不是通用质量保证。任务为 `pw_task_7e24362539`；SQLite request JSON 的 SHA-256 为 `0950cbfd6ba47df056c85a2366b5c88eefaeeebf599ac2ca90d6f34274a007a3`。

执行 `uv run python -m scripts.diagnose_batch_strength pw_task_7e24362539 --output outputs/diagnostics/pw_task_7e24362539-strength`。脚本只读原任务，使用 CPUExecutionProvider、不调用外部付费模型；复用原素材、人物映射、0.35 匹配阈值、InSwapper、保护遮罩、边界色彩参数，仅比较 0.72 / 0.9 / 0.95。输出目录包含 report.json、完整对照图片及逐脸拼图；私人图片不加入版本控制。拼图中的 face 编号是该图从左到右的检测序号，不是跨图的任务人物编号。

解码后 BGR 像素 SHA-256：

1. `f2e2feb5aff9f45612e4202e4858f6c7445090ec7b0ddd5e6fd43aeed9737da1`
2. `5f9880eeb7c8a75d34adc9eafc4da0c17f3c705a996afdae0184d176249a17e4`
3. `d90b82f81312a649d671817e3e6c387e417f9449f199118219aaca6d2a70f558`
4. `e8823054a7d40a09519b354f54c77a170bdd3ba8bfa25f171c82f17c5282ce68`

### 结果

| 输入顺序 | 检测脸数 | 72% 通过脸数 | 90% 通过脸数 | 95% 通过脸数 |
| --- | ---: | ---: | ---: | ---: |
| 1 | 4 | 4 | 4 | 4 |
| 2 | 3 | 2 | 3 | 3 |
| 3 | 2 | 0 | 0 | 1 |
| 4 | 1 | 0 | 0 | 1 |

- 原始失败不是没有检测到脸：第 3 张的任务人物 #3 / #6 和第 4 张的 #1 均匹配到目标，其中作为代表图的配对相似度为 1.0。失败来自融合后变化不足。任务映射有六个人，不意味着每张照片都能检测到六个人；其余未达阈值的映射不能通过调强度解决。
- 第 1 张在 72% 下，四张脸的脸框平均实际 alpha 为 0.217–0.392，而不是全脸 72%。模型/边界色彩后的对齐裁剪平均像素差为 7.60–11.99，最终脸框像素差仅 3.67–4.63。保护交集和融合共同削弱了可见变化；不能把全部衰减归因于强度参数。
- 第 1 张四个换入的素材 ArcFace 余弦相似度（检测序号 2/1/3/4）从 72% 时的 0.194/0.482/0.470/0.525，提高到 90% 时的 0.260/0.611/0.585/0.604。该指标只支持换入特征增强，不是自然度评分，也不是人的身份鉴定。
- 第 3 张检测脸 #1 被头发和物体遮挡，XSeg mask 在对齐裁剪上的均值仅 0.076，最终脸框平均 alpha 在 72% 时约 0.050、95% 时约 0.066。像素差从 0.77 到 1.01，但素材相似度反而下降。它在所有档位都应继续跳过，不扩大遮罩强行替换。
- 第 3 张另一张侧脸的像素差从 2.31 到 3.04，第 4 张侧脸从 2.34 到 3.07（72% → 95%）。因此 95% 时两图各保留一个换脸结果；只刚过原有门槛，不能宣称整体身份或自然度达标。
- 第 3/4 张尝试的脸 yaw 比值均为 -1，侧脸安全缩放生效。95% 请求强度在该分支还要乘 0.65/0.72，而不是把所有皮肤像素以 95% 替换。
- 眼镜保护覆盖镜框和镜片，因此眼部变化受限。没有为了指标更高删除眼镜、遮挡、密集轮廓或极端侧脸保护。

### 产品调整与边界

常规默认强度从 0.72 改为 0.9，工作台上限从 0.90 开放到已有 API 上限 0.95；用户可以退回 0.72。旧请求和原任务不被改写。这批想要更明显的结果可明确选择 0.95；结果仍需人工对照，尤其是侧脸与眼镜区域。

实际 API 已另建 95% 任务 `pw_task_5e6c58e467` 验证：四图都有正式结果，换脸人数分别为 4/3/1/1，图片失败数为 0。任务仍为 PARTIAL（未匹配或被遮挡的人物继续跳过），不是所有请求人物全部成功；原任务 `pw_task_7e24362539` 保持不变。

原始v1报告采用下取整脸框，而生产右/下边界向上取整，数值存在极小边缘差；v2已统一为生产边界。计数始终直接取生产 SwapResult。v1的 raw_crop_mae 实为边界调色后的对齐输出（不是未经后处理的模型张量），v2已更名 aligned_output_mae。

新失败记录保留真实检测数、人物结果和模型路由，但零换脸仍 FAILED、不发布图片；旧失败记录不能凭空恢复。详见 [本地生产路径](facefusion-local-swap.md)、[任务历史契约](task-history.md)。回归见 tests/test_facefusion_pipeline.py、tests/test_strength_contract.py、tests/test_task_swap_outcome.py。
