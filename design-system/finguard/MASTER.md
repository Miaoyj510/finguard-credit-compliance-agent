# FinGuard 设计系统 · 主文件（MASTER）

> **使用方式**：做具体页面前先看 `design-system/finguard/pages/[page-name].md`；
> 有该文件则其规则**覆盖**本文件，没有则严格遵守本文件。
>
> 本文件由 UI/UX Pro Max（`ui-ux-pro-max`）检索结果生成后**人工修订**。
> 修订项与理由见文末「决策记录」——检索结果是**建议**，不是指令。

---

**项目**：FinGuard · 信贷内控合规审查与贷后风险 Agent 平台
**类别**：企业内部作业系统（银行合规 / 风控工作台）——**不是**营销落地页
**设计档位**：Variance 2/10（克制 / 极简）· Motion 2/10（细微）· Density 8/10（密集 / 看板）
**技术栈**：静态 HTML + 手写 CSS（设计 token）+ 原生 JS。**无构建、无 CDN 依赖、可离线、可打印**

---

## 1. 设计原则（这个产品特有，先于一切视觉决策）

| # | 原则 | 落到设计上 |
| --- | --- | --- |
| P1 | **AI 出结构化意见，人签字** | 界面上「AI 产出」与「人工签核」必须在视觉上分得开：AI 内容用可折叠次级样式，人工动作是页面唯一的高强调按钮 |
| P2 | **一切结论可回溯** | 每条意见旁必须同屏显示 `rule_id + version` 与事实引用的**取值 + 来源通道 + 置信度**；不藏在二级菜单里 |
| P3 | **人工闸门不可绕过** | 闸门状态用**闸门型控件**（⛔ 图标 + 剩余时限 + 资格指示），不能和普通待办混在同一个列表里 |
| P4 | **未判定必须比判定更显眼** | 「须人工判定」用琥珀色 + 独立区块，**不允许**被折叠默认收起——它是需要人动手的地方 |
| P5 | **密集但不拥挤** | 一屏能看到一条案件的全部要素；行高 32–36px，等宽字体只用于编号与金额 |

---

## 2. 配色（浅色 · Analytics Dashboard 蓝板）

| 角色 | Hex | CSS 变量 | 用途 |
| --- | --- | --- | --- |
| Primary | `#1E40AF` | `--color-primary` | 主色：签核按钮、选中态、链接 |
| On Primary | `#FFFFFF` | `--color-on-primary` | 主色上的文字 |
| Secondary | `#3B82F6` | `--color-secondary` | 次级强调：焦点环、标签底 |
| On Secondary | `#0F172A` | `--color-on-secondary` | 次级色上的文字 |
| Accent | `#D97706` | `--color-accent` | **琥珀 = 需人工介入**：须人工判定、超时倒计时、待裁决冲突 |
| On Accent | `#FFFFFF` | `--color-on-accent` | 琥珀底上的文字 |
| Background | `#F8FAFC` | `--color-background` | 页面底色 |
| Foreground | `#0F172A` | `--color-foreground` | 正文 |
| Card | `#FFFFFF` | `--color-card` | 卡片底 |
| Card Foreground | `#0F172A` | `--color-card-foreground` | 卡片内正文 |
| Muted | `#E9EEF6` | `--color-muted` | 表头底、次级区块底 |
| Muted Foreground | `#475569` | `--color-muted-foreground` | 次要文字（**正文禁用**，对比度不足） |
| Border | `#DBEAFE` | `--color-border` | 描边、分割线 |
| Destructive | `#DC2626` | `--color-destructive` | 破坏性动作、驳回、异常 |
| On Destructive | `#FFFFFF` | `--color-on-destructive` | 破坏性色上的文字 |
| Ring | `#1E40AF` | `--color-ring` | 键盘焦点环 |
| Success | `#15803D` | `--color-success` | 已签核 / 已归档 / 校验通过 |
| Success Soft | `#F0FDF4` | `--color-success-soft` | 通过态底色 |
| Warning Soft | `#FFFBEB` | `--color-warning-soft` | 需人工处置底色 |
| Danger Soft | `#FEF2F2` | `--color-danger-soft` | 异常 / 越界底色 |

**配色纪律**

- 正文**必须** `#0F172A`；`--color-muted-foreground` 只用于 12px 以上的辅助标签，**不用于正文**（对比度 4.5:1）
- **状态不能只靠颜色**：等级、闸门、判定态一律「颜色 + 文字 + 图标」三重表达（无障碍要求）
- 琥珀与红色**只用于需要人行动或出错**的地方；绿色只用于"已通过"——绿色出现太多会让"已签核"失去分量

