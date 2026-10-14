# Refinement evidence, diagnosis and prompt boundary

本文说明精修轮次的分析部分：模型读取哪些证据、人物如何绑定、诊断能表达什么，以及不可信文本的边界。

会话与状态见 [ai-refinement.md](ai-refinement.md)，生成与复评见 [refinement-generation.md](refinement-generation.md)，提示词版本及官方依据见 [refinement-prompts.md](refinement-prompts.md)。实现位于 `core/refinement/conversation.py`、`contracts.py`、`prompts.py`、`evidence.py`。

## Evidence roles

证据标签区分“场景依据”“待修改图”和“目标身份参考”，避免把原图人物当作换脸后的目标身份：

- `ORIGINAL`（原图）：锚定姿态、人数、构图、场景和遮挡关系，**不锚定目标面部身份**。按要求替换身份本身不构成缺陷。
- `CURRENT`（当前图）：选中的完整待评估图片；若用户授权，也是本轮修改基图。
- `REFERENCE_BOARD_PERSON_N`（人物 N 的参考板）：只包含稳定 `person_id=N` 的目标身份参考。顶部和每个格子都标同一人物 ID，完整参考图按比例放入，不做人脸检测或裁剪。列表位置、参考板到达顺序、新检测的左右顺序都不重绑 ID；缺参考须明确标注。
- 复评增加 `BEFORE`（本轮显式基图）与 `CANDIDATE`（候选）：读取原图、基图、候选和同一批参考板。参考板只供分析/复评，绝不送给生成器。

**稳定定位：**创建快照记录原图检测的固定从左到右人脸序号。原图尺寸已知时，还记录脸框中心距左上角的百分比；缺尺寸时省略坐标。定位文字用于区分稳定人物 ID 与单张图片的人脸序号，不重新检测或排列候选。已有会话沿用冻结定位，可通过讨论澄清。

**图像预算：**证据图片排在最终文字问题之前。原图、当前图、基图和候选以 JPEG 概览提交，最长边为 3200 像素（maximum edge of 3200 pixels；实现常量 `VISION_MAX_SIDE`），较小输入保持原尺寸。

这是应用预算，不是供应商能力声明。上游仍可能限制采样细节或使请求失败；失败应明确返回，不静默丢弃某个人的证据。

参考板使用独立布局：最多 24 人，每人最多八张参考，每格 320×352，至多两列。解码数组必须是非空的 uint8 BGR（三通道蓝/绿/红顺序），且符合共享像素上限；累计解码预算见 [refinement-session-api.md](refinement-session-api.md)。

## Diagnosis

**调用与结果：**

`analyze(original, current, people, references, history, user_message, *, previous_diagnosis=None) -> Diagnosis`

该方法使用 `vision_alias("refinement")` 调用 `AIGateway.structured_vision_completion`，只获取一次结构化响应，禁用文本修复，详见 [错误与重试](refinement-generation.md#errors-and-retries)。它不生成或接受图片；用户文字、图片中文字都不改变后端模式。

`Diagnosis`（诊断）联合考察身份外观、解剖结构、遮挡、融合、光影、纹理和构图。消息、`issues[]`、`people[]`、`edit_plan` 与不确定项的可执行定义在 `core/refinement/contracts.py`。

**问题和人物判断：**

- `issue.category` 仅有 `identity`、`anatomy`、`occlusion`、`blending`、`lighting`、`texture`、`composition`，不含数字分数。位置、描述、证据、建议至少分别为 3/12/12/8 字符；低置信度必须说明不确定原因。工具调用、分数、接受决定等额外字段校验失败。
- `identity_status` 为 `match`（外观相符）、`mismatch`（外观不符）、`uncertain`（证据不足）或 `unavailable`（缺参考）。缺参考必须用 `unavailable` 并解释；遮挡、视角不兼容、小脸或定位歧义可能需要 `uncertain`。这只是视觉比较，不是身份认证。
- `mapping_confident` 判断是否对应到给定人物，独立于外观相似度。

**结构一致性校验：**初始 JSON schema（结构校验契约）同时要求：

- 不确定或缺参考的身份判断附非空说明。
- `mapping_uncertain` 等于是否存在不确定的人物映射。
- `needs_edit` 与 `edit_plan.changes/instructions` 一致。
- 人物评估 ID 完整且唯一。缺失、多余、重复 ID 或无参考的身份断言均校验失败。

这些规则不放宽校验，也不开启额外修复调用；结构有效不证明视觉判断准确。

**无缺陷、用户偏好与摘要：**

提示词不预设缺陷，也无分数门槛。干净图片可返回 `needs_edit=false`、`issues=[]`、`edit_plan.changes=[]`。小脸、侧脸、遮挡或可比较特征太少时表达不确定，不编造结构或身份问题。

用户提出具体整图改动时，即使没有缺陷也可形成计划；用户要求与模型观察分开。`needs_edit` 只是建议，不授权付费生成。人物对应有歧义时，生成前必须澄清。

摘要为 1–3 个短句，不重复结构化问题列表。自然侧脸透视与可见结构错误分开；眼镜、手、小物体穿过脸部时对照原图遮挡证据。隐藏特征不补造，不确定观察不写成确定的修改指令。这些是提示词要求，不是模型行为保证。

**同一基图的诊断接续：**

服务端单独提供已显式确认的诊断；直接 API 命令没有绑定时，提供同一基图最近完成的诊断。它以结构化 `previous_diagnosis` 传入，不受历史文字截断影响；调用前由核心校验结构和人物绑定。

提示词将先前发现视为待复核假设，再结合图片和人工反馈形成计划。当前视觉偏好优先于冲突的建议；省略或修正建议要解释，仍有歧义则请求澄清。

生成器看不到参考板，因此计划须把参考观察转成完整、自足的视觉要求。讨论说明留在 `assistant_message`，避免与可执行计划矛盾。生成收到计划、问题证据和原始当前消息；问题不是另一份必须全部执行的清单。回归见 `tests/test_refinement_conversation.py`，精确基图选择见 `tests/test_refinement_sessions.py`。

## Untrusted data and bounded context

图片（包括 OCR，即图片文字识别结果）、人物描述、先前助手文字和用户文字都是不可信任务数据。用户可以要求视觉修改，但这些内容不改变系统角色、工具权限、凭据、执行模式或发布规则。

变量以 JSON 编码放进转义的数据封套，不插入系统指令；首次调用就附完整可信 schema。

**历史与当前要求使用不同上限：**

- 历史只取最近八条，仅保留 user/assistant 的字符串内容；每条最多 2,000 字符，合计最多 12,000。system/tool 历史及带图片的消息对象不重放。
- 当前消息最多 4,000 字符，人物定位最多 300 字符。超限在任何模型调用前返回输入错误，不截断当前要求。
- 当前输入上限与最多 24 人的限制统一定义在 `core/refinement/contracts.py`，由 API 和核心共用；只有历史上下文会截断。

这些措施限定权限和输入规模，不宣称提示词本身可以抵抗所有对抗文本。硬边界来自分离的方法、无工具接口、类型化 schema 和生成输入的构造方式。
