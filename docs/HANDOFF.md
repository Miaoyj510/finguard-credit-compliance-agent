# 评审交接说明（HANDOFF）

> 每个切片完成后由实现方更新本文件，评审方据此复现与审阅。
> 格式固定，便于评审方批量处理。

---

## 切片 1　规则模型与三键版本绑定

| 项 | 内容 |
| --- | --- |
| **分支** | `feat/impl` |
| **提交** | 见 `git log feat/impl --oneline -1` |
| **切片目标** | 实现 PRD 第五章「核心机制一：规则版本化」的**规则模型**与**三键取版本**，即整个项目的判定入口 |
| **本次不含** | 规则内容的 DSL 求值（切片 2）、事实层（切片 3）、任何模型调用、任何数据库与网络依赖 |
| **验证命令** | `python scripts/verify.py --slice 1`（预期 **27 / 27 通过**） |

### 改了什么

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `src/finguard/domain/enums.py` | 新增 | 判定三态 `Verdict`、严重度 `Severity`、覆盖度 `CoverageFlag`、未判定原因码 `UnresolvedReasonCode` |
| `src/finguard/domain/rule.py` | 新增 | `Rule` 模型（不可变），构造期校验必填字段，含 `covers_product` / `covers_org` / `effective_on` |
| `src/finguard/domain/case.py` | 新增 | `TripleKey`（三键）与 `CaseState` 骨架 |
| `src/finguard/rules/registry.py` | 新增 | `RuleStore.bind()` 三键取版本；`RuleBinding` / `UnresolvedRule` / `BindingResult` |
| `src/finguard/rules/seeds.py` | 新增 | 6 组演示用**合成**规则（非真实制度条款），刻意覆盖各分支 |
| `tests/test_rule_model.py` | 新增 | 12 项断言：模型校验与范围判定 |
| `tests/test_rule_registry.py` | 新增 | 15 项断言：三键取版本的各条分支 |
| `scripts/verify.py` | 新增 | 唯一验证入口，含套件登记表与检查点模式 |

### 影响到什么功能与效果

| 层面 | 影响 |
| --- | --- |
| **功能** | 案件受理时能确定「本案适用哪一版规则」。这是后续所有判定、意见、回放的地基 |
| **对外效果** | 同一笔业务在不同时间点重跑，取到的规则版本**跟着业务发生日走**，而不是跟着「今天」走 |
| **合规效果** | 取不到唯一版本时输出「须人工判定」并入待定池，**绝不按最新版硬判**——避免用今天的规则审判当年的业务 |
| **可度量** | 直接支撑 PRD 指标 **Q-02（规则版本选择正确率 100%）** 与 **Q-09（须人工判定输出正确率 100%）** 的实现侧 |
| **不影响的** | 判定结果（切片 2）、事实质量（切片 3）、人审闸门（切片 5）、回放（切片 6） |

### 关键设计点（建议重点看这 5 处）

1. `Rule.__post_init__` —— 必填字段校验放在**模型构造时**，而不是发布流程里；`source_ref` 缺失直接拒绝，因为没有出处就没有引用可回溯。
2. `Rule.effective_on` —— 生效区间**左闭右开** `[effective_from, effective_to)`。边界语义写错会导致历史结论算错，是本切片最容易出错的地方。
3. `RuleStore.bind()` —— 三种匹配结果（1 / 0 / ≥2）分别进 `bindings` / `NO_VERSION_MATCH` / `AMBIGUOUS_VERSION`；重叠时必须列出候选版本供人工裁定。
4. `RuleStore.__init__` —— 同一 `rule_id + version` 重复入库直接拒绝（数据质量闸）；但**同一 rule_id 的不同版本允许共存**，这是版本化的前提。
5. `RuleStore.bind_case()` —— 对 `CaseState` 的绑定写回，留给切片 5 的状态机用。

### 验证脚本在本切片抓到的三个真问题（已修，留档供评审参考）