---

## 3. 字体

| 角色 | 字体 | 说明 |
| --- | --- | --- |
| 标题 / 正文 | `IBM Plex Sans` | 金融、可信、克制（检索命中 "financial, trustworthy, banking, serious"） |
| 中文回落 | `Noto Sans SC` → `Microsoft YaHei` → `PingFang SC` | IBM Plex Sans **无汉字**，必须回落 |
| 编号 / 金额 / 版本 | `IBM Plex Mono` | `R-CR-013`、`3.2.0`、`820,000` 这类**要能逐字符核对**的值 |

```css
/* 字体只走本地回落链，不引入任何 @import / CDN（见决策记录 7） */
font-family: "IBM Plex Sans", "Noto Sans SC", "Microsoft YaHei", "PingFang SC", -apple-system, "Segoe UI", sans-serif;
```

> 机器上若装有 IBM Plex 系列则优先使用；未安装时**直接落到系统中文字体**，
> 不向任何外部域名发请求——行内内网与答辩断网场景下首屏零外网依赖。

**字号阶（12 14 16 18 24 32，模块化，不许随手取值）**

| Token | 值 | 用途 |
| --- | --- | --- |
| `--fs-xs` | 12px | 辅助标签、页脚 |
| `--fs-sm` | 14px | 表格正文、次要说明（**正文下限，不可更小**） |
| `--fs-base` | 16px | 页面正文 |
| `--fs-lg` | 18px | 区块标题 |
| `--fs-xl` | 24px | 页面主标题 |
| `--fs-2xl` | 32px | 数字看板大数（仅用于 KPI） |

行高：正文 1.5，表格 1.35，标题 1.25。

---

## 4. 间距与密度（Density 8/10）

| Token | 值 | 用途 |
| --- | --- | --- |
| `--space-xs` | 2px | 图标与文字的贴合 |
| `--space-sm` | 4px | 徽标内边距 |
| `--space-md` | 8px | 表格单元格内边距（上下） |
| `--space-lg` | 12px | 卡片内边距、表头内边距 |
| `--space-xl` | 16px | 区块之间 |
| `--space-2xl` | 24px | 页面主区块之间 |
| `--space-3xl` | 32px | 页面外边距 |

- 表格行高 **32–36px**；卡片圆角 **6px**（Swiss 风格：直角偏多，不用大圆角）
- 阴影**克制**：只用 `0 1px 2px rgba(15,23,42,.06)` 一档，靠**描边 + 底色**分层，不靠阴影堆叠

---

## 5. 组件规范

| 组件 | 规范 |
| --- | --- |
| **闸门控件** | `⛔` 图标 + 闸门编号 + 剩余时限（琥珀）+ 签核人资格指示（✓/✗）+ 唯一高强调按钮。**闸门状态永远单独成块** |
| **覆盖度条** | 三段式（已判定 / 不适用 / 未判定）横向条 + 数字。**未判定段用琥珀**，不适用段用中性灰，不用色阶 |
| **意见卡片** | 头部：严重度徽标 + 意见编号 + 规则编号版本；中部：理由、事实引用表、依据出处；底部：AI 段落（次级）+ 人工操作区（高强调） |
| **规则版本标签** | 等宽字体。**「须人工判定」的规则用琥珀标签**，不是普通灰标签 |
| **事实引用表** | 三列：字段路径（等宽）/ 取值（等宽）/ 来源 + 置信度。置信度低用琥珀，高用次要灰 |
| **预警行** | 等级 + 提前量（等宽）+ 触发规则版本 + 核查结论 + 四档处置按钮组 |
| **回放时间轴** | 节点横向排列，闸门节点用 `⛔` 且**不可点击跳过**（tooltip 说明"人工闸门，不可绕过"）；点击节点展开当时快照 |
| **按钮** | 主操作实心蓝；次操作描边；破坏性红色。**同一屏只允许一个实心主按钮** |
| **状态徽标** | `role="status"` + `aria-atomic="true"`，播报完整语义（如「2 条规则须人工判定」），不播裸数字 |

---

## 6. 动效（Motion 2/10）

| 用途 | 时长 | 缓动 |
| --- | --- | --- |
| hover / focus 反馈 | 150–200ms | `ease-out` |
| 折叠展开 | 200–250ms | `ease-out` |
| 闸门状态变化 | 250ms | `ease-out` |

- 动效只用于**说明状态变化**，不做装饰性动画
- **必须**尊重 `prefers-reduced-motion`：命中时关闭所有过渡
- 禁止对 `width` / `height` 做动画（引起布局抖动）

---

