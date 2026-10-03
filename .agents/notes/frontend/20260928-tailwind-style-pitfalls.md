# Tailwind 样式体系的几个坑已修复，其余“看起来该做”的重构有意不做

状态：有效
日期：2026-09-28

## 背景

一次只读审阅检查了 `web/` 的 token、`index.css` 组件层、对比度、动效和响应式。审阅发现的缺陷已在同日修复（变更见 `CHANGELOG.md` 的 2026-09-28 段）；原 75 KB 报告已删除，只保留下面会再次踩到的结论。

## 决策

- **阴影 token 不能与颜色键同名。** `boxShadow` 的键与 `colors` 键重名（如 `primary`、`cyan`）时，颜色插件会额外生成 `.shadow-<键>` 并重写 `--tw-shadow`，劫持阴影的几何与透明度（主按钮发光曾渲染成不透明纯色）。阴影键统一用 `glow-*`。
- **覆盖补丁用 `:where()` 归零特异性。** `.refinement-workbench` 的 40px 触摸目标补丁特异性 (0,1,1) 高于工具类 (0,1,0)，会把调用处写对的 44px 反向压回 40px。兜底样式必须不压过工具类；触摸目标统一为 `min-h-touch` / `min-h-touch-sm`。
- **动画类要有实际产物。** 项目没有启用 `tailwindcss-animate`，`slide-in-from-top-2` 一直零产物。缺一个类时在 `index.css` 本地补 keyframes，不为一个类引入插件。
- **文字色 token 以对比度为准。** `#5b5fef` 在 `primary/15` 底上仅 2.95:1，`#ef4444` 在 err 浅底上 3.89–4.45，均不达 AA：新增 `primary-soft`、`err-soft` 文字 token，`text-zinc-500` 正文一律用 `text-zinc-400`。
- **验证以构建产物为准，不以源码为准。** 判断“某类是否生效、规则是否重复”要在临时目录编译后查产物；只读源码曾得出“产物与源码不同步”的误判。

## 被放弃的方案

- **整体去掉 `@apply` 链**：`seg-item-*`、`icon-badge-*` 的调用点只写变体、不写基类（如 `PortraitLibraryModal.tsx` 直接写 `seg-item-primary`），去掉 `@apply` 会直接破坏样式。要做必须先让调用点补写基类。
- **引入 `cn()` / tailwind-merge**：当前没有实测的类名冲突造成的用户可见缺陷，收益不抵新增依赖；出现真实冲突时再引入。
- **zIndex 层级表、字体自托管、拆分 `TargetCanvas.tsx`**：未执行。拆分要按职责（空状态、状态横幅、放大镜、分割线）而不是按行数。
- **“动态类名拼接会破坏 JIT”**：逐处核实后 34 处全是完整字面量，不是缺陷。**“离线字体造成 swap 位移”**、**“缺少 `prefers-reduced-motion`”**：前者离线时不产生 swap 型位移，后者 `MotionConfig` 已处理。这些都不要再当缺陷提。

## 影响与验证

规范见 `AGENTS.md` 的前端一节；token 在 `web/tailwind.config.ts`，组件层在 `web/src/index.css`。修改样式后执行 `pnpm --dir web format` 与 `pnpm --dir web lint`，并检查构建产物中的规则条数。仍待处理且已知的问题：768–1023px 区间参数面板入口摩擦、等宽字体是更主要的 CLS 来源、`z-index` 散乱。
