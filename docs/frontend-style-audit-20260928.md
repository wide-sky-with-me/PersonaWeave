# PersonaWeave Web 前端样式体系分级审阅报告

> **修复状态（2026-09-28 补记）**：本报告列出的 A 级缺陷与对比度问题已在同日落地，变更记录见 `CHANGELOG.md` 的 `## 2026-09-28` 段。
>
> **已修复**：`slide-in-from-top-2` 零产物（补写 keyframes）；`.refinement-workbench` 补丁的特异性压制（改用 `:where()` 归零，产物 12 → 2 条规则）；`boxShadow` 的 `primary`/`cyan` 被颜色插件劫持（改名 `glow-primary`/`glow-accent`）；`.seg-item-primary` / `.chip-primary` / `.icon-badge-primary` / `.val-badge-primary` 的 2.95:1（新增 `primary-soft`）；`.chip-err` 的 3.89:1（新增 `err-soft`）；`text-zinc-500` 62 处升 `text-zinc-400`；3 处任意值画布底色收敛为 `bg-canvas`；Logo 渐变 / 人脸标记 / 对比分割线的默认调色板裸用改 token；9 处内联 `minHeight: 44` 改 `min-h-touch`；Framer Motion 的两处 tween 违规改 spring；`chip-sm` 死类；`.btn-accent` 删除。
>
> **未执行（有意保留，附理由）**：§10 第二步的「全面去掉 `@apply` 链」——执行中发现 `seg-item-*` 与 `icon-badge-*` 的调用点**只写变体、不写基类**（如 `PortraitLibraryModal.tsx:149` 直接写 `'seg-item-primary'`），去掉 `@apply` 会直接破坏这些样式，与报告原结论的前提不符；`cn()` / tailwind-merge 引入；zIndex 层级表；字体自托管；`TargetCanvas.tsx` 拆分。
>
> **验证结果**：`pnpm --dir web lint`（Prettier + tsc）、`pnpm --dir web test`（161 passed / 35 files）、`pnpm --dir web build`、`uv run pytest tests/test_documentation_consistency.py`（15 passed）全通过。

**审阅对象**：`web/`（Vite 6.4.3 + React 19.2.8 + TypeScript 5.7 + Tailwind CSS 3.4.19 + Framer Motion 12.43.0 + Zustand 5 + Radix Primitives，pnpm）
**审阅方式**：纯只读。源码静态阅读 + `rg` 统计 + 产物反查 + 临时目录内 Tailwind 编译验证（未触碰仓库文件）
**约束遵守**：审阅阶段未修改任何仓库既有文件，未运行 `pnpm format` / `lint` / `build`。本报告本位于 `web/STYLE-AUDIT-REPORT.md`，于修复完成后移至 `docs/frontend-style-audit-20260928.md`，并追加了上方的修复状态与 §3 末的主会话裁决块。

---

## 0. 证据等级约定

| 标记 | 含义 |
| --- | --- |
| **【读到的】** | 直接来自文件内容、构建产物字节、或我实际执行的只读命令输出 |
| **【实测的】** | 我用标准库脚本/临时编译实际计算或生成的结果（含可复现命令） |
| **【推测的】** | 由前两类推出、未直接验证；已标注置信度与验证路径 |

## 0.1 对主会话量化事实的更正

主会话提供的数据中有 5 项与实际不符，全部重算（口径：`web/src/**`，排除 `*.test.tsx`）：

| 指标 | 主会话值 | 本次重算 | 口径 |
| --- | --- | --- | --- |
| `sm:` 断点 | 34 | **50** | `\bsm:[a-zA-Z0-9\[\]_.%/-]+` |
| `md:` 断点 | 5 | **6**（1 处在 `ProjectHistory.tsx:63`） | 同上 |
| `lg:` / `xl:` | 10 / 3 | **16 / 4** | 同上 |
| `!important` | 0 处 | **源码 `@apply` 带 `!` 12 处；编译产物 6 处 `!important`** | 见 §2 / 问题 H-2 |
| 动效违规 | 未列 | **新增：`slide-in-from-top-2` 全站零产物** | 见问题 H-1 |

**结论**：`!important` 的"健康"结论需要修正——源码里没写 `!bg-*` 这类前缀类，但 `@apply !bg-primary/15` 的写法在产物中确实输出了 `!important`。响应式则比主会话说得更差：工作台主链路完全没有 `md:` 适配。

## 0.2 产物级结论的交叉验证方法与一处证据裁决

本报告多处结论建立在产物之上。审阅中发现产物与当前源码存在可核查差异，因此我用**当前源码重新编译了一次**交叉验证，避免"结论基于过期产物"：

```bash
# 临时目录内编译，不触碰仓库
T=$(mktemp -d); mkdir -p "$T/src"
find web/src -name '*.tsx' -not -name '*.test.tsx' -exec cp --parents {} "$T/" \;
sed "s#content: \[.*\],#content: ['$T/src/**/*.tsx'],#" web/tailwind.config.ts > "$T/tw.config.ts"
printf '@tailwind base;\n@tailwind components;\n@tailwind utilities;\n' > "$T/full.css"
cat web/src/index.css >> "$T/full.css"
npx tailwindcss -c "$T/tw.config.ts" -i "$T/full.css" -o "$T/out.css" --minify
```

结果：当前源码产物 **67,320 bytes** vs `dist` **71,871 bytes**。差异存在，但**不影响本报告任何结论**：

| 断言 | `dist` | `out.css`（当前源码） | 是否成立 |
| --- | --- | --- | --- |
| `slide-in-from-top-2` 存在 | 0 | **0** | ✅ |
| `chip-sm` 存在 | 0 | **0** | ✅ |
| `.btn` 声明块数 | 1 | **1** | ✅ |
| 8 个 `.btn-*` 各自内联完整 `display:inline-flex` 基础块 | 是 | **是（8/8）** | ✅ |
| `.shadow-lg` 早于 `.shadow-indigo-600/25` | 52401 < 53896 | **51183 < 52501** | ✅（顺序同向） |
| `.shadow-indigo-600/25` 覆盖 `--tw-shadow` | 是 | **是** | ✅ |
| `.seg-item-primary` 重复 `.seg-item` 声明块 | 是 | **是（12 条逐字重复）** | ✅ |
| `.seg-item-primary` 带 `!important` | 6 个 | **6 个** | ✅ |

### 证据裁决：一处"产物与源码不同步"的误判

一位独立子代理提出：`dist` 里存在源码中"根本不存在"的 `.refinement-workbench a.btn-icon{min-height:40px}` / `a.btn-xs` / `a.btn-sm` / `a.btn-md` / `a.btn-lg` / `a.btn-primary` / `a.btn-ghost` / `a.btn-outline` 共 8 条规则，而源码 `index.css:320-323` 只写了 `.refinement-workbench button, .refinement-workbench a.btn, .refinement-workbench select`，据此判断产物与源码不同步。

**该判断不成立。** 用当前源码重新编译后这 8 条规则**同样存在**：

```
.refinement-workbench a.btn-icon{min-height:40px}
.refinement-workbench a.btn-xs{min-height:40px}
...（8 条全部出现）
.refinement-workbench a.btn,.refinement-workbench button,.refinement-workbench select{min-height:40px}
```

**原因是 Tailwind 3 的 `@apply` 选择器展开语义**：当 `.refinement-workbench a.btn` 作为载体时，Tailwind 会把目标规则按组合选择器**展开**为 `.refinement-workbench a.btn-icon`、`.refinement-workbench a.btn-xs` 等具体形式，而不是只输出一条通用规则。这是 `@apply` 在复合选择器下的正常行为，不是版本差异。

**裁决的连带影响**：本报告 §2 / §3 / §6 中基于产物的断言**全部保持有效**（上表逐项复核）。同时它给出一个额外的工程含义：`index.css:320-323` 那 3 行源码，在产物里膨胀成 **9 条规则 + 8 条派生规则**——这本身就是"scoped 补丁其实不 scoped"的又一例证。

**产物级数字的时效声明**：本文所有产物字节数、偏移量、规则条数均标注为"**基于 09-23 构建的 `dist`；当前源码编译值已在 §0.2 表中给出**"。落地前应重建一次复核。

---

## 1. 设计 token 体系一致性

### 判断：`tailwind.config.ts` 的 token 处于"名义权威、实际少数派"状态

**【实测的】** 类名使用次数（同一口径）：

| 体系 | 出现次数 |
| --- | --- |
| Tailwind 默认调色板裸用（`indigo/cyan/emerald/zinc/red/amber/rose/purple-NNN`） | **483** |
| 项目自定义 token（`surface*` / `primary*` / `accent*` / `ok*` / `warn*` / `err*` / `info*` / `border-*` / `background`） | **357** |

默认调色板以 **1.35 倍**压过自定义 token。这不是个别遗漏，而是双体系并行的事实状态。

**【读到的】** 最严重的具体后果是"同名不同色"：

| token | 项目定义值 | 默认调色板同名前缀 | 冲突点 |
| --- | --- | --- | --- |
| `primary` | `#5b5fef` | `indigo-600` = `#4f46e5` | 视觉几乎相同，代码里随机选 |
| `accent` | `#06b6d4` | `cyan-500` = `#06b6d4` | 完全相同，纯冗余 |
| `ok` | `#22c55e` | `green-500` / `emerald-500` | 代码里写 `emerald-300` / `emerald-400` |

具体证据（**读到的**）：

- `web/src/components/layout/AppHeader.tsx:25` — Logo 用 `from-indigo-500 via-indigo-600 to-cyan-500`，绕过 `primary`/`accent`。这里是 **Tailwind 默认的第三套紫**（`#6366f1 → #4f46e5 → #06b6d4`），既不是 AGENTS.md 5.2 写的 `#6366f1`，也不是 token 的 `#5b5fef`。
- `web/src/components/layout/ResponsiveWorkflowAccess.tsx:77` — `bg-ok-muted text-emerald-300 border-ok-border`：**同一 token 家族里混用两套体系**。背景用 `ok-muted`（`rgba(34,197,94,.12)`），文字用 `emerald-300`（`#6dd58c`），边框用 `ok-border`。色相系一致，亮度系不统一。
- 同文件 `:69` `text-emerald-400`、`:73` `text-amber-400`、`:78` `text-amber-300` 同样是默认调色板。
- `web/src/features/refinement/components/SessionComposer.tsx:146` — `text-emerald-300`，而项目有 `text-ok`。
- `web/src/features/refinement/components/SessionWorkbench.tsx` 出现 indigo-100/200/300/400/500、amber-200/500、cyan-200/300/500 等，形成 refinement 模块自成一体的调色板。
- `web/src/components/studio/ParameterPanel.tsx:555,592` 与 `web/src/components/studio/TargetCanvas.tsx:666` 用 `shadow-lg shadow-indigo-600/25` / `shadow-indigo-500/80`——默认调色板的阴影色，绕过已存在的 `shadow-primary` token（详见 §3）。

### `bg-[#...]` 任意值的收敛目标：必须先补 token，不能直接换

**【读到的】** 三处任意值背景：`TargetCanvas.tsx:433` `bg-[#0a0a0d]`、`CanvasBatchFilmstrip.tsx:24` `bg-[#121215]/90`、`RefinementImageViewport.tsx:44` `bg-[#09090b]`。