| # | 问题 | 处理 |
| --- | --- | --- |
| 1 | 测试期望「R-CR-13」为非法 rule_id，但按 `R-<域>-<数字序号>` 它本就该合法 | 修正断言为「缺段 / 小写 / 非数字序号」，并补一条合法格式的正例 |
| 2 | 重叠版本用例取了 `2026-10-28`，而 R-CR-041 的重叠窗口是 2025 年——**用例日期取错了窗口** | 用例改为遍历重叠窗口内的日期 |
| 3 | 重叠窗口末边界取到 `2025-12-31`，但区间左闭右开，当天旧版已失效，属唯一命中 | 边界改为 `2025-12-30`，并补「窗口结束后唯一命中 v1.1.0」的正例 |

> 第 2、3 条正好说明左闭右开语义的坑——**这类问题只有断言能抓到，靠肉眼读代码看不出来**。

### 有意留白（不是遗漏）

| 项 | 原因 | 计划 |
| --- | --- | --- |
| 种子规则是**合成条款** | 不使用任何真实机构制度 | 真实规则由 U5 制度管理岗在规则建模工作台维护（切片 9 的发布流程） |
| `CaseState` 只有骨架 | 事实快照、覆盖度、闸门状态分别属切片 3 / 4 / 5 | 切片 5 补齐并接状态机 |
| 无数据库、无网络依赖 | 切片 1 必须能在任何机器上零依赖跑起来 | 切片 9 接入 |
| `UnresolvedReasonCode` 预留了 `MISSING_FACT` / `LOW_CONFIDENCE` | 事实层切片 3 才会产生 | 切片 3 启用 |

### 评审方请确认

- [ ] 三键取版本的三条分支（1 / 0 / ≥2）是否覆盖了应有的全部情形
- [ ] 生效区间的左闭右开语义是否符合业务预期（业务发生日当天适用哪版）
- [ ] 「绝不按最新版硬判」是否在代码路径上被真正阻断，而不只是靠约定
- [ ] 模型层校验的字段清单是否与 PRD 6.1 FR-RAG-03 一致（有没有漏掉该强制为必填的）
- [ ] 断言是否覆盖了「不该发生的事」（如重复版本、倒置区间、缺出处）

### 评审方入口

| 项 | 值 |
| --- | --- |
| 评审工作目录 | `D:\桌面\AIagent_project\金融项目\.worktrees\review` |
| 评审分支 | `review/impl`（基于 `feat/impl` 的 `736ce8b`） |
| 先做什么 | `cd .worktrees/review && python scripts/verify.py --slice 1` 复现结果 |
| 只改什么 | `src/`、`tests/`、`scripts/`，以及本文件的「评审意见」段落 |
| 不改什么 | PRD、需求编号、验收标准、`docs/ROADMAP.md` |
| 提交后 | 停下，由实现方合并 `review/impl` → `feat/impl` 并复跑完整验证 |

### 评审意见（由评审方填写）

> 评审方：agent-b（review/impl）。评审基线 `a5eaceb` → `736ce8b`，深度审阅（核心逻辑约 500 行全读 + 探针验证）。
> **结论：实现与切片 1 目标一致（on target），5 处关键设计点成立，留档 3 个问题确认已修。**
> 发现 8 处数据质量闸/验证脚本缺陷，已在本分支修复并补回归断言（先红后绿）；4 处咨询项留实现方决策或后续切片。
> 修复后验证：**49 / 49 全部通过**（原 27 + 新增 22），完整回归与 `--checkpoint commit` 均退出码 0。

