# Conversational refinement prompt contracts

本文记录对话式精修提示词的版本、官方依据和离线回归。实现位于 `core/refinement/conversation.py`、`contracts.py`、`prompts.py`、`evidence.py`。

提示词版本为 `conversation-refinement-v5`。Prompt version: `conversation-refinement-v5`.

行为以 [refinement-analysis.md](refinement-analysis.md)（证据、诊断、不可信数据）、[refinement-generation.md](refinement-generation.md)（生成、复评）和权威契约 [ai-refinement.md](ai-refinement.md) 为准。本页不定义另一套接受规则或模型目录。

## Official references

**来源使用范围：**只采用实际获取的材料。标注“仅摘要”的来源，只依据官方搜索摘录，不据此声称已读取页面全文。

1. [Anthropic — Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)，仅摘要：清晰的任务说明、明确的上下文与输出约束、结构化分隔变量数据、多样示例。落地为独立系统/数据部分，以及具体缺陷、不确定、无需编辑的示例。
2. [Anthropic — Vision](https://platform.claude.com/docs/en/build-with-claude/vision)，仅摘要：图片放在问题之前；小图、低质量图、定位和计数有局限。完整照片以 JPEG 概览提交，最长边为 3200 像素（maximum edge of 3200 pixels），较小输入不放大；有界参考板保留自身布局。
3. [OpenAI Cookbook — Gpt-image-1.5 Prompting Guide](https://cookbook.openai.com/examples/multimodal/image-gen-1.5-prompting_guide)，读取自 [官方 notebook 源码](https://raw.githubusercontent.com/openai/openai-cookbook/main/examples/multimodal/image-gen-1.5-prompting_guide.ipynb)：为目标/细节/约束加标签，具体描述视觉变化，明确“修改/保留”清单，并在迭代编辑中重申不变量。采用这些方法，不引申身份准确率、速度、供应商、可用性或分辨率保证。

**视觉证据的限制：**3200 像素预算让小脸获得更多输入像素，但不控制上游采样，也不是供应商分辨率上限声明。小脸、侧脸和遮挡需要表达不确定，不补造细节。

稳定人物 ID 是应用绑定。视觉相似度不是现实身份认证；上游未提供比较或比较不可用时，不转成身份结论。上述约束属于提示词和输入组织要求，不是对模型判断准确性的承诺。

**结果如何解读：**测试验证离线契约与请求构造。[五人照片两轮实测](refinement-live-test-20260911.md)验证了请求组成和版本接续，但两个候选都有可见质量退步；第二轮复评 HTTP 408 失败，候选仍被保留。它不证明提示词对其他照片或模型配置有效。

## Offline regression evidence

[tests/test_refinement_conversation.py](../tests/test_refinement_conversation.py)覆盖 [refinement-generation.md 的证据章节](refinement-generation.md#evidence)所列提示词与输入契约。

离线运行命令：`uv --no-cache run --no-sync pytest tests/test_refinement_conversation.py`。通过只说明受测契约成立，不替代真实照片质量判断或上游可用性检查。