| 实际值 | 语义 | 最接近的现有 token | 差距 |
| --- | --- | --- | --- |
| `#09090b` | 图片画布底（refinement 视口） | `background` `#0c0c0f` | 更暗 3 阶 |
| `#0a0a0d` | 图片画布底（studio 画布） | `background` `#0c0c0f` | 更暗 2 阶，带蓝味 |
| `#121215` | 底片条 | `surface` `#111115` | 极接近，仅 G/B 通道 +1 |
| `#121215`/90 + `backdrop-blur-xl` | 底片条磨砂 | `glass-sm` = `rgba(17,17,22,0.8)` + `blur(12px)` | **`glass-sm` 就是它的正确 token 化表达** |
**行号核对说明**：`index.css` 中每个组件类的**声明行**与其 `@apply` **属性行**相差 1 行（例如 `.chip-err` 在 `:163`，其 `@apply` 在 `:164`）。本报告统一以**声明行**为准，定稿前已用 `sed -n "Np" src/index.css` 逐行复核全部引用。
**哪个是真正的画布底色语义**：`background: #0c0c0f` 的注释写着 `// deepest canvas`，而 `TargetCanvas.tsx:433` 与 `RefinementImageViewport.tsx:44` 都是"图片显示区"，语义上**就是** `background`。三处任意值是在已有正确 token 的情况下另造私有色。

**可落地的收敛改法**：

```ts
// web/tailwind.config.ts —— 在 colors 的 Surface Hierarchy 段追加
        'canvas-deep': '#09090b', // 图片视口底：比 background 更深，避免与面板层级混淆
```
```diff
- // TargetCanvas.tsx:433
- <div className="relative flex-1 flex flex-col h-full bg-[#0a0a0d] overflow-hidden select-none">
+ <div className="relative flex-1 flex flex-col h-full bg-canvas-deep overflow-hidden select-none">
```
```diff
- // RefinementImageViewport.tsx:44
- <div data-testid="refinement-image-viewport" className="relative min-h-0 flex-1 bg-[#09090b]">
+ <div data-testid="refinement-image-viewport" className="relative min-h-0 flex-1 bg-canvas-deep">
```
```diff
- // CanvasBatchFilmstrip.tsx:24
- <div className="h-[72px] border-t border-white/10 bg-[#121215]/90 backdrop-blur-xl px-3 ...">
+ <div className="h-[72px] border-t border-border bg-surface/90 backdrop-blur-xl px-3 ...">
```

`CanvasBatchFilmstrip` 这处还有二次收敛空间：`.glass-sm` 的定义（`index.css:135-141`）就是 `rgba(17,17,22,0.8)` + `blur(12px)` + `border-white/.07`，与 `bg-surface/90 backdrop-blur-xl` 是同一设计意图的两种写法。建议二选一：统一用 `.glass-sm`，或删掉 `.glass-sm` 把索引压到一层。

### JS 层硬编码色（比 className 里的更难收敛）

**【读到的】** 四处：

| 位置 | 内容 | 改法 |
| --- | --- | --- |
| `web/src/components/studio/ParameterPanel.tsx:150` | `accent === 'warn' ? '#f59e0b' : accent === 'accent' ? '#06b6d4' : '#5b5fef'` | 改走 CSS 变量 + `data-accent` |
| `web/src/components/studio/ParameterPanel.tsx:173` | `style={{ background: linear-gradient(...) }}` | 见下方片段 |
| `web/src/components/studio/ParameterPanel.tsx:403` | `shadow-[0_0_16px_rgba(6,182,212,0.18)]` | 改 `shadow-glow-cyan`（token 已有：`0 0 24px -4px rgba(6,182,212,0.30)`） |
| `web/src/components/studio/TargetCanvas.tsx:482,562,727` | `shadow-[0_0_50px_rgba(6,182,212,...)]` / `_25px_` / `_8px_` | 三者都是 cyan 外发光，收敛为 `shadow-glow-cyan`，或按强度补 `-sm`/`-lg` |

`ParameterPanel.tsx:172-176` 的可落地改法（消除 JS 拼 gradient）：

```diff
-          style={{
-            background: `linear-gradient(to right, ${accentColor} 0%, ${accentColor} ${percentage}%, rgba(255, 255, 255, 0.08) ${percentage}%, rgba(255, 255, 255, 0.08) 100%)`,
-            accentColor,
-          }}
+          data-accent={accent}
+          style={{ '--pw-range-pct': `${percentage}%` } as React.CSSProperties}
+          className="pw-range w-full h-1.5 rounded-full transition-all cursor-pointer"
```
```css
/* web/src/index.css @layer components 内追加 */
.pw-range {
  background: linear-gradient(
    to right,
    var(--pw-range-accent) 0%,
    var(--pw-range-accent) var(--pw-range-pct),
    rgba(255, 255, 255, 0.08) var(--pw-range-pct),
    rgba(255, 255, 255, 0.08) 100%
  );
}
.pw-range[data-accent='warn'] { --pw-range-accent: theme('colors.warn'); }
.pw-range[data-accent='accent'] { --pw-range-accent: theme('colors.accent'); }
.pw-range[data-accent='primary'] { --pw-range-accent: theme('colors.primary'); }
```
收益：色值只在 `tailwind.config.ts` 定义一次，`index.css` 用 `theme()` 引用。

**注意**：`web/src/hooks/useManualProtectionBrush.ts:93,107` 的 `rgba(245,158,11,0.45)` 是 Canvas 2D 绘制指令，**不能**改成 Tailwind 类，属合理硬编码。同理 `web/src/index.css:80` 的 `input[type=range]` thumb 阴影是伪元素样式，只能留在 CSS 里（可改为 `theme('colors.primary')` 引用）。

---

## 2. `index.css` 组件层设计

### 判断：`@apply` 链确实造成产物重复

**【实测的】** 从产物反查（数据见 §0.2 复核表）：

**`.btn` 家族**——`.btn` 的基础声明块被**每个变体各内联一份**：
```css
.btn{display:inline-flex;cursor:pointer;user-select:none;align-items:center;justify-content:center;
     gap:.375rem;font-weight:500;transition-property:all;transition-timing-function:cubic-bezier(.4,0,.2,1);
     transition-duration:.15s}
.btn-icon{ /* 同样 12 条声明，逐字重复 */ }
.btn-xs{   /* 同样 12 条 */ }
.btn-sm{   /* 同样 12 条 */ }
.btn-md{   /* 同样 12 条 */ }
.btn-lg{   /* 同样 12 条 */ }
.btn-primary{ /* 同样 12 条 */ }
.btn-ghost{   /* 同样 12 条 */ }
.btn-outline{ /* 同样 12 条 */ }
```
已用 `out.css` 逐类验证：8 个 `.btn-*` 的**每一个**首块都含 `display:inline-flex`（8/8 命中）。加上每个变体各自的 `.btn:focus-visible` / `.btn:active` / `.btn:disabled` 三条伪类块也逐类复制 → 产物中 `.btn:active{--tw-scale-x:.97;...}` 那整套 transform 声明出现 **9 次**。

**`.chip` 家族**——`.chip` 的 10 条声明在 `.chip-ok` 里逐字重复（`.chip{10 条}` / `.chip-ok{14 条}` = 10 + 自身 4）。

**`.seg-item` 家族——最严重**：
```css
.seg-item-primary{ /* .seg-item 的 12 条声明逐字重复（已实测确认） */ }
.seg-item-primary:hover{ /* 与 .seg-item:hover 逐字重复 */ }
.seg-item-primary{
  border-width:1px;border-color:#ffffff12;background-color:rgb(37 37 44);color:rgb(244 244 245);
  --tw-shadow:...;--tw-shadow-colored:...;
  box-shadow:var(--tw-ring-offset-shadow,...),var(--tw-ring-shadow,...),var(--tw-shadow);
  box-shadow:inset 0 1px 3px #0006;              /* ← 前一条被覆盖，冗余 */
  border-color:#5b5fef59!important;
  background-color:#5b5fef26!important;
  color:rgb(91 95 239)!important;
}
```

**【读到的】** 三级 `@apply` 链的完整定义（`index.css`）：
DtE│- `:154` `.chip`
9zH│- `:157/160/163/166/169/172/175` 七个 `chip-*` 各带 `@apply chip`
BkP│- `:180-185` `.btn`
Jb4│- `:186/189/192/195/198/202/205/208/211/214` 十个类各带 `@apply btn`（**10 个，主会话说的是从 `.btn-primary` 检查，实际范围更大**）
9ad│- `:226` `.seg-item-active { @apply seg-item ... }` → `:229` `.seg-item-primary { @apply seg-item-active !bg-primary/15 !text-primary !border-primary-border }`（**二级嵌套，链长 3**）
ljJ│- `:242/245/248` 三个 `@apply val-badge`
Fcv│- `:140/143/146/149` 四个 `@apply icon-badge`（`icon-badge` 本身在 `:137`）

### 问题 H-2：`!important` 实际存在于产物中

**【实测的】** 源码里 `!` 出现在 `@apply` 参数中，不是 className 的 `!bg-*` 前缀类，所以"className 里 0 处"的统计没错——但编译产物确实输出了 `!important`：

| 位置 | 源码写法 | 产物 `!important` |
| --- | --- | --- |
| `index.css:230` | `@apply seg-item-active !bg-primary/15 !text-primary !border-primary-border` | 3 个 |
| `index.css:243` | `@apply val-badge !bg-primary-muted !text-primary !border-primary-border` | 3 个 |
| `index.css:246` | `@apply val-badge !bg-accent-muted !text-accent !border-accent-border` | 3 个 |
| `index.css:249` | `@apply val-badge !bg-warn-muted !text-warn !border-warn-border` | 3 个 |

**说明**：表中列的是 `!` 实际出现的 `@apply` 行。

（对应的声明行是 `:229` / `:242` / `:245` / `:248`。）

产物按选择器归类（`dist` 与 `out.css` 一致）：`.seg-item-primary` 1 块、`.val-badge-primary` 1 块、`.val-badge-accent` 1 块、`.val-badge-warn` **2 块**——共 6 条 `!important`。

### 精简方案（可直接落地）

**核心原则**：`@apply` 链改成单层，源码里只写变体类，**不要**在 `@apply` 里再 `@apply` 自己的基类。基类通过 HTML 并列的类名继承。