## 7. 无障碍（Priority 1，最高优先级）

- 正文对比度 ≥ 4.5:1，大字 ≥ 3:1；焦点环**不可移除**
- 所有可点击元素 `cursor: pointer`；点击目标 ≥ 44×44px（密集表格里的行内按钮可 32×32，但要保证行高足够）
- **不用 emoji 当图标**——统一用 SVG（Phosphor Outline 风格）
- 装饰性 SVG `aria-hidden="true"`；承载信息的 SVG 给可访问名称
- 表格在窄屏用 `overflow-x-auto` 包裹，**不允许**撑破布局
- 键盘可完成全部主流程（打开详情 → 签核 / 退回）

---

## 8. 响应式断点

| 断点 | 布局 |
| --- | --- |
| 375px | 单列；表格横向滚动；侧边栏收成顶部条 |
| 768px | 单列 + 侧栏抽屉 |
| 1024px | 双栏（主内容 + 右侧待办栏） |
| 1440px | 三栏（导航 + 主内容 + 待办栏） |

---

## 9. 交付前检查清单

- [ ] 没有 emoji 当图标（全部 SVG）
- [ ] 所有可点击元素 `cursor: pointer`，有 hover 态
- [ ] 正文对比度 ≥ 4.5:1；`--color-muted-foreground` 未用于正文
- [ ] 键盘焦点可见，`Tab` 顺序合理
- [ ] `prefers-reduced-motion` 已处理
- [ ] 响应式：375 / 768 / 1024 / 1440 四档都看过
- [ ] 表格窄屏有 `overflow-x-auto`
- [ ] **状态不只靠颜色**（颜色 + 文字 + 图标）
- [ ] 打印样式可用（导出取证包用）——浅色底正因此成为硬要求
- [ ] 页面**无外网依赖**（字体、图标、脚本全部本地或内联）

---

## 决策记录（为什么改了自动生成的结果）

| # | 自动生成 | 实际采用 | 理由 |
| --- | --- | --- | --- |
| 1 | Category: Smart Home/IoT Dashboard | 企业内部作业系统 | 检索命中的是**智能家居**，与金融合规无关；品类判断错误，检索词重写为 "admin dashboard compliance review operations console" 后重取 |
| 2 | Pattern: Enterprise Gateway / Real-Time Landing | **不采用 pattern** | 两次匹配都是**营销落地页**结构（Hero / 行业方案 / Logo 墙 / Contact Sales）。本产品是内部工作台，无访客落地页；只采纳其 STYLE、COLORS、TYPOGRAPHY、EFFECTS、AVOID、CHECKLIST |
| 3 | 深色板（bg `#0F172A`） | **浅色蓝板**（Analytics Dashboard） | ① 四个界面都是密集文本 + 表格 + 数字，深色底长时间阅读易错 ② PRD 10.2.4 要求**导出 PDF 取证包**，浅色底才可打印 ③ 浅色更符合银行内部系统惯例 |
| 4 | Fira Code / Fira Sans | **IBM Plex Sans + IBM Plex Mono** | Fira 偏技术/代码感；IBM Plex 的检索理由明写 "financial, trustworthy, banking, serious"，更贴金融合规。Mono 用于编号与金额，便于逐字符核对 |
| 5 | Tailwind（stack 检索） | **手写 CSS token** | `--stack html-tailwind` 两次检索**均未命中**（"data table list badge severity status label" 与 "table" 皆 0 结果），按 skill 要求明确退回通用默认；且答辩现场可能断网，Tailwind CDN 会让界面**当场塌掉**——改用本地 CSS 变量，离线可用、零构建 |
| 6 | 间距到 32px 止 | 保留 | Density 8/10 下不需要更粗的间距；粗细层级靠描边与底色区分 |
| 7 | Google Fonts `@import`（初稿自带） | **删除，纯本地回落链** | 评审 R-F1：与本文「无 CDN / 无外网依赖」自相矛盾；`@import` 被阻断时样式解析挂起等超时，首屏可感卡顿，且向境外域名发请求不符合行内内网部署前提。字体视觉差异由回落链吸收 |
| 8 | 琥珀 `#D97706` 同时用于文字与图形 | **文字改用 `--color-accent-text: #B45309`** | 评审 R-F10：#D97706 在白底 12px 粗体约 3.2:1，不达 WCAG AA 4.5:1；#B45309 约 5.0:1。图形（覆盖度条、边线）仍用 #D97706 |
| 9 | 宽屏 `220px 1fr 320px` 三列 | **删第三列** | 评审 R-F7：无任何页面提供右栏内容，≥1440px 凭空空出 320px（生成稿残留） |