| # | 位置 | 问题 | 严重度 | 建议改法 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 1 | `src/finguard/domain/rule.py` `__post_init__` | `effective_from` 完全未校验：`None`/字符串/`datetime` 均可入库，到 `bind()` 才炸 `TypeError`。FR-RAG-03 与 7.2「版本三元组强制」均列其为必填 | 中 | 构造期校验 `type(effective_from) is date`（`datetime` 是 `date` 子类但与 `date` 比较会 TypeError，一并拒绝）；`effective_to` 非 None 时同样校验类型 | ✅ 已修（断言：缺/非日期/datetime 冒充被拒） |
| 2 | `src/finguard/domain/rule.py` `covers_product/org` | scope 误写为 str（`("小额经营贷")` 漏逗号）时 `in` 退化为**子串匹配**：探针证实 `covers_product("小额")=True` 且 `bind` 静默命中——用错规则判业务是合规事故类 | 中 | 新增 `_normalize_scope`：拒绝 str，tuple/list 规范化为 tuple，元素须非空、无首尾空白；`required_facts` 同治 | ✅ 已修（断言：字符串 scope/空白元素被拒，list 规范化） |
| 3 | `src/finguard/domain/rule.py` `RULE_ID_RE` | `match` + `$` 放过尾随换行：`"R-CR-013\n"` 与 `"R-CR-013"` 同版本可**双双入库绕过重复闸**（探针 P4b 证实）；`\d` 还放过全角数字 | 中 | 改 `fullmatch` + `re.ASCII` | ✅ 已修（断言：尾随换行/全角数字被拒） |
| 4 | `src/finguard/domain/rule.py` `source_ref`/`version` 校验 | 纯空白 `source_ref="   "`、`version="  "` 可入库：出处闸与版本身份形同虚设（`dsl` 有 strip 校验而这两处没有，不一致） | 中 | 补 `isinstance(str)` + `strip()` 非空校验 | ✅ 已修 |
| 5 | `src/finguard/domain/case.py` `TripleKey.__post_init__` | `business_date=None`/字符串可构造；`product_type="  "` 纯空白可构造。三键是版本选择唯一依据（PRD 4.3 受理必填），缺键/脏键让「可复算」失效 | 中 | 构造期校验 `business_date` 为 date、两键为非空无首尾空白字符串 | ✅ 已修（新增 `tests/test_case_model.py`） |
| 6 | `scripts/verify.py` 套件解析 | `--slice 2` / `--suite dsl` 跑**空套件**打印「0 / 0 全部通过」退出码 0（假绿），违反脚本自身设计约定第 3 条；CI 若按切片调用会被误导 | 中 | 显式请求未实现套件 → 提示「待实现」并退出码 2；混合请求只跑已启用套件并注记待实现项 | ✅ 已修（新增 `tests/test_verify_entry.py`，只测不运行套件的参数路径，避免递归） |
| 7 | `tests/` 覆盖缺口 | HANDOFF 关键设计点 5 `bind_case()` 写回**无任何断言** | 低 | 补写回正确性与可复算两条断言 | ✅ 已修 |
| 8 | `src/finguard/rules/registry.py` | `RuleBinding.effective_to: object`、`bind_case` 无类型标注；`Sequence`/`field` 未使用导入 | 低 | 改 `Optional[date]`、`CaseState` 标注，清理导入 | ✅ 已修 |
| 9 | `src/finguard/rules/registry.py` `bind()` 0 命中分支 | 「范围排除」（产品/机构不适用）与「时间版本缺口」共用 `NO_VERSION_MATCH` 且 detail 文案相同，但人工处置动作不同（确认不适用 vs 补发版本，见 F-01）。**语义本身是 ROADMAP 切片 1 验收标准（产品/机构不适用→须人工判定），不改**；仅提示待定池会常驻范围外规则 | 低（咨询） | 建议 detail 区分两种成因或加子原因码，供切片 4 覆盖度统计与 Q-09 金标口径定义时采用 | ⏸ 留实现方决策 |
| 10 | PRD FR-RAG-04 措辞 | 「多版本重叠时按生效区间优先级裁决」易被读成系统自动裁决。实现取保守路径（重叠一律须人工判定 + 列候选），与 FR-RAG-05 / F-01 / E2 一致，是正确取舍 | 低（咨询） | 建议 PRD 责任人在后续修订中明确「裁决」指人工裁决并记录依据；代码不改 | 📁 留档 |
| 11 | `src/finguard/domain/case.py` `CaseState` vs PRD 8.4/8.6 | 实现把 bindings/unresolved 分列；PRD 8.4 示例与 8.6 Handoff 表把 unresolved 放在 `rule_binding` 内并带原因码。骨架期可接受，但切片 5 补齐时须对齐口径 | 低 | 切片 5 定稿 CaseState/Handoff schema 时与 PRD 8.4/8.6 对齐（或 PRD 修订认可分列） | 📁 留切片 5 |
| 12 | `src/finguard/domain/case.py` | docstring 声称「三键受理后锁定不可修改」但 `CaseState` 可变、未强制；`CASE_ID_PREFIX="FG"` 定义后未使用（案件号格式未校验） | 低 | 锁定语义与案件号校验在切片 5 状态机落地；常量届时接线或删除 | 📁 留切片 5 |