```diff
/* web/src/index.css —— @layer components 内，btn 家族 */
-  .btn-icon { @apply btn h-9 w-9 shrink-0 rounded-lg; }
-  .btn-xs { @apply btn min-h-8 px-2 py-1 text-xs rounded-lg; }
-  .btn-sm { @apply btn px-2.5 py-1.5 text-xs rounded-lg; }
-  .btn-md { @apply btn px-3.5 py-2   text-xs rounded-xl; }
-  .btn-lg { @apply btn px-4   py-2.5 text-sm rounded-xl; }
-  .btn-primary { @apply btn bg-primary hover:bg-primary-hover text-white shadow-primary; }
-  .btn-accent { @apply btn bg-accent hover:bg-accent-hover text-white shadow-cyan; }
-  .btn-ghost { @apply btn text-zinc-400 hover:text-zinc-100 hover:bg-surface-hover border border-transparent hover:border-border; }
-  .btn-outline { @apply btn text-zinc-300 bg-surface-3 hover:bg-surface-hover border border-border hover:border-border-md; }
-  .btn-danger { @apply btn text-zinc-400 hover:text-red-400 hover:bg-err-muted; }
+  /* 尺寸与语义拆开；变体不再 @apply btn，调用处保持 "btn btn-sm" 的既有写法 */
+  .btn-icon { @apply h-9 w-9 shrink-0 rounded-lg; }
+  .btn-xs { @apply min-h-8 px-2 py-1 text-xs rounded-lg; }
+  .btn-sm { @apply px-2.5 py-1.5 text-xs rounded-lg; }
+  .btn-md { @apply px-3.5 py-2   text-xs rounded-xl; }
+  .btn-lg { @apply px-4   py-2.5 text-sm rounded-xl; }
+  .btn-primary { @apply bg-primary hover:bg-primary-hover text-white shadow-primary; }
+  .btn-accent { @apply bg-accent hover:bg-accent-hover text-white shadow-cyan; }
+  .btn-ghost { @apply text-zinc-400 hover:text-zinc-100 hover:bg-surface-hover border border-transparent hover:border-border; }
+  .btn-outline { @apply text-zinc-300 bg-surface-3 hover:bg-surface-hover border border-border hover:border-border-md; }
+  .btn-danger { @apply text-zinc-400 hover:text-red-400 hover:bg-err-muted; }
```

**为什么可行**：调用处已经是 `btn` + 变体全写。**【实测的】** 这样的调用处共 **84 行**（21 个文件），例证：
- `web/src/features/projects/ProjectControls.tsx:34` `className="btn btn-outline btn-xs"`
- `web/src/components/layout/AppHeader.tsx:64` `className="btn btn-outline btn-sm gap-1.5"`
- `web/src/features/refinement/components/SessionComposer.tsx:64` `className="btn btn-md btn-primary w-full"`
- `web/src/features/history/components/HistoryWorkspace.tsx:272` `className="btn btn-outline btn-xs"`
- `web/src/features/history/components/TaskResultHistoryDialog.tsx:210` `className="btn btn-primary btn-sm"`

去掉 `@apply btn` 后行为完全不变（`.btn` 的唯一声明块继续生效），产物中 `.btn` 的 12 条基础声明从内联 8 次降到 1 次。

**同样处理 chip / seg-item / val-badge / icon-badge**：

```diff
-  .chip-ok { @apply chip bg-ok-muted text-ok border-ok-border; }
+  .chip-ok { @apply bg-ok-muted text-ok border-ok-border; }
-  .seg-item-active { @apply seg-item bg-surface-hover text-zinc-100 shadow-inset border border-border; }
+  .seg-item-active { @apply bg-surface-hover text-zinc-100 shadow-inset border border-border; }
-  .seg-item-primary { @apply seg-item-active !bg-primary/15 !text-primary !border-primary-border; }
+  .seg-item-primary { @apply bg-primary/15 text-indigo-300 border-primary-border !shadow-inset; }
-  .val-badge-primary { @apply val-badge !bg-primary-muted !text-primary !border-primary-border; }
+  .val-badge-primary { @apply bg-primary-muted text-indigo-300 border-primary-border; }
```

**关键说明**：`seg-item-primary` 与 `val-badge-primary` 的文字色同时从 `text-primary` 换成 `text-indigo-300`——这不是风格偏好，是**对比度硬要求**（`text-primary` 叠 `primary/15` 底只有 **2.95:1**，两个阈值双双失败，见 §5）。去掉 `@apply seg-item-active` 后，`!` 也全部可去，因为调用处是三元互斥（`PortraitLibraryModal.tsx:149,157` `gender === 'all' ? 'seg-item-primary' : 'seg-item'`；`SourceGallery.tsx:384-386` 同构），**二者从不并列**，不需要 `!important` 压。

**关于调用处并列叠加的精确判断**：

| 家族 | 调用处"基类+变体"并列 | 是否影响 CSS 体积 |
| --- | --- | --- |
| `btn` + `btn-*` | **84 行** | **否**——CSS 按类定义只输出一次，HTML 写几次无关 |
| `chip` + `chip-*` | **11 行**（`AppHeader.tsx:83`、`App.tsx:150`、`MultiPersonMatrixModal.tsx:221/302/307`、`SourceGallery.tsx:345/374/593/598`、`ParameterPanel.tsx:119/388`） | **否** |
| `seg-item` + `seg-item-*` | **0**（全部三元互斥） | — |
| `val-badge` + `val-badge-*` | **0**（`ParameterPanel.tsx:147-148` 只返回变体名；其他处只用裸 `val-badge`） | — |
| `icon-badge` + `icon-badge-*` | **0** | — |

**所以真正让 CSS 变大的是定义侧的 `@apply` 链，不是调用侧的叠加。** 调用侧叠加的成本只有 className 字符串长度与浏览器级联计算量。这个区分很重要——如果只去重调用处而不动 `@apply` 链，产物体积一点都不会变。

### `.chip` 家族的一个真实 bug

**【读到的 + 实测的】** `web/src/features/history/components/HistoryWorkspace.tsx:416`：
```tsx
className={`chip chip-sm ${task.status === 'COMPLETED' ? 'chip-ok' : ...}`}
```
`chip-sm` 在 `index.css` 的 `.chip` 家族里**不存在**，且我用**当前源码重新编译**验证：`grep -c 'chip-sm' out.css` → **0**。这是一处无效类名——不影响视觉（`chip` 本身已提供 `text-2xs`），但说明这段代码是从别处复制、且从未被验证。

改法：删掉 `chip-sm`；若确实需要更紧凑的 chip，补定义而非留悬空类名：
```css
.chip-sm { @apply px-1.5 py-0 text-[10px]; }
```

---

## 3. 缺少 `cn()` / tailwind-merge 的类名冲突风险

### 【实测的】全项目 `clsx` / `tailwind-merge` / `cn()` 零命中

```bash
rg -n 'clsx|tailwind-merge|twMerge|classnames' web/src   # → 0 hits
rg -n '\bimport.*\bcn\b' web/src                          # → 0 hits
```
`web/package.json` 的 dependencies 中也没有这两个包。

### 真实冲突案例 1（最严重的一处）

`web/src/components/studio/ParameterPanel.tsx:555` 与 `:592`：
```tsx
className="w-full btn btn-lg btn-primary justify-center gap-2 font-bold text-sm shadow-lg shadow-indigo-600/25"
```

**在无 twMerge 下谁胜出：`shadow-lg` 完全失效。** ⚠️ **【主会话产物级裁决：此断言不成立，且基于它给出的修复建议有害 —— 见本节末裁决块】**

**【实测的】** 产物字节偏移（`dist`：`.shadow-lg` 52401 < `.shadow-indigo-600/25` 53896；`out.css`：51183 < 52501——两次测量同向）。两者都是单类选择器（特异度相同），**后者胜出**：

```css
.shadow-lg{--tw-shadow:0 10px 15px -3px rgb(0 0 0/.1),0 4px 6px -4px rgb(0 0 0/.1);
           --tw-shadow-colored:0 10px 15px -3px var(--tw-shadow-color),0 4px 6px -4px var(--tw-shadow-color);
           box-shadow:var(--tw-ring-offset-shadow,0 0 #0000),var(--tw-ring-shadow,0 0 #0000),var(--tw-shadow)}
.shadow-indigo-600\/25{--tw-shadow-color:rgba(79,70,229,.25);--tw-shadow:var(--tw-shadow-colored)}
```

覆盖后的实际计算值：`--tw-shadow` 被重定义为 `var(--tw-shadow-colored)`。按 Tailwind 设计意图这应能与 `shadow-lg` 组合成"大号 indigo 阴影"，**但实际不是**——`.shadow-indigo-600/25` 自己覆盖了 `--tw-shadow`，几何值来自它自身（`--tw-shadow-colored` 里嵌的是 `shadow-lg` 的几何，这层恰好侥幸成立），但**类名层面 `shadow-lg` 已经完全不起作用**：若改成 `shadow-sm shadow-indigo-600/25`，几何仍走 `--tw-shadow-colored`，结果视觉相同。

这就是"后写的断不掉的既有类"最实际的危害：**多写少写一个 shadow-size 类，渲染结果看不出差别，代码里也看不出问题。**

改法（用 token 替掉违规色）：
```diff
- // ParameterPanel.tsx:555、:592
- className="w-full btn btn-lg btn-primary justify-center gap-2 font-bold text-sm shadow-lg shadow-indigo-600/25"
+ className="w-full btn btn-lg btn-primary justify-center gap-2 font-bold text-sm shadow-primary"
```
`shadow-primary` token 已存在（`primary: '0 2px 16px rgba(91,95,239,0.35)'`），语义就是"primary 发光"。`TargetCanvas.tsx:666` 的 `shadow-lg shadow-indigo-500/80` 一并改。

### 【主会话产物级裁决】案例 1 的两处结论需要修正

用当前源码独立重编译复核（`cd web && ./node_modules/.bin/tailwindcss -i src/index.css -o /tmp/pw-probe-out.css`），本节结论逐条核对如下。

**修正 1：`shadow-lg` 并未失效，本节的核心断言不成立。**

`--tw-shadow-colored` 是**每个 shadow-size 类各自定义**的几何值，不是共享变量：

```css
.shadow-sm { --tw-shadow-colored: 0 1px 2px 0 var(--tw-shadow-color); }
.shadow-md { --tw-shadow-colored: 0 4px 6px -1px var(--tw-shadow-color), 0 2px 4px -2px var(--tw-shadow-color); }
.shadow-lg { --tw-shadow-colored: 0 10px 15px -3px var(--tw-shadow-color), 0 4px 6px -4px var(--tw-shadow-color); }
```

`.shadow-indigo-600/25` 把 `--tw-shadow` 重定义为 `var(--tw-shadow-colored)`，是 Tailwind **设计意图内的着色机制**：着色类只注入 `--tw-shadow-color` 并把 `box-shadow` 改读 colored 版本，几何仍由 size 类提供。所以 `shadow-lg shadow-indigo-600/25` 与 `shadow-sm shadow-indigo-600/25` 的渲染结果**完全不同**（前者 15px/6px 双层，后者 2px 单层）。本节「改成 `shadow-sm` 结果一字不变」不成立——本节自己的论证里已写出「`--tw-shadow-colored` 里嵌的是 `shadow-lg` 的几何」，事实写对了，结论下反了。

**修正 2：本节给出的修复建议（改用 `shadow-primary`）会引入一个真实的 token 劫持缺陷。**

产物中 `.shadow-primary` 存在**两条同名规则**：

```css
/* 产物 4043 行，来自 theme.extend.boxShadow.primary */
.shadow-primary { --tw-shadow: 0 2px 16px rgba(91,95,239,0.35);
                  --tw-shadow-colored: 0 2px 16px var(--tw-shadow-color);
                  box-shadow: var(--tw-ring-offset-shadow, 0 0 #0000), ... , var(--tw-shadow); }
/* 产物 4076 行，来自颜色插件（因为 colors.primary 是一个字符串键） */
.shadow-primary { --tw-shadow-color: #5b5fef; --tw-shadow: var(--tw-shadow-colored); }
```

4076 在后胜出，`.shadow-primary` 的最终计算值是 `0 2px 16px #5b5fef`（**100% 不透明**），而不是配置里写的 `rgba(91,95,239,0.35)`（35% 透明）。**每个用 `shadow-primary` 的 `.btn-primary` 当前都在渲染这个被劫持的值。**

