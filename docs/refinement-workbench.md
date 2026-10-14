# 精修工作台与编辑器恢复

本文说明精修工作台的操作、失败提示、编辑器恢复，以及已接受结果的展示和导出。实现位于 `web/src/features/refinement/`，行为由就近测试覆盖。

会话语义见 [ai-refinement.md](ai-refinement.md)，接口与存储见 [refinement-session-api.md](refinement-session-api.md)，工程与历史契约见 [task-history.md](task-history.md)。

## 交互流程

工作台顺序为 **检查问题 → 确认分析 → 补充意见与修改 → 对比并继续下一轮**。打开或创建会话不调用模型。

**开始前：**准备区的 AI 接续选项仅在服务端健康接口确认精修就绪后启用。失去就绪会清除选中状态，重新就绪不自动勾选。HTTP IP 浏览器使用强随机请求 ID，幂等（重复同一命令不重复执行）语义与安全上下文一致。实现与原因见 [浏览器兼容决策](../.agents/notes/frontend/20261014-verify-project-assets-without-secure-context.md)。

1. 提交 `discuss` 检查。发现按人物与五官、遮挡与融合、光影与质感、构图与场景分组，可折叠查看具体位置、观察、证据和建议。不确定项、人物对照与修改计划各自可展开。
2. 显式确认**这一基图**的已完成讨论后，意见输入框和生成才可用。意见可修改或收窄建议；干净诊断需要新的视觉要求才能生成。人物对应不明确时只开放澄清与讨论，更新分析须重新确认。
3. 用“以此版继续”显式更换基图。这会清除确认和旧意见草稿；再次生成前须先分析新基图。
4. 对比候选后由人决定下一步。生成结果突出建议性复评，输入计划和先前分析折叠。迭代上限为 200 个轮次。

**查看不等于修改或接受：**画布默认完整展示所选版本，原图/基线分屏须显式选择，避免目标被原图一半遮住。查看、选基图、接受是独立动作；生成不自动推进下一轮基图或最终版。

**人物定位与参考：**界面使用原图检测时冻结的左右人脸序号；原图尺寸已知时，另显示脸框中心距左上角的百分比。缺尺寸就省略坐标，不编造。参考侧栏显示人物 ID 和缩略图，较长的原图定位默认折叠。

定位实现及回归见 [会话配方](../web/src/features/refinement/sessionRecipe.ts)、[配方测试](../web/src/features/refinement/sessionRecipe.test.ts)。生成命令的 `diagnosis_turn_id`（已确认分析 ID）校验见 [ai-refinement.md](ai-refinement.md)；组件见 [工作台](../web/src/features/refinement/components/SessionWorkbench.tsx)、[输入区](../web/src/features/refinement/components/SessionComposer.tsx)，交互回归见 [SessionWorkbench.test.tsx](../web/src/features/refinement/components/SessionWorkbench.test.tsx)。

## 失败提示

命令校验失败、轮次失败、候选复评失败都用需确认的对话框展示；输入校验失败时保留服务端错误详情。

复评失败仍保留候选供人查看。关闭对话框不重试，也不生成图片。恢复会话时，历史失败保留在对话中，不重放旧弹窗；新观察到的轮次失败仍需确认。

## 图片读取

参考图和版本缩略图懒加载，即在需要显示时读取。临时读取失败后，可显式选择“仅重新加载图片”；此动作不创建轮次。

接受成功后，关联编辑器等待工程保存。保存失败会提示“服务端接受已成功，但工程仍需保存”；接受状态仍以会话服务端为准。

## 编辑器恢复

**保存哪些状态：**完整 Studio 工程连同准备区，保存每个会话的查看/基图版本、已确认分析、未提交意见，以及模型、对比、缩放和分栏选择。同标签页刷新也保留轻量选择；状态不确定的待提交命令保留原幂等键，绝不自动重放。

**恢复的边界：**打开会话与显式恢复关联工程是两件事。恢复不改变服务端轮次、接受或稳定人物绑定。没有完整工程的旧会话只恢复已有证据和显式映射，并提示缺失项。归属、存储、冲突和删除见 [studio-projects.md](studio-projects.md)。

**批任务如何形成配方：**建会话只用每张图实际的人物-人脸分配。未分配角色不绑到猜测位置，也不阻止已确认人物进入精修；成功结果缺分配则失败。来源枚举与已分配结果统一来自 [task provenance](../api/tasks/provenance.py)（任务来源证据）；精修接续和工程恢复分别编排。

工程恢复时，将服务端保留的原图与本地结果，分别与编辑器中对应的画布比对：每一对须摘要相同，或解码像素完全一致。两项都通过后，才用保留参考与分配结果重建缺失配方，不重跑换脸。运行中输入连同任务 ID 保存；只有显式关联的工作区，才在刷新后结果到达时接续。

实现见 `api/refinement/batch.py`、`api/projects/recovery.py`、`web/src/hooks/useBatchPipeline.ts`；回归见 `tests/test_assignment_provenance.py`、`tests/test_batch_refinement.py`、`tests/test_projects.py`。

## 已接受结果投影与导出

**最终结果如何进入编辑器：**服务端确认的接受投影到关联目标的 `refinedUrl`，原图和 `processedUrl`（本地初稿）保持不变。准备画布、对比、胶片条和当前图下载使用这一最终结果。

全局观察器即使不在精修视图也刷新显式关联的会话，因此恢复工程后仍会显示已接受结果。会话 ID 与目标 ID 必须同时匹配；重新处理的目标不适用旧关联，旧 revision（状态修订号）不覆盖新的接受。查看候选或无关历史不改变编辑器结果。

已保存工程保留独立图片副本和版本/revision 投影；这些元数据不是接受授权。实现与回归见 `acceptedResults.ts`、`useAcceptedResults.ts`、`SessionWorkbench.test.tsx`、`acceptedResults.test.ts`。

**删除后的展示：**确认删除清除匹配会话的已接受 URL 和元数据，并持久化“结果不可用”标记。画布明确标出剩下的本地初稿，不将它下载为已接受最终图。工程冻结的 data URI（内嵌图片数据地址）副本仍保留。

**单图与 ZIP 导出：**先核对关联会话，使用其当前已接受发布。只有确认 `404/410` 才可回退到冻结图；网络错误、服务端失败或会话/目标不匹配均明确失败，不静默导出过期接受结果。

实现见 `sessionCache.ts`、`acceptedResults.ts`、`workspaceExport.ts`；回归见 [缓存清理](../web/src/features/refinement/sessionCache.test.ts)、[最终图导出](../web/src/utils/workspaceExport.test.ts)、[接受投影](../web/src/features/refinement/hooks/useAcceptedResults.test.tsx)。

**工作台和历史中的删除：**两处使用同一带 revision 校验的删除。历史支持选择和批量删除，并分别显示结果。只有删除成功，或重试确认 `404` 后，记录和缓存详情才消失；运行中记录不可选，过期或失败记录保留并刷新。

删除会清除该会话打开的工作区、草稿、待提交命令及匹配的轻量 `refinementSessionId` 关联；源图、本地输出、映射和遮罩保持不变。SSE（服务端事件流）订阅收到 `deleted` 后关闭，向已删除记录重连返回 `404`。