#### 「评审方请确认」五项的答复

1. **三条分支是否覆盖全部情形**：1 / 0 / ≥2 结构上穷尽。0 命中分支混淆了两种成因（意见 9），语义按 ROADMAP 验收标准执行，不改。
2. **左闭右开是否符合业务预期**：符合。边界断言 + 探针确认 `effective_from` 当天取新版、`effective_to` 当天旧版已失效；`effective_to=None` 长期有效与 PRD 8.4 示例（`effective_to: null`）一致。
3. **「绝不按最新版硬判」是否被代码路径阻断**：是。`bind()` 仅唯一命中进 `bindings`；`version_of()` 未绑定抛 `KeyError`；全源码 grep 无 latest/max/取末位等回退路径。
4. **必填字段清单与 FR-RAG-03 一致性**：修复后覆盖 PRD 全部 7 个必填字段（另含 severity/dsl）。**修复前漏 `effective_from`**（意见 1）；`effective_to` 允许 None 有 PRD 8.4 示例背书。
5. **断言是否覆盖「不该发生的事」**：原 27 项覆盖重复版本/倒置区间/缺出处等；本轮新增 22 项覆盖空白值/类型冒充/换行绕过/str scope 子串误匹配/验证脚本假绿，全部先在旧代码上跑红再修复转绿。

#### 验证证据（本评审会话实际运行）

| 命令 | 结果 |
| --- | --- |
| `python scripts/verify.py --slice 1`（修复前复现） | 27 / 27，退出码 0 |
| 新增 22 条断言 + 扩展 1 条既有用例，在旧代码上运行 | 17 条新断言失败 + 既有用例 2 个新子例失败（合计 20 failures + 1 error），其余旧断言零回归 |
| `python scripts/verify.py --slice 1`（修复后） | **49 / 49**，退出码 0 |
| `python scripts/verify.py`（完整回归）/ `--checkpoint commit` | 49 / 49，退出码 0 |
| `python scripts/verify.py --slice 2` / `--suite dsl` | 退出码 2，不再输出「全部通过」 |
| 探针 P1—P14（构造期漏洞逐条复测） | 全部 `ValueError` 拒绝；正例对照（list 规范化/通配/唯一绑定）通过 |

> 给实现方：合并 `review/impl` → `feat/impl` 后，请把本文件切片 1「验证命令」的预期从 **27 / 27** 同步为 **49 / 49**（该段落属实现方，评审方未改）。PRD、ROADMAP 验收标准未动。

### 合并方式

```bash
# 评审方在自己的 worktree 里：
git checkout review/impl
# ... 审阅与修改 ...
git commit -am "review: 切片 1 评审意见"
# 提交后停下，由实现方合并回 feat/impl
```

---

## 待评审队列

| 切片 | 状态 | 等待评审 |
| --- | --- | --- |
| 1 | ✅ 已提交，待评审 | **是** |
| 2—9 | ⬜ 未开始 | 否 |