对照组：`shadow-cyan` 只有一条规则（`colors.cyan` 是 `{neon, glow}` 对象、没有 `DEFAULT`，颜色插件不为它生成同名类），token 正常生效。**同一个 `extend` 块里的两个阴影 token，一个坏一个好，差别只在于对应颜色键是字符串还是对象** —— 这是本项目最隐蔽的一处配置缺陷，且被本节的修复建议直接踩中。

正确的修复二选一，不要改用 `shadow-primary`：

```diff
# 方案 A（推荐）：给 boxShadow 键改名，消除与 colors 键的同名碰撞
-    shadow: { primary: '0 2px 16px rgba(91,95,239,0.35)', cyan: '0 2px 16px rgba(6,182,212,0.30)', ... }
+    shadow: { 'glow-primary': '0 2px 16px rgba(91,95,239,0.35)', 'glow-cyan': '0 2px 16px rgba(6,182,212,0.30)', ... }
+  # 同步替换 .btn-primary / .btn-accent 里的 shadow-primary / shadow-cyan
+
# 方案 B：保留 `shadow-lg shadow-indigo-600/25` 原样，只把违规默认色换成项目 token
+  className="... shadow-lg shadow-primary/25"   # 注意用 /25 透明度变体，几何仍是 lg
```

方案 B 的前提是先执行方案 A —— 否则 `shadow-primary/25`（产物 4081 行）会被同名的 boxShadow token 规则干扰。

### 真实冲突案例 2（**看起来像冲突，实际不是**）

`web/src/components/studio/CanvasTopToolbar.tsx:89`：
```tsx
className={`btn btn-icon ${props.showFaceMarkers ? 'bg-cyan-500/10 text-cyan-300' : 'btn-ghost'}`}
```
两个分支**互斥，永不并列**。`btn-icon` 提供尺寸，`btn-ghost` 提供 hover 反馈，active 分支用 `bg-cyan-500/10` 自绘背景——是正确写法。写进报告是为了区分真问题与假警报。

### 真实冲突案例 3（**结构性隐患，当前无 bug**）

`web/src/components/ui/ComparisonViewer.tsx:132`：
```tsx
className={`space-y-2 ${className}`}
```
调用处 `TaskResultHistoryDialog.tsx:245` 传 `className="w-full"`，当前无冲突。基类在前、外部类在后，所以**外部覆盖能生效**——这是正确顺序。但同文件 `:80` 用的是 `'space-y-2 ' + className` 字符串拼接，`viewportSize` 分支又用三元返回完整字面量串（`:88`/`:100`/`:114`）。**同一文件三种风格并存**，这才是需要 `cn()` 解决的可维护性问题。

### 【实测的】引入 `cn()` 的成本与收益判断

- 成本：`pnpm add clsx tailwind-merge`（约 3KB gzip），改 5 处高危拼接点。
- 收益：消除案例 1 这类静默失效；让 `ComparisonViewer` 的 className 透传语义确定化。
- **判断：值得引入，但不是第一优先级。** 当前只有 1 处真实渲染隐患（案例 1），且案例 1 用 token 替换就能解决。建议引入时机放在案例 1 修复的**同一次提交**里，避免"修了又写回去"。

最小落地形态：
```ts
// web/src/utils/cn.ts
import { twMerge } from 'tailwind-merge';
export const cn = (...parts: Array<string | false | null | undefined>) => twMerge(parts.filter(Boolean).join(' '));
```
配合 `vite.config.ts` 已配好的 `@` 别名，调用处改 `className={cn('space-y-2', className)}`。优先改：`SessionWorkbench.tsx:210,306,472`、`ComparisonViewer.tsx:80,132`。

---

## 4. 动态类名拼接对 Tailwind JIT 的破坏风险

### 逐一核实结论：**34 处全部是完整字面量，不存在真实的产物缺失风险，不需要 safelist**

**【实测的】** 导出 `web/src/**` 中所有 `${}` 插值（共 **149** 处，含 className / style / 文本 / 属性）并逐类分类。与 className 相关的 **34 处**模板拼接点：

| 类别 | 数量 | 例证 | 是否产生运行时类名 |
| --- | --- | --- | --- |
| 三元分支返回完整字面量类名串 | 13 | `CanvasBrushToolbar.tsx:44-46` `!isEraser ? 'bg-amber-500/20 text-amber-300 border border-amber-400/50 shadow-sm' : 'text-zinc-400 hover:text-zinc-200'` | 否 |
| 映射对象键取值，值是完整字面量 | 3 | `ParameterPanel.tsx:116` `${accentClasses[accent]}` → `:102-106` 的 `'text-primary'`/`'text-accent'`/`'text-warn'`；`SourceGallery.tsx:349` `${qualityClass}` → `:308-313` 的 `'chip-ok'`/`'chip-err'`/`'chip-warn'` | 否 |
| 条件追加的单个完整类名 | 5 | `TargetCanvas.tsx:698` `${viewMode === 'BRUSH' ? '' : 'hidden'}`；`BatchStatusBar.tsx:62` `${indeterminate ? 'animate-pulse' : ''}` | 否 |
| 基础类名 + 条件类名（全字面量） | 13 | `BatchStatusBar.tsx:47` `${queryError ? 'text-err' : 'text-zinc-300'}` | 否 |
| **props 透传** | **1** | `ComparisonViewer.tsx:132` `${className}` | **是，但取值域是有限字面量** |

**唯一的 `${className}` 透传点**：两个调用处传入的都是**完整字面量**（`TaskResultHistoryDialog.tsx:245` 传 `"w-full"`；`RefinementImageViewport.tsx` 未传）。所以即使值来自运行时，取值域仍是能被 content 扫描的有限集合。

**结论（明确）**：**当前不存在真实的产物缺失风险。引入 safelist 无收益。**

### 真正的产物缺失问题（性质不同，见问题 H-1）

`slide-in-from-top-2` 不是"拼接导致类名不被扫描"，而是"**类名被扫描了，但 Tailwind 根本没有这个类的定义**"。

**【实测的】** 探针编译验证（临时目录）：
```bash
printf "export const probe = 'slide-in-from-top-2 fade-in zoom-in-95 animate-in chip-sm text-2xs';\n" > $T/src/probe.ts
npx tailwindcss -c $T/tw.config.ts -i $T/src/in.css -o $T/out.css
# 结果：slide-in-from-top-2 → 0 | fade-in → 0 | zoom-in-95 → 0 | animate-in → 0 | chip-sm → 0 | text-2xs → 1
```
并用**当前源码完整编译**复核：`grep -c 'slide-in-from-top-2' out.css` → **0**（`dist` 同样为 0）。

**根因**：`web/tailwind.config.ts` 的 `plugins: []` 为空。`.fade-in` / `.zoom-in-95` / `.animate-in` 之所以能工作，是因为 `web/src/index.css:267-302` 在 `@layer utilities` 里**手写补了这三个**；而 `slide-in-from-top-2` 是 `tailwindcss-animate` 插件提供的类，**项目从没补过**。

**受影响位置（静默失效）**：
- `web/src/components/studio/TargetCanvas.tsx:584`（VLM 推理横幅）
- `web/src/components/studio/CanvasBrushToolbar.tsx:30`（画笔浮动工具栏）

（注：`AppHeader.tsx:48`、`TargetCanvas.tsx:482/493/562`、`CanvasFaceMarkersOverlay.tsx:107` 用的是 `animate-in fade-in` / `zoom-in-95`，这三个有手写补丁，正常工作。）

---

## 5. 深色主题层次与对比度

### surface 亮度阶梯：**设计上成立，但缺一档**

**【读到的】** token 定义与相对亮度 L（**实测的**）：

| token | 值 | L | 相邻 ΔL | 语义 |
| --- | --- | --- | --- | --- |
| `background` | `#0c0c0f` | 0.0043 | — | deepest canvas |
| `surface` | `#111115` | 0.0058 | +0.0015 | primary panel |
| `surface-1` | `#141418` | 0.0070 | +0.0012 | elevated card/strip |
| `surface-2` | `#17171c` | 0.0086 | +0.0016 | elevated panel/dialog |
| `surface-3` | `#1e1e24` | 0.0130 | +0.0044 | card / input |
| `surface-hover` | `#25252c` | 0.0191 | +0.0061 | interactive hover |
| `surface-active` | `#2d2d36` | 0.0281 | +0.0090 | pressed / selected |

阶梯单调递增、步长逐级放大（0.0012 → 0.0090），符合"越靠近交互层对比越强"的深色 UI 惯例。**阶梯本身成立。**

但 `#09090b`（`RefinementImageViewport.tsx:44`）的 L = 0.0033，比 `background` 更暗——**设计上确实缺一档"图片视口底"**。§1 的 `canvas-deep` token 补的就是这个洞。

### 对比度总表（双轨独立计算，数值完全一致）

**【实测的】** 方法：sRGB 线性化 + `L = 0.2126R + 0.7152G + 0.0722B` + `(L1+0.05)/(L2+0.05)`；alpha 合成按 `C = a·Fg + (1−a)·Bg` 逐通道合成。我按此计算，另由独立子代理用不同脚本对 18 个组合复算，**逐项数值完全一致**。

| 前景 | 背景 | 对比度 | AA 正文 4.5 | AA 大字/UI 3.0 |
| --- | --- | --- | --- | --- |
| `text-zinc-500` `#71717a` | `bg-surface-3` `#1e1e24` | **3.43** | **FAIL** | PASS |
| `text-zinc-500` | `bg-surface` `#111115` | **3.90** | **FAIL** | PASS |
| `text-zinc-500` | `bg-surface-2` `#17171c` | **3.70** | **FAIL** | PASS |
| `text-zinc-500` | `bg-surface-hover` `#25252c` | **3.15** | **FAIL** | PASS（勉强） |
| `text-zinc-600` `#52525b` | `bg-surface-2` | **2.31** | **FAIL** | **FAIL** |
| `text-zinc-600` | `bg-surface-3` | **2.15** | **FAIL** | **FAIL** |
| `text-zinc-400` `#a1a1aa` | `bg-surface-3` | 6.47 | PASS | PASS |
| `text-zinc-400` | `bg-surface` | 7.35 | PASS | PASS |
| `text-zinc-300` `#d4d4d8` | `bg-surface-3` | 11.22 | PASS | PASS |
| `text-zinc-200` `#e4e4e7` | `bg-background` | 15.39 | PASS | PASS |
| `text-ok` `#22c55e` on `bg-ok-muted`\|s3 | | 5.96 | PASS | PASS |
| `text-warn` `#f59e0b` on `bg-warn-muted`\|s3 | | 6.25 | PASS | PASS |
| `text-accent` `#06b6d4` on `bg-surface-3` | | 6.83 | PASS | PASS |
| `text-info` `#818cf8` on `bg-info-muted`\|s3 | | 4.66 | PASS | PASS |
| `text-err` `#ef4444` on `bg-err-muted`\|s3 | | **3.89** | **FAIL** | PASS |
| `text-primary` `#5b5fef` on `bg-surface-3` | | **3.42** | **FAIL** | PASS（勉强） |
| **`text-primary` on `bg-primary-muted`\|s3** | | **2.95** | **FAIL** | **FAIL** |
| `btn-primary` `text-white` on `bg-primary` | | 4.85 | PASS（余量 0.35） | PASS |
| **`btn-accent` `text-white` on `bg-accent`** | | **2.43** | **FAIL** | **FAIL** |

