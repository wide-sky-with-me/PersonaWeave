# AI model catalog

本页说明 AI 模型的配置与路由契约，不是可用服务清单。唯一可执行事实源是 [`config/ai_gateway.yaml`](../config/ai_gateway.yaml)：[`configuration.py`](../core/ai/configuration.py) 为 `AIGateway` 校验配置，`GET /api/v1/ai/models` 向前端公开不含凭据的图像模型元数据。文档中的别名必须存在于该 YAML。

VLM（视觉语言模型）负责图片分析；图像编辑模型负责生成候选。配置就绪只证明本地设置有效，不证明上游可访问、已付费或画质达标。

## Native deployment configuration

**配置分工。** `model_list` 是视觉与图像编辑共用的原生 LiteLLM Router 部署列表。`model_name` 是应用的稳定别名；`litellm_params.model` 指定实际 provider/model ID，可通过 `os.environ/NAME` 读取环境变量。同一别名可对应多个部署。

`image_models` 每个图像别名只保留一条应用元数据：`model_name`、`display_name`、`description`、`generation`。它引用同一部署列表，不含 `provider`、`params` 或第二套连接配置。

**调用与边界。** 视觉走 `Router.acompletion`，图像编辑走 `Router.aimage_edit`；供应商协议由 LiteLLM 处理，没有手写供应商 HTTP 适配器或私有协议回退。项目安装的 LiteLLM 1.98.0 在 Router 实例上动态注册图像编辑方法，该方法未必是类属性。依据：[Router 文档](https://docs.litellm.ai/docs/routing)、[图像编辑文档](https://docs.litellm.ai/docs/image_edits)。

原生支持必须按端点核实（endpoint-specific）：任意 base URL 或可用的 chat 服务均不足以证明支持图像编辑。使用前确认所选 provider/model、编辑端点与安装版本兼容。

**传输兼容。** LiteLLM 1.98.0 会丢弃调用级图像编辑 `client`。网关因此通过原生 `router.factory_function(..., call_type="aimage_edit")` 注册仅负责传输的包装：每次尝试创建禁用重定向、带响应钩子的 `AsyncHTTPHandler`，调用 `litellm.aimage_edit` 后关闭 handler。Router 的部署选择、重试和 fallback（失败后切换备用别名）仍由原生机制负责，不修改全局 client 或模型对象。上游保留 client 且传输回归通过后，方可移除此兼容注册。

## VLM aliases

下表是默认视觉别名及用途；后端模型由环境变量决定。

| Alias | Backend model | Current use |
| --- | --- | --- |
| `personaweave-vision-default` | `os.environ/AI_VISION_MODEL` | 默认视觉、身份仲裁、精修诊断与辅助复评 |

## VLM role routing

业务代码按语义角色选模型，不内嵌模型别名：

| Role | Configured alias |
| --- | --- |
| `default` | `personaweave-vision-default` |
| `identity_arbitration` | `personaweave-vision-default` |
| `refinement` | `personaweave-vision-default` |

`AIGateway` 校验这三个角色均引用 `model_list` 中的视觉部署。用户可为不同角色配置不同视觉别名；路由不改变本地身份判断以 ArcFace 特征余弦相似度为先的规则。

## Image-edit aliases

| Alias | Display name | Backend model | Provider |
| --- | --- | --- | --- |
| `personaweave-refine-default` | 默认图像编辑模型 | `os.environ/AI_IMAGE_MODEL` | `litellm` |

Default image alias: `personaweave-refine-default`.

该默认图像别名与视觉别名组成仓库的两项通用目录，不是供应商模型清单。`GET /api/v1/ai/models` 保持公开响应结构：`provider` 为 `litellm`，`model` 为实际配置的模型 ID（多个部署可用逗号分隔），不公开连接 URL 或凭据。前端将 `default_alias` 放首位，其余按配置顺序排列；无需前端服务管理界面或独立服务。

## Environment configuration

**操作。** 每种能力独立设置模型、地址和凭据，可使用不同服务：

| Capability | Model | Base URL | API key |
| --- | --- | --- | --- |
| Vision | `AI_VISION_MODEL` | `AI_VISION_API_BASE` | `AI_VISION_API_KEY` |
| Image edit | `AI_IMAGE_MODEL` | `AI_IMAGE_API_BASE` | `AI_IMAGE_API_KEY` |

YAML 通过 `os.environ/` 引用这六项。[`.env.example`](../.env.example) 的 `openai/gpt-4o` 与 `openai/gpt-image-1` 只是可替换示例，不承诺兼容性、账号权限、价格或可用性。URL 和 Key 在本机私下填写，不提交凭据；额外部署可引用自定义变量，不必改变业务别名。

OpenAI 兼容端点的两个模型设置都应显式使用 `openai/<actual-model-id>`。LiteLLM 会在发送前去掉协议前缀；裸写未被本地注册表识别的新模型名可能导致 provider 检测失败。项目不自动猜前缀，也不为此增加模型请求。

**结果与排错。** 启动只校验配置和环境引用非空，不发付费请求。缺失或无效配置使 `ai_refinement_ready=false`：模型目录和新的精修轮次命令返回 503，本地换脸、已保存会话查看及接受仍可用；可选 AI 失败不阻止 API 启动。就绪不等于上游鉴权或端点可用，未知价格也不等于免费，默认目录没有虚构零成本定价。

Router 初始化失败单独报告为本地 AI 路由配置错误，与上游 HTTP 错误区分，不暴露原始异常体。独立的 `/api/v1/vlm/arbitrate` 保持辅助响应契约：网关/调用失败可返回 HTTP 200 且 `is_success=false`，构造错误返回 400，均不代表身份匹配成功。实现与回归：[`gateway.py`](../core/ai/gateway.py)、[`types.py`](../core/ai/types.py)、[`test_litellm_gateway.py`](../tests/test_litellm_gateway.py)。

## Generation policy

**结论。** 每个图像别名的 `image_models[].generation` 必须且只能声明下列一种尺寸策略；别名背后的所有部署都须支持它。不得凭模型名推断限制或静默转换不支持的参数。

1. **服务默认尺寸：** `{size: "auto"}`，把尺寸交给服务选择；does not promise 4K，也不承诺固定分辨率或与源画布尺寸完全一致。
2. **离散尺寸：** `{sizes: ["1024x1024", "1536x1024", "1024x1536"]}`，选择宽高比最接近画布的项。数字仅展示语法，须填写所选编辑端点实际支持的尺寸。
3. **动态尺寸范围：** `{max_side, min_side, grid, max_pixels?, delimiter: "x"}`。必需边界为有效正数，`min_side <= max_side`；`grid` 控制尺寸对齐，可选 `max_pixels` 限制总像素。仅在服务支持这些尺寸时声明；缺失、混用或无效策略使配置校验失败。

[`generation_parameters()`](../core/refinement/image_ops.py) 按策略生成参数，不猜 provider 名。候选规范化遵守整图输出契约：请求尺寸不证明原生细节，不通过拼接保留未选区域，也不跳过人工检查。

## Routing, retries and fallbacks

**超时。** Default gateway request timeout: `300` seconds（5 分钟），来自 `router_settings.timeout`；图像部署覆盖为 `600` 秒。精修分析/复评另有 330 秒阶段截止时间，为视觉请求之外的证据编码与校验留出余量。上游可能更早超时，调大应用超时不保证响应成功。

**重试费用。** 默认 `router_settings` 为 `num_retries: 0`、`fallbacks: []`，图像部署的 `litellm_params.max_retries` 为 `0`。自定义配置省略这些项时，加载器补 `num_retries=0`，每个部署补 `max_retries=0`；用户显式值保留。这避免默认自动重复付费生成，不保证供应商计费方式。

用户可显式配置重试和同能力 fallback，额外尝试可能额外计费。同一别名下的部署必须能力一致；视觉与图像编辑不得互为 fallback，图像备用别名须有完全一致的尺寸元数据（identical `generation`），避免静默改变尺寸语义。

**结构化响应修复。** 它与 Router 重试独立。`AIGateway.structured_vision_completion(..., max_text_repairs=2)` 默认至多增加两次纯文本 JSON 修复调用，适用于身份仲裁等调用方；不带图片仍可能计费。**对话式精修分析/复评显式传 `max_text_repairs=0`**，初始 system prompt 提供完整 JSON schema，响应无效就报错，不自动修复。预算只在本地校验，不发给 provider；即使精修修复预算为零，用户配置的 Router 重试/fallback 仍可产生额外费用。

## Migration

以下是兼容配置与已有数据的操作边界，不自动转换或重发请求。

1. 保留本机 `.env` 和自定义 YAML，不覆盖凭据、运行数据、任务历史或暂存候选。只把 `.env.example` 当模板，私下填写六项 `AI_VISION_*` / `AI_IMAGE_*`。`DMXAPI_API_BASE` / `DMXAPI_API_KEY` 不兼容（incompatible），没有自动转换。
2. 供应商专属目录须整理为两种能力共用的原生 `model_list`：实际模型/连接设置放 `litellm_params`，`image_models` 只留别名、展示和生成元数据。图像 `provider`/`params` 格式校验失败，不作为第二套 schema。
3. 显式选择相关 LiteLLM 端点支持的模型，设置 `vision_roles`、`default_image_alias`、尺寸策略及可选同能力 fallback。停用的别名没有静默映射（not silently mapped）；重试保持零，除非用户接受重复费用。
4. 重启后端加载配置，先检查本地就绪和不含凭据的模型目录。就绪或离线测试不等于付费上游冒烟验证；真实生成须由用户另行明确触发。
5. 单任务兼容生成/重试路由返回 410，不改选其他模型。既有兼容候选接受仍校验 `expected_revision` 和 `candidate_digest`，只发布已验证的同一暂存字节，不重路由或重生成。新编辑显式创建使用支持别名和已验证证据的会话，并校验所选版本；生成只接收整图与文字，接受只由人工触发。

## Maintenance rule

增删、重命名或改变模型时，同一变更完成：

1. 更新 `config/ai_gateway.yaml` 的原生 `model_list`、语义 `vision_roles`、图像元数据/尺寸策略与环境引用。
2. 同步本页别名和角色表，不虚构供应商专属默认清单。
3. 用上游一手文档及离线集成测试核实端点/尺寸兼容性；真实调用证据单独标注，不把离线通过当作在线可用。
4. 运行 `tests/test_documentation_consistency.py` 及网关/API 测试。

仓库权威层级见 [documentation-governance.md](./documentation-governance.md)。
