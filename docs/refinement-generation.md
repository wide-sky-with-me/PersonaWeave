# Whole-image generation and advisory review

本文说明精修轮次如何生成完整候选图，以及如何提供不自动接受的复评意见。实现为 `core/refinement/conversation.py` 经 `AIGateway` 调用模型。

证据与诊断见 [refinement-analysis.md](refinement-analysis.md)，会话语义见 [ai-refinement.md](ai-refinement.md)，发布见 [refinement-session-api.md](refinement-session-api.md)。

## Generation

**生成前的分析：**

提供确认分析 ID 时，使用对应的诊断；否则使用显式 `base_version_id`（修改基图版本）最近完成的诊断，其轮次状态为 `completed` 或 `review_required`。诊断以完整 `previous_diagnosis` 单独传入，不混进有上限的历史文字。

分析对照图片复核这些假设，再把仍有证据支持的发现与当前人工反馈合成一份计划。当前视觉偏好优先于冲突建议；用户收窄范围时，不要求逐一处理所有问题；其他版本的诊断不带入。这是生成轮次唯一一次分析调用。

**唯一生成输入：**

`generate(current, people, diagnosis, user_message, model_alias=None) -> (candidate, metadata)`

计划必须可执行，人物对应必须明确；核心校验会拦下映射不确定或无需修改的诊断。满足条件后，调用 `AIGateway.edit_image` **恰好一次，只传一张原分辨率、无损编码的完整当前图和文字**。

文字包含当前问题证据、修改要求、语义保留意图及稳定的人物可见定位。原图、参考板、来源照片、人脸裁剪、遮罩、关键点、标注图或历史版本都不附带。因此分析须把参考观察转为自足的视觉指令，而不是要求生成器查看未提供的参考板。

**整图编辑的范围：**

为完成指定意图，可连贯重绘光影、衣服、头发、遮挡边缘和背景。保留意图用来减少无关漂移，不是仅改脸的范围闸门，也不承诺像素逐一相等。

单张图片加文字未必修复严重身份偏差；评估模型所见的参考不交给生成器，因此不承诺身份恢复。

**输出与保存：**

尺寸参数只来自网关声明的生成策略，经 `generation_parameters()` 和共享 `image_ops` 工具选择，不按供应商名称猜测。

供应商输出先解码，再规范化方向、颜色和画布尺寸；画布规范化仅做整图中心裁剪/缩放。不重新配准关键点，不加遮罩、不局部回填、不执行 `apply_candidate`，也不接受图片。

规范化后的图片在复评前保存为不可变候选。元数据记录请求别名、实际模型/供应商、用量、请求 ID、提示词版本及文字、输入图片数和规范化审计。工作台展示供应商输出与画布尺寸，并说明放大输出不等于恢复细节。

## Advisory review

`review(original, before, candidate, people, references, diagnosis, user_message) -> CandidateReview`

复评读取原图、本轮基图、候选和同一参考板。`CandidateReview`（候选复评）给出 `summary`（摘要）、`improved`（改善）、`remaining`（残留问题）、`new_issues`（新增问题）、`uncertainties`（不确定项）及逐人判断；没有分数、接受标记或再次生成决定。

先前诊断是待复核假设，不是事实证明。按要求修改非脸部内容不自动算退步；干净候选可以没有残留或新增问题。

**失败与人工判断：**复评不生成、不接受、不丢弃图片；核心方法也不负责文件发布或自主循环。复评失败向轮次编排层传播，后者保留已生成候选并记录 `review_error`，供人判断。

## Errors and retries

分析和复评各调用一次结构化网关，首次请求就带完整 JSON schema（含跨字段一致性规则），并设置 `max_text_repairs=0`。无效输出直接作为错误返回，不追加纯文字修复调用。

对外错误只包含脱敏分类：可获取时的上游 HTTP 状态、超时或白名单内的 schema 错误代码。供应商响应正文、模型输出值、校验上下文和额外字段名都不对外展示。

这个限制约束应用调用次数，不等于网络尝试次数。用户配置的 LiteLLM Router 重试或 fallback（失败后的备用路由）仍可能增加上游尝试及费用；身份仲裁等其他调用方沿用各自说明的默认修复预算。等待上限见 [ai-refinement.md](ai-refinement.md#waiting-limits)。

离线测试不证明付费服务可用，也不证明真实图片质量。

## Evidence

`tests/test_refinement_conversation.py` 的回归范围包括：

- 人物 ID/参考板绑定与标签，干净图片无需编辑，侧脸/遮挡不确定，缺参考和虚构 ID。
- 文本权限边界与输入上限；单张原分辨率生成；整幅画布可变且无回填；声明式尺寸选择；映射与无修改校验。
- 原图/基图/候选/参考复评，复评失败保留候选，以及 JSON 契约。

精确基图的诊断选择由 `tests/test_refinement_sessions.py` 覆盖。离线运行命令：`uv --no-cache run --no-sync pytest tests/test_refinement_conversation.py`。