**不通过共 9 项**（`zinc-600` 的两项连 UI 3.0 都跌破）。

### 独立复核补充的三个新发现

**（1）`btn-accent` 的 `text-white` 对比度 2.43:1，当前是死代码但属潜伏风险**

`web/src/index.css:206`：
```css
.btn-accent { @apply btn bg-accent hover:bg-accent-hover text-white shadow-cyan; }
```
`#ffffff` on `#06b6d4` = **2.43**，两个阈值双双失败。`rg -n 'btn-accent' web/src` **只命中定义处**，当前零使用点。一旦有人按名字直觉使用它（"强调色按钮"），就会得到白字在青色上不可读的按钮。改法二选一：
```diff
/* 选项 A：深色文字（推荐——青色是亮底，深字对比更强） */
-  .btn-accent { @apply btn bg-accent hover:bg-accent-hover text-white shadow-cyan; }
+  .btn-accent { @apply bg-accent hover:bg-accent-hover text-zinc-950 shadow-cyan; }
/* 选项 B：直接删除——零使用点，删掉比留着安全（符合 AGENTS.md 7.12 死代码清理） */
```

**（2）`chip-err` 的实际底色比 `index.css` 定义更浅，对比度在 3.99–4.45 漂移**

代码里更常见的写法不是 `bg-err-muted`，而是 `bg-err/10`（`TaskResultHistoryDialog.tsx:109,221`、`HistoryWorkspace.tsx:304,312`、`RecentJobsPopover.tsx:89,94`、`ParameterPanel.tsx:538`）。实测 `text-err` `#ef4444` on `bg-err/10` 合成底：surface-3 **3.99** / surface-2 **4.30** / surface-1 **4.45**——**三个都不达 4.5**。所以修 `chip-err` 时必须**同时覆盖 `bg-err/10` 这条路径**，只改 `index.css:164` 的 `.chip-err` 不够。

**（3）`btn-primary` 的 4.85:1 是贴边通过，余量仅 0.35**

`web/src/index.css:203`：`#ffffff` on `#5b5fef` = **4.85**；hover 态 `#4e52d6` = 6.00。**不建议现在改**（会牵动全站主按钮视觉），但新增主色系变体时要注意：任何把 `primary` 往亮处调的动作都会让按钮文字跌破 4.5。

### 需替换的具体位置与替换后数值

| 问题 | 替换 | 替换后 |
| --- | --- | --- |
| `text-zinc-500` 承载**正文**（约 20 处代表位置，见下表） | → `text-zinc-400` | surface-3 **6.47** / surface-2 6.97 / surface-1 7.17 / surface 7.35 / background 7.62 / surface-hover 5.94（**六个底全通过，一次改动通杀**） |
| `text-zinc-600` 6 处 | → **直接升到 `text-zinc-400`** | 2.15 → 6.47。**升一级到 `text-zinc-500` 只有 3.43，仍不达 4.5** |
| `.seg-item-primary` / `.chip-primary` 的 `text-primary` | → `text-indigo-300` `#a5b4fc` | primary/15\|surface-3 **7.17** / \|surface-hover **6.59**。**不要用 `text-indigo-400`**——它在 primary/15\|surface-hover 上只有 **4.40**，不够 |
| `.chip-err` / `.val-badge-*` 的 `text-err` | → `text-red-400` `#f87171` | **5.30**（`bg-err/10` 路径同步改） |
| `.icon-badge-primary` 的 `text-primary`（UI 元素需 3.0） | → `text-indigo-300` | 2.95 → 7.17 |

**`text-zinc-500` 作为正文的具体改动点**：

| 类型 | 位置 | 建议 |
| --- | --- | --- |
| 段落正文（最该换） | `SessionWorkbench.tsx:314` `text-sm leading-relaxed text-zinc-500` | 改 `text-zinc-400` |
| | `SessionWorkbench.tsx:484` `p-5 text-sm text-zinc-500` | 改 `text-zinc-400` |
| | `SessionConversation.tsx:253` `text-xs leading-relaxed text-zinc-500` | 改 `text-zinc-400` |
| | `TaskResultHistoryDialog.tsx:132` / `:266` `text-sm text-zinc-500` | 改 `text-zinc-400` |
| | `RefinementQualityPanel.tsx:118,127` `text-2xs text-zinc-500` | 改 `text-zinc-400` |
| 空状态提示 | `ProjectHistory.tsx:61`、`LegacyQualityHistory.tsx:113`、`RecentJobsPopover.tsx:100,107`、`SessionWorkbench.tsx:270` | 改 `text-zinc-400` |
| 描述性副标题（`Dialog.Description`） | `TaskResultHistoryDialog.tsx:70`、`PortraitLibraryModal.tsx:120`、`SourceGallery.tsx:444` | 改 `text-zinc-400` |
| 半透明底上的正文 | `SourceGallery.tsx:533`（`bg-surface-3/70` 合成 **3.58**）、`RecentJobsPopover.tsx:67`（宿主 `bg-surface-2`，**3.70**） | 改 `text-zinc-400` |
| 装饰性图标（可保留 500） | `SourceGallery.tsx:195,229,494`、`ParameterPanel.tsx:125,127` | 保留（图标属 UI 元素，3:1 达标） |

**`text-zinc-600` 的 6 处**：`RecentJobsPopover.tsx:137`（宿主 `<article>` `bg-surface-1` → **2.38**）、`SourceGallery.tsx:467`、`PortraitLibraryModal.tsx:167,210`、`SourceGallery.tsx:454` 与 `index.css:88`（`placeholder`，宿主 `bg-surface-3` → **2.15**）。占位符虽非 WCAG 强制，但 2.15:1 已低于可读阈值。

**工程化替代方案（可选）**：不要逐个手改 55 处 `text-zinc-500`——项目全站只用深色模式，`zinc-500` 从没被当"深色文字"用过，可在 config 里重定义：
```ts
// web/tailwind.config.ts —— 需要 import defaultTheme from 'tailwindcss/defaultTheme';
// 理由：深色底上 #71717a 只有 3.15–3.90:1，不足以承载正文；本项目 zinc 只用于文字层级，无反色场景
        zinc: { ...defaultTheme.colors.zinc, 500: '#9ca3af' },
```
**风险**：`text-zinc-500` 也用于装饰图标，抬亮后会略微抢眼。若不想全局动，则按上表批量替换（可脚本化的机械操作）。

---

## 6. 响应式与移动端

### 先纠正一个误判：参数面板在 768px 以下**有入口**，不是不可访问

**【读到的】** `web/src/components/layout/ResponsiveWorkflowAccess.tsx` 提供了完整的抽屉式降级：
- `:40` `源身份` 按钮 `lg:hidden`（< 1024px 显示）
- `:47` `换脸控制` 按钮 `xl:hidden`（< 1280px 显示）
- `:95-113` Radix `Dialog` 抽屉，`z-[80]`，内容 `openPanel === 'sources' ? sourcePanel : parameterPanel`（`:112`）

`App.tsx:205,216` 的 `aside` 用 `hidden lg:block` / `hidden xl:block` 隐藏，与抽屉入口 `lg:hidden` / `xl:hidden` **严格互补，无重叠区间**。这是正确的响应式模式。

### 真实问题：768–1023px 区间的工作流摩擦

**【读到的】** 在 `md` 区间：
1. `App.tsx:200` 网格只有 `grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)] xl:grid-cols-[290px_minmax(0,1fr)_320px]`。768px 时落到 `grid-cols-1`，即**单列**。
2. 此时 `SourceGallery` 和 `ParameterPanel` 都在抽屉里——**调整相似度阈值必须：开抽屉 → 拖滑块 → 关抽屉 → 看画布 → 再开抽屉**。滑块调节是高频反复动作，这个循环在 1024px 以下无法闭合。
3. `md:` 只有 6 次使用，其中 `ProjectHistory.tsx:63`（`md:grid-cols-2`）和 `TaskResultHistoryDialog.tsx:134,135`（`md:flex-row`/`md:w-48`）是历史/工程模块，`AppHeader.tsx:107` 是 `hidden md:inline`。**工作台主链路（TargetCanvas / ParameterPanel / SourceGallery / SessionWorkbench）零 `md:` 适配。**

**可落地的改法**：

```diff
- // web/src/App.tsx:200
- ? 'flex-1 grid grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)] xl:grid-cols-[290px_minmax(0,1fr)_320px] overflow-hidden min-h-0'
+ ? 'flex-1 grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_300px] lg:grid-cols-[260px_minmax(0,1fr)] xl:grid-cols-[290px_minmax(0,1fr)_320px] overflow-hidden min-h-0'
```
```diff
- // web/src/App.tsx:215-217 —— 参数面板从 xl 提前到 md
-        <aside className="hidden xl:block h-full border-l border-border overflow-hidden">
+        <aside className="hidden md:block h-full border-l border-border overflow-hidden">
```
```diff
- // ResponsiveWorkflowAccess.tsx:47 —— 抽屉触发按钮的隐形断点必须同步改为 md
-              className="btn btn-sm btn-outline min-h-9 shrink-0 gap-1.5 text-xs text-zinc-200 xl:hidden"
+              className="btn btn-sm btn-outline min-h-9 shrink-0 gap-1.5 text-xs text-zinc-200 md:hidden"
```
后两处**必须同时改**，否则 md–lg 区间会出现双入口（抽屉按钮 + 常驻面板）。

另外 `ParameterPanel` 在 300px 宽度下需渲染验证：它内部有 `ParameterPanel.tsx:510` 的 `max-h-[55%] overflow-y-auto` footer 和多个 `select`（`:490` `w-full text-xs`）。**这点我未实测渲染，标注为【推测的】**，建议改后用 Playwright 在 768/900px 两档截图确认。

### 触摸目标：三套机制并行，且补丁会反向吃掉工具类

**【读到的 + 实测的】**

| 机制 | 位置 | 生效范围 | 实测值 |
| --- | --- | --- | --- |
| 内联 `style={{ minHeight: 44 }}` | `SessionComposer.tsx:65,108,116,138,153` + `SessionWorkbench.tsx:332,398,415,424` = **9 处**（全项目仅此 9 处，全在精修区） | 仅这 9 个按钮 | 44px |
| CSS 补丁 `.refinement-workbench button / a.btn / select` | `index.css:320-323` | refinement 区 | 40px |
| Tailwind 类 `min-h-8` / `min-h-9` / `min-h-11` | 各组件（见下） | 全站 | 32 / 36 / 44px |

实际尺寸完整分类：

| 控件 | 证据位置 | 实际尺寸 | 依据 |
| --- | --- | --- | --- |
| SessionComposer 主操作按钮 | `SessionComposer.tsx:65,108,116,138,153` | 44px | 内联 `minHeight: 44` |
| SessionWorkbench 创建/继续/导出/接受 | `SessionWorkbench.tsx:332,398,415,424` | 44px | 内联 `minHeight: 44` |
| refinement 区 button / a.btn / select | `index.css:320-323` | 40px | CSS 补丁 |
| refinement 区 `.btn-icon` | `index.css:325-329` | 40×40px | CSS 补丁 |
| **分区 tab（源码写了 `min-h-11`）** | **`SessionWorkbench.tsx:180`** | **40px（被压回）** | **特异性 `(0,1,1) > (0,1,0)`** |
| Studio 顶栏图标按钮 | `CanvasTopToolbar.tsx:89,108,120` | 36×36px | `btn-icon` → `h-9 w-9` |
| Studio 结果操作条 | `CanvasResultActionBar.tsx:26,34` | 36px | `min-h-9` |
| 移动端工作流入口 | `ResponsiveWorkflowAccess.tsx:40,47` | 36px | `min-h-9` |
| 画布画笔工具条 | `CanvasBrushToolbar.tsx:43,55,82,92,102` | 44px | `min-h-11` / `min-w-11` |
| 历史/项目区小按钮 | `HistoryWorkspace.tsx:272,281,320,370,442,450,479,488`、`ProjectControls.tsx:34,39,46`、`ProjectHistory.tsx:11,75` | 32px | `.btn-xs` → `min-h-8` |

**`SessionWorkbench.tsx:180` 是这一节的临界证据**：类名写的是 `min-h-11`（意图 44px），但 `.refinement-workbench button{min-height:40px}` 的**特异性 `(0,1,1)` 高于 `.min-h-11` 的 `(0,1,0)`**，实际渲染 40px。**这是"补丁反向吃掉 Tailwind 工具类"的实证**——补丁本意是抬到 40px，实际把一个已经写对的 44px 拉了下来。

**尺寸依据（产物实测）**：`.h-9`/`.w-9` = `2.25rem` = 36px；`min-h-11` = `min-height:2.75rem` = 44px；`.btn-xs` 的 `min-h-8` = 32px。**上述尺寸结论来自 CSS 层叠规范推导 + 产物声明值**，未在浏览器实测（未启动 dev server、未 build）。建议落地断言：
```js
getComputedStyle(tab).minHeight                     // SessionWorkbench.tsx:180 → 期望 40px
getComputedStyle(btn).minHeight                     // SessionComposer.tsx:65    → 期望 44px
document.querySelector('[aria-label="快捷键指南"]').getBoundingClientRect().height  // CanvasTopToolbar.tsx:108 → 期望 36
```
未验证项：`SessionWorkbench.tsx:438` 版本轨道按钮是"内容撑高"还是"40px 兜底"，取决于缩略图内容高度。

**统一的触摸目标方案（推荐 Tailwind 暴露 + CSS 变量两层）**：

```ts
// web/tailwind.config.ts —— extend 内追加
      spacing: { 'touch': '44px' },
      minHeight: { 'touch': '44px', 'touch-sm': '40px' }, // 44 = iOS HIG 下限；40 = 密集工具栏
      minWidth: { 'touch': '44px', 'touch-sm': '40px' },
```
```diff
-          className="btn btn-md btn-primary w-full"
-          style={{ minHeight: 44 }}          // SessionComposer.tsx:64-65，全项目 9 处同类
+          className="btn btn-md btn-primary w-full min-h-touch"
```
```diff
-        className={`min-h-11 px-3 py-2 rounded-lg ...`}   // CanvasBrushToolbar.tsx:43,55,102
+        className={`min-h-touch px-3 py-2 rounded-lg ...`}
```
```diff
-            className="relative flex min-h-8 items-center gap-1.5 ..."   // CanvasTopToolbar.tsx:67
+            className="relative flex min-h-touch-sm items-center gap-1.5 ..."
```
```diff
-              className="btn btn-sm btn-outline min-h-9 shrink-0 ..."      // ResponsiveWorkflowAccess.tsx:40,47 / CanvasResultActionBar.tsx:26,34
+              className="btn btn-sm btn-outline min-h-touch-sm shrink-0 ..."
```

同时**收窄补丁段**，让 Tailwind 的尺度恢复可信：

```diff
/* web/src/index.css:318-329 */
-/* Dense workbench controls still need real touch targets; scoped to avoid changing legacy layouts. */
-.refinement-workbench button,
-.refinement-workbench a.btn,
-.refinement-workbench select {
-  min-height: 40px;
-}
-.refinement-workbench .btn-icon {
-  min-width: 40px;
-  width: 40px;
-  height: 40px;
-  flex-shrink: 0;
-}
+/* 触摸目标由 min-h-touch / min-h-touch-sm 显式声明；此处不再对元素选择器全局兜底，
+   否则 .refinement-workbench button 的 (0,1,1) 会压掉内部所有 Tailwind 高度类。 */
+.refinement-workbench select {
+  min-height: var(--pw-touch, 44px);
+}
```

**注意（工程顺序）**：删除补丁会让人工检查遗漏的裸 `<button>` 掉到浏览器默认高度。**必须先扫出来补类**：
```bash
rg -n '<button' web/src/features/refinement --glob '*.tsx' -A3 | rg -v 'btn|min-h-'
```

---

## 7. 动效

### Framer Motion spring 参数：4 符合 + 1 违规 + 1 缺失

**【读到的】** `framer-motion` 导入 7 个文件，`motion.*` 组件共 11 个。显式 `transition` prop 只有 5 处：

| 位置 | 参数 | 判定 |
| --- | --- | --- |
| `web/src/components/ui/ToastProvider.tsx:52` | `{ type: 'spring', stiffness: 300, damping: 25 }` | ✅ 完全符合 AGENTS.md 5.2 |
| `web/src/features/refinement/components/SessionWorkbench.tsx:140` | 同上 | ✅ |
| `web/src/components/studio/CanvasTopToolbar.tsx:72` | 同上 | ✅ |
| `web/src/components/studio/CanvasBatchFilmstrip.tsx:42` | 同上 | ✅ |
| **`web/src/components/studio/MultiPersonMatrixModal.tsx:209`** | **`{ duration: 0.18, ease: 'easeOut' }`** | ❌ **违反规范** |

`MultiPersonMatrixModal.tsx:208` 有一行注释：
```tsx
// Framer Motion owns the transform property, so centering must live in
// its x/y values instead of Tailwind translate classes.
```
这个注释解释的是**为什么用 `x`/`y` 而不是 translate 类**，**不是**为什么用 tween——注释与参数不匹配，`spring` 同样能驱动 `x`/`y`。改法：

```diff
- // MultiPersonMatrixModal.tsx:209
-            transition={{ duration: 0.18, ease: 'easeOut' }}
+            transition={{ type: 'spring', stiffness: 300, damping: 25 }}
```
`initial`/`animate` 的 `x: '-50%'` / `y: '-50%'` 语义不受影响（spring 对百分比字符串同样生效）。

**【读到的】** 第 6 处缺失：`web/src/components/studio/SourceGallery.tsx:315-319` 的 `motion.div` 有 `initial`/`animate`/`exit` 但**无 `transition`**，落到 Framer Motion 默认过渡（tween 近似），不是 spring：
```diff
- // SourceGallery.tsx:315-319
+                        transition={{ type: 'spring', stiffness: 300, damping: 25 }}
                         initial={{ opacity: 0, y: 4 }}
                         animate={{ opacity: 1, y: 0 }}
                         exit={{ opacity: 0, scale: 0.96 }}
```
这是 `AnimatePresence mode="popLayout"` 的列表项（`:293`），加 spring 会让增删参考图的手感与其余交互相统一。

### `prefers-reduced-motion`：**MotionConfig 已经处理了，这不是缺陷**

**【读到的】** `web/src/main.tsx:27`：
```tsx
<MotionConfig reducedMotion="user">
```
`reducedMotion="user"` 让 Framer Motion 读取系统 `prefers-reduced-motion` 并自动降级 transform/布局动画，**覆盖了 JS 驱动的 transform**。

**【实测的】** `web/src/index.css:305-315` 的 `@media (prefers-reduced-motion: reduce)` 块确实**对 Framer Motion 无效**（它只管 CSS `animation-duration` / `transition-duration`），但这是**正确且必要的补充**：它管的是 CSS keyframes 动画（`animate-spin`、`animate-pulse`、`.fade-in`、`.zoom-in-95`、`index.css:290-302` 手写的三个 keyframes）和 CSS transition（`.btn` 的 `transition-all`、`input[type=range]` thumb、`::-webkit-scrollbar`）。

**所以：JS 侧由 `MotionConfig reducedMotion="user"` 负责，CSS 侧由 media query 负责，两者互补，覆盖完整。这一项主会话的判断需要修正——不是真实缺陷。**

**唯一的遗漏**：`canvas-confetti`（`package.json` 依赖，用于成功反馈）不读 reduced-motion 偏好。若使用需手动 guard：
```ts
const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
if (!prefersReduced) confetti({ ... });
```
（`useManualProtectionBrush.ts` 的 Canvas 2D 绘制是画笔轨迹，本身不是动画，不构成问题。）

---

## 8. 字体加载与 CLS

### 离线/内网部署的降级表现

**【读到的 + 实测的】**

- **⚠️ 行号更正**：三个外链实际在 **`web/index.html:8,9,10`**（第 5 行是 data-URI favicon，第 7 行是 `<title>`）。第 10 行含 `display=swap` 与 `family=Inter:wght@300..700&family=JetBrains+Mono`。
- **`web/` 下无任何 `.woff/.woff2/.ttf/.otf`**（全量 `find` 为零）
- **`web/public/` 是空目录**
- `web/package.json` 无 `@fontsource/*` 依赖
- 全项目 `font-display` / `@font-face` **各 0 命中**；`index.css:24` 的 `body{@apply ... font-sans ...}` 是 `sans` 栈的唯一启用点
- **构建产物 `dist/index.html:8-10` 仍保留三个 Google Fonts 外链**——字体从未被 Vite 处理或自托管

**结论（明确）**：内网/离线部署时 `/css2?family=Inter...` 请求失败，浏览器走 `tailwind.config.ts` 的 `fontFamily.sans` 兜底链 `Inter → -apple-system → BlinkMacSystemFont → "Segoe UI" → Roboto → "PingFang SC" → "Hiragino Sans GB" → "Microsoft YaHei" → "WenQuanYi Micro Hei" → sans-serif`。由于列表里没有本地 `local()` 源，`Inter` 这一项直接跳过，落到系统字体。**Linux 内网容器若没有 `WenQuanYi Micro Hei`，最终落到 `sans-serif`**（DejaVu Sans 或 Liberation Sans），中文字形甚至可能缺字。

### ⚠️ 重要澄清：离线场景**不产生** swap 型位移

主会话提出的"字体度量差异导致布局抖动 CLS"需要收窄。**实测依据**：产物中 Tailwind 的字号工具类全部带**固定** `line-height`——`.text-2xs{font-size:11px;line-height:16px}`、`.text-xs{.75rem/1rem}`、`.text-sm{.875rem/1.25rem}`、`.text-base{1rem/1.5rem}`。**行盒高度被锁死，字体切换不改变行高。**

结论：
- **离线/内网（字体永远拿不到）**：Inter 从未被应用，不存在"先 fallback 后 swap"的两次渲染，**CLS ≈ 0**。差异只是字宽与字形。
- **联网但 Google Fonts 慢**：才有 `display=swap` 的两次渲染，位移来源是**字宽重排**，且**只影响 ASCII/数字**——Inter 的 Google Fonts 子集不含 CJK 字形，**中文字符从一开始就由 fallback 中文字体渲染**，不受切换影响。

**这修正了 CLS 的定位**：真正的风险点是**宽度由文本决定、且没有固定宽度的容器**，而不是行高。重点核查对象：`SessionWorkbench.tsx:92`（header）、`:438`（版本轨道）、各 `flex-wrap` 按钮组。

**我未能给出"差多少 px"的精确值**——Inter 的官方度量有两个口径不一致的来源（[rsms/inter 历史度量](https://github.com/rsms/inter/blob/master/misc/historical-metrics.md) 记 UPM 2816 / Asc 2728 / Desc −680；[rsms.me](https://rsms.me/inter/) 记 Cap 1490 / Asc 1984 / Desc −494）。**凭记忆给像素级断言不可接受**，必须用本地 ttf 实测：

```js
document.fonts.check('16px Inter');
document.fonts.check('16px "JetBrains Mono"');
// 实际命中的字体：DevTools → Elements → Computed → Rendered Fonts
// CLS 具体值：在 Inter 加载前后对比 getBoundingClientRect().width
```

**另一个未实测项**：`<link rel="stylesheet">` 是渲染阻塞资源，离线且 DNS 挂起时会**延后 FCP**（秒数取决于 DNS/connect 超时），本次未测量。

### 等宽字体是更值得关注的 CLS 来源

`fontFamily.mono` 链是 `JetBrains Mono → Menlo → Consolas → monospace`。Menlo/Consolas 是 macOS/Windows 专属，Linux 上直接落到 `monospace`（DejaVu Sans Mono）。JetBrains Mono 与 DejaVu Sans Mono 的字宽不同，且**中文全角字符在不同等宽字体中的进位不同**。

受影响的可见元素：

| 位置 | 元素 | 影响 |
| --- | --- | --- |
| `web/src/App.tsx:150` | `chip chip-accent ... font-mono` 任务数 | 数字宽度 + 徽章背景宽度 |
| `web/src/features/refinement/components/SessionComposer.tsx:166` | `shrink-0 tabular-nums` 字数计数 | **已用 `tabular-nums`，这是正确做法** |
| `web/src/features/history/components/RecentJobsPopover.tsx:137` | `text-2xs` 计数 | 无 `tabular-nums` |
| `web/src/features/projects/ProjectHistory.tsx:71` | `text-2xs` 日期 | 无 `tabular-nums` |
| `web/src/components/studio/CanvasTopToolbar.tsx:144` | `font-mono` 快捷键 | 表格对齐 |
| `web/src/components/studio/ParameterPanel.tsx:179` | `text-2xs font-mono` 滑块刻度 | 三个 span 的对齐 |

### 可落地的改法（按成本递增）

**方案 A（最小）——显式声明离线优先的本地字体源**：
```html
<!-- web/index.html，插在 :8 的 preconnect 之前 -->
<style>
  /* 内网/离线：优先本地已装字体，避免 Inter 缺失时落到 DejaVu Sans 造成 mono/metric 抖动 */
  @font-face {
    font-family: 'InterLocal';
    src: local('Inter'), local('Inter Variable'), local('PingFang SC'), local('Microsoft YaHei');
    font-display: swap;
    unicode-range: U+0000-00FF, U+4E00-9FFF;
  }
</style>
```
并把 `tailwind.config.ts` 的 `fontFamily.sans` 首位改成 `'InterLocal'`。**风险**：`unicode-range` 覆盖 CJK 后中文也走 `InterLocal`，若本地字体不含 CJK 会落到下一项。**这是【推测的】改法**，建议先在目标内网环境做一次字体可用性探测。

**方案 B（推荐，彻底）——自托管字体，移除外链**：
```bash
pnpm add -D @fontsource-variable/inter @fontsource/jetbrains-mono
# main.tsx 顶部
import '@fontsource-variable/inter';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
```
并删掉 `web/index.html:8-10` 三个 link。收益：零外网依赖、`font-display: swap` 由 fontsource CSS 保证、Vite 自动 hash 文件名可长缓存。

**方案 C（CLS 专项，无论 A/B 都建议加）——给数字元素补 `tabular-nums`**：
```diff
-                      <p className="mt-1 text-2xs text-zinc-600">          // RecentJobsPopover.tsx:137
+                      <p className="mt-1 text-2xs tabular-nums text-zinc-400">
```
```diff
-              <p className="mt-1 text-2xs text-zinc-500">{new Date(project.updated_at).toLocaleString('zh-CN')}</p>   // ProjectHistory.tsx:71
+              <p className="mt-1 text-2xs tabular-nums text-zinc-400">{new Date(project.updated_at).toLocaleString('zh-CN')}</p>
```
（第二处同时应用了 §5 的对比度修正。）`SessionComposer.tsx:166` 已经这么做了，是全项目的正确范例。

---

## 9. 组件体积与样式可维护性

### `TargetCanvas.tsx`（764 行）的样式职责拆分点

**【读到的】** 组件结构：

| 行段 | 职责 | 是否含样式 |
| --- | --- | --- |
| `:1-30` | 导入 + 7 个 ref/state 声明 | 否 |
| `:31-180` | Zustand 取数（解构 20+ 值） | 否 |
| `:180-360` | 6 个事件处理（文件读取、拖拽、指针、VLM 仲裁） | 否 |
| `:360-432` | 派生值（`currentImage` / `detectedFaces` / `distinctGlobalPersons`） | 否 |
| `:433` | 根容器 | 1 处 |
| `:434-470` | 两个工具栏挂载 | 透传 props |
| `:471-497` | **拖拽遮罩（dropzone overlay）** | **大量内联类名** |
| `:498-520` | **空状态（empty state）** | **大量内联类名** |
| `:521-556` | 图片视口 + 图片 + 对比层 | 中等 |
| `:557-600` | **状态横幅：hold-to-compare / clustering / VLM** | **大量内联类名** |
| `:601-690` | 对比分隔器（slider，含 pointer/keyboard 处理） | 中等 |
| `:691-700` | 画笔画布 | 少量 |
| `:701-740` | **放大镜（magnifier）** | **大量内联类名 + 3 个内联 style** |
| `:741-764` | 结果操作条 + 底片条挂载 | 透传 props |

**【推测的，基于结构】拆分建议**——按"变化原因"而非行数（符合 AGENTS.md 7.12）：

1. **`:471-520` → `CanvasEmptyState.tsx`**（约 50 行）。变化原因：空状态/拖拽态视觉设计。它已是纯展示（一个 `onClick` 回调 + `isDragOver` 布尔），抽取后无状态耦合。
2. **`:557-600` → `CanvasStatusBanners.tsx`**（约 45 行）。变化原因：三个横幅（原图对比提示 / 聚类中 / VLM 结果）**显示条件互相独立**（`isHoldingOriginal && ...` / `isClustering || clusteringError` / `vlmReasoning`），是典型的"三合一"。
3. **`:701-740` → `CanvasMagnifier.tsx`**（约 40 行）。变化原因：放大镜是自成一体的几何计算（3 倍缩放 + 4 个内联 style 偏移）。**内部无任何 store 依赖**，只读 `magnifierData` / `currentImage` / `isHoldingOriginal`。这是最干净的一刀。
4. **`:601-690` → `CanvasSplitDivider.tsx`**（约 90 行）。变化原因：它包含独立的 pointer capture 状态机 + 键盘 ARIA 逻辑（`role="slider"` / `aria-valuemin/max/now/text` / 四键 `onKeyDown` / 5 个 pointer 事件）。**这是 TargetCanvas 里唯一带完整交互状态机的部分**，且 `updateSplitFromPointer`（`:244-250`）是它专用。

**不建议拆的**：
- `:521-556`（图片视口）——与 `imageViewportSize` / `imageContainerRef` / `onLoad` 的尺寸写回强耦合，拆分需传 5+ props 和 2 个 ref，是"为拆而拆"。
- `:180-360`（事件处理）——handler 全要访问 store 写入方法，抽 hook 会引入更大接口面。若真要拆，只有 `:300-360` 的 `handleVlmArbitrate`（60 行、只依赖 `api.arbitrateIdentity` 和 `relabelFaceSafely`）适合独立成 `useVlmArbitration.ts`。

**预估收益**：拆出 1–4 项后 `TargetCanvas.tsx` 从 764 行降到约 **530 行**，4 个新文件各自 < 100 行、职责单一、可独立测试。

### 其他巨型组件的样式职责

| 文件 | 行数 | 样式职责问题 |
| --- | --- | --- |
| `SourceGallery.tsx` | 631 | 含 4 个 Radix Dialog（`:423`/`:561`/`:585` + 内嵌），每个都有 `z-[70]`/`z-[90]` 与独立 overlay。可抽 `GalleryDialogShell` 统一 overlay/content 类名 |
| `ParameterPanel.tsx` | 620 | 样式最密集：`Section` 子组件（`:95-130`）自带 `accentClasses`，滑块子组件（`:160-185`）自带 `accentColor` 硬编码。**这两个子组件是 §1 收敛的核心** |
| `HistoryWorkspace.tsx` | 510 | `:416` 的 `chip chip-sm` 无效类（§2）；`:386` 的选中态用 `bg-primary/5` 而非 `primary-muted` token |
| `SessionWorkbench.tsx` | 497 | pane 切换用 `(pane === 'x' ? 'flex' : 'hidden') + ' ... lg:flex'`（`:210`/`:306`/`:472`）字符串拼接，是 `cn()` 的典型场景 |
| `MultiPersonMatrixModal.tsx` | 425 | 唯一 tween 动效（§7）+ `z-[90]`/`z-[91]` |

### 附：`z-index` 层级散乱（新增发现）

**【读到的】** 全部使用任意值，无中心化层级表：

| 值 | 位置 |
| --- | --- |
| `z-30` | `TargetCanvas.tsx:571,698` |
| `z-40` | `TargetCanvas.tsx:562,584` |
| `z-50` | `CanvasFaceMarkersOverlay.tsx:107`、`AppHeader.tsx:20` |
| `z-[70]` / `z-[71]` | `ResponsiveWorkflowAccess.tsx:95`、`SourceGallery.tsx:423,424` |
| `z-[80]` | `ResponsiveWorkflowAccess.tsx:99`、`RecentJobsPopover.tsx:62` |
| `z-[90]` | `MultiPersonMatrixModal.tsx:199`、`SourceGallery.tsx:561,585`、`CanvasTopToolbar.tsx:114` |
| `z-[91]` | `MultiPersonMatrixModal.tsx:210`、`SourceGallery.tsx:562,586`、`CanvasTopToolbar.tsx:115` |
| `z-[100]` / `z-[101]` | `TaskResultHistoryDialog.tsx:65,66`、`ToastProvider.tsx:73` |
| `z-[120]` | `ProjectControls.tsx:73` |
| `z-[200]` / `z-[201]` | `ToastProvider.tsx:43,46` |

**可落地改法**：在 `tailwind.config.ts` 补语义层级后机械替换：
```ts
      zIndex: {
        canvas: '30', 'canvas-banner': '40', 'face-marker': '50',
        drawer: '70', 'drawer-content': '71', 'app-drawer': '80',
        modal: '90', 'modal-content': '91', dialog: '100', 'dialog-content': '101',
        project: '120', toast: '200', 'toast-content': '201',
      },
```
把 `z-[80]` → `z-app-drawer`、`z-[90]` → `z-modal` 等。收益：不再出现 `SourceGallery.tsx:562` 的 `z-[91]` 与 `MultiPersonMatrixModal.tsx:210` 的 `z-[91]` **同层但互不可见**的隐患——两个弹层同时打开时的堆叠顺序取决于 DOM 顺序，不可控。

**另一个新发现**：`web/tailwind.config.ts:4` 配了 `darkMode: ['class']`，`index.html:2` 也挂了 `class="dark"`，但**全项目 `dark:` 前缀 0 命中**。这是一条死配置——深色是唯一形态（`index.css:6` 的 `:root{color-scheme:dark}` 已固定）。要么删掉 `darkMode`，要么保留它并在文档里说明"为将来可能的浅色模式预留"。**当前它不产生任何影响，但会误导后来者以为存在双主题。**

---

## 10. 若要落地：三步优先级清单

### 第一步（只改 4 个文件，收益最高）

**目标：修掉 2 个功能性缺陷 + 1 个规范违反，全部是"改了就有可见效果"的。**

1. **补 `slide-in-from-top-2` 定义**（问题 H-1）。在 `web/src/index.css` 的 `@layer utilities` 里，`.fade-in` 旁边加：
   ```css
   .slide-in-from-top-2 {
     animation-name: slideInFromTop2;
     animation-duration: 200ms;
     animation-timing-function: ease-out;
   }
   @keyframes slideInFromTop2 {
     from { opacity: 0; transform: translateY(-8px); }
     to   { opacity: 1; transform: translateY(0); }
   }
   ```
   或更彻底地 `pnpm add -D tailwindcss-animate` + `plugins: [require('tailwindcss-animate')]`。**前者零依赖、当前只差这一个类，推荐前者。**
2. **修阴影 token 被劫持**（§3 末裁决块）：先改 `tailwind.config.ts` 的 `boxShadow` 键名 `primary`/`cyan` → `glow-primary`/`glow-cyan`（消除与 `colors` 键的同名碰撞），再同步 `index.css` 的 `.btn-primary` / `.btn-accent`。**不要**按原稿把 `shadow-lg shadow-indigo-*` 改成 `shadow-primary` —— 那会把正常工作的 25% indigo 阴影换成被颜色插件劫持的 100% 不透明主色（详见 §3 裁决块）。
2b. **阴影类名收尾**：`ParameterPanel.tsx:555,592`、`TargetCanvas.tsx:666` 的 `shadow-lg shadow-indigo-*` → `shadow-lg shadow-primary/25` / `shadow-primary/80`（保留 `shadow-lg`，着色改用项目 token 的透明度变体）。
3. **修动效规范违反**：`MultiPersonMatrixModal.tsx:209` 的 tween → `{ type: 'spring', stiffness: 300, damping: 25 }`；`SourceGallery.tsx:315` 补上缺失的 `transition`（§7）。
4. **删无效类名**：`HistoryWorkspace.tsx:416` 的 `chip-sm`（§2）。

### 第二步（token 收敛 + 对比度，涉及面广但机械）

1. **补 2 个 token**：`tailwind.config.ts` 加 `canvas-deep: '#09090b'` 与 `zIndex` 层级表（§1 / §9）。
2. **换 3 处任意值背景**：`TargetCanvas.tsx:433`、`RefinementImageViewport.tsx:44` → `bg-canvas-deep`；`CanvasBatchFilmstrip.tsx:24` → `bg-surface/90 backdrop-blur-xl`（§1）。
3. **消 `!important` 与 `@apply` 链**：按 §2 的 diff 重写 `index.css` 的 `.btn-*`（10 个）/ `.chip-*`（7 个）/ `.seg-item-*`（2 个）/ `.val-badge-*`（3 个）四组，约 40 行改动。已核实调用处是 `btn` + 变体全写（**84 行**），去掉 `@apply btn` 行为不变。
4. **修对比度**：
   - ① `text-zinc-500` 的**正文**类（代表位置见 §5 表）统一 → `text-zinc-400`（六个底上 5.94–7.62 全通过，一次通杀）；
   - ② `text-zinc-600` 6 处 → **直接升到 `text-zinc-400`**（升一级到 `zinc-500` 只有 3.43，不够）；
   - ③ `index.css:229`（`@apply` 在 `:230`）`.seg-item-primary` 的 `!text-primary` → `text-indigo-300` `#a5b4fc`（7.17:1）。**不要用 `text-indigo-400`**（在 primary/15\|surface-hover 上只有 4.40）；
   - ④ `.chip-err`（`index.css:163`）/ `.val-badge-err` → `text-red-400` `#f87171`（5.30:1），**并同时改掉 `bg-err/10` 那 7 处使用点**（§5 发现 (2)）；
   - ⑤ `index.css:205`（`@apply` 在 `:206`）`.btn-accent` 的白字 → `text-zinc-950`，或直接删掉这个零使用的类（§5 发现 (1)）。
5. **替换 JS 硬编码色**：`ParameterPanel.tsx:148-150,172-176` 改 `data-accent` + CSS 变量（§1 片段）。

### 第三步（工程硬化）

1. **引入 `cn()`**：`pnpm add clsx tailwind-merge` + `web/src/utils/cn.ts`，改 `SessionWorkbench.tsx:210,306,472` / `ComparisonViewer.tsx:80,132`（§3）。
2. **触摸目标收敛**：`tailwind.config.ts` 加 `minHeight/minWidth.touch = 44px` / `touch-sm = 40px`；9 处 `style={{ minHeight: 44 }}` → `min-h-touch`；`min-h-9` → `min-h-touch-sm`；`min-h-11` → `min-h-touch`；然后收窄 `index.css:318-329` 的补丁段（§6）。
3. **响应式补档**：`App.tsx:200` 加 `md:grid-cols-[minmax(0,1fr)_300px]`，参数面板 aside 从 `xl:block` 提前到 `md:block`，同步 `ResponsiveWorkflowAccess.tsx:47` 的 `xl:hidden` → `md:hidden`（§6）。
4. **字体自托管**：`pnpm add -D @fontsource-variable/inter @fontsource/jetbrains-mono`，删 `index.html:8-10`；给数字元素补 `tabular-nums`（§8 方案 B + C）。
5. **拆 `TargetCanvas.tsx`**：按 §9 的 4 个拆分点（`CanvasEmptyState` / `CanvasStatusBanners` / `CanvasMagnifier` / `CanvasSplitDivider`）执行，不要按行数切。

---

## 附：验证方法（改完后跑）

```bash
cd web

# 1. 格式与类型（AGENTS.md 7.13 要求）
pnpm --dir web format
pnpm --dir web lint

# 2. 构建并核对产物（会写 dist/ —— 这是唯一被允许的写盘）
pnpm --dir web build

# 3. 逐项验证（对照本报告的"实测的"结论）
CSS=$(ls dist/assets/*.css)
grep -c 'slide-in-from-top-2' "$CSS"       # 第一步后应 ≥1（原为 0）
grep -o '\.btn{[^}]*}' "$CSS" | wc -l      # 第二步后 .btn 声明块应仍为 1，但 8 个 .btn-* 不再各自内联
grep -c '!important' "$CSS"                # 第二步后应为 0（原为 6）
grep -c 'chip-sm' "$CSS"                   # 应为 0（确认没写回）
grep -c '\.refinement-workbench a\.btn' "$CSS"   # 第三步后应降为 1（原为 9+8 条派生）

# 4. 单元与集成测试
pnpm --dir web test
```

**回归清单（第二步必做）**：`TargetCanvas.test.tsx`、`SessionWorkbench.test.tsx`、`HistoryWorkspace.test.tsx`、`ComparisonViewer.test.tsx` 都断言了 `data-testid` 与 `aria-*`；改 `index.css` 组件层时若动到 selector 结构需同步检查。

**浏览器核对（第二步后）**：用 DevTools contrast checker 复核三个代表位置——`ParameterPanel` 滑块徽章（`val-badge-primary`，改后应 ≥4.5）、`TargetCanvas` 空状态说明文字（改后应 ≥6.4）、`SessionComposer` 错误提示（`chip-err` 与 `bg-err/10` 两条路径，改后应 ≥4.5）。响应式用 768 / 900 / 1280px 三档截图确认无"双入口"。

---

## 附：完整命令清单（可复现本报告全部实测结论）

```bash
cd /home/yhr/workspace/dsh-workspace/PersonaWeave/web

# 断点统计
for p in sm md lg xl 2xl; do printf "%-6s %s\n" "$p:" "$(rg -o "\b${p}:[a-zA-Z0-9\[\]_.%/-]+" src --glob '*.tsx' -g '!*.test.*' | wc -l)"; done

# 默认调色板 vs 自定义 token
rg -o -N '\b(indigo|cyan|emerald|zinc|red|amber|rose|purple)-[0-9]{2,3}' src --glob '*.tsx' -g '!*.test.*' | wc -l
rg -o -N '\b(surface|surface-1|surface-2|surface-3|primary|accent|ok|warn|err|info|border-md)\b' src --glob '*.tsx' -g '!*.test.*' | wc -l

# 全部 ${} 插值（逐类核实用）
rg -o -N '\$\{[^{}]*\}' src --glob '*.tsx' -g '!*.test.*' | sort > /tmp/interp.txt && wc -l /tmp/interp.txt

# 产物反查
CSS=$(ls dist/assets/*.css)
grep -o '\.btn-primary{[^}]*}' "$CSS"
grep -o '\.seg-item-primary{[^}]*}' "$CSS" | head -3
grep -bo 'shadow-indigo-600\\/25' "$CSS"
grep -bo '\.shadow-lg{' "$CSS"

# 用当前源码重新编译（§0.2 的交叉验证；临时目录，不触碰仓库）
T=$(mktemp -d); mkdir -p "$T/src"
find src -name '*.tsx' -not -name '*.test.tsx' -exec cp --parents {} "$T/" \;
sed "s#content: \[.*\],#content: ['$T/src/**/*.tsx'],#" tailwind.config.ts > "$T/tw.config.ts"
printf '@tailwind base;\n@tailwind components;\n@tailwind utilities;\n' > "$T/full.css"
cat src/index.css >> "$T/full.css"
npx tailwindcss -c "$T/tw.config.ts" -i "$T/full.css" -o "$T/out.css" --minify

# 探针编译（验证类名是否真能被生成）
printf "export const probe='slide-in-from-top-2 fade-in zoom-in-95 animate-in chip-sm text-2xs';\n" > "$T/src/probe.ts"
T2=$(mktemp -d); mkdir -p "$T2/src"; cp "$T/src/probe.ts" "$T2/src/"
sed "s#content: \[.*\],#content: ['$T2/src/**/*.tsx'],#" tailwind.config.ts > "$T2/tw.config.ts"
printf '@tailwind utilities;\n' > "$T2/in.css"
npx tailwindcss -c "$T2/tw.config.ts" -i "$T2/in.css" -o "$T2/out.css"

# WCAG 对比度公式
#   相对亮度：sRGB 线性化 c<=0.03928 ? c/12.92 : ((c+0.055)/1.055)^2.4
#   L = 0.2126R + 0.7152G + 0.0722B ；对比度 = (L1+0.05)/(L2+0.05)
#   alpha 合成：C = a·Fg + (1−a)·Bg（在 sRGB 8bit 空间逐通道）
```
