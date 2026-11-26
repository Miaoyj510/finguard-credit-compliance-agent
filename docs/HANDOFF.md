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
| **验证命令** | `python scripts/verify.py --slice 1`（预期 **49 / 49 通过**：原 27 项 + 评审新增 22 项） |

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

## 切片 2　DSL 判定引擎

| 项 | 内容 |
| --- | --- |
| **分支** | `feat/impl` |
| **切片目标** | 实现 PRD 5.4 的规则 DSL **解析**与**确定性判定**：把规则表达式与事实求值成「命中 / 不命中 / 不适用」，并把一切异常降级成「未判定」 |
| **本次不含** | 事实层抽取（切片 3）、审查意见生成（切片 4）、模型调用 |
| **验证命令** | `python scripts/verify.py --slice 2`（预期 **58 / 58 通过**）；全量 `python scripts/verify.py` 预期 **107 / 107 通过** |

### 改了什么

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `src/finguard/rules/dsl.py` | 新增 | 手写词法分析 + 递归下降解析器；产出不可变语法树 `RuleExpression`。**不使用 eval / exec** |
| `src/finguard/rules/engine.py` | 新增 | `RuleEngine.evaluate()` 纯比较求值；`Evaluation` 不可变可哈希；引擎**永不抛异常** |
| `src/finguard/domain/enums.py` | 修改 | 新增两个未判定原因码 `DSL_PARSE_ERROR` / `TYPE_MISMATCH` |
| `src/finguard/rules/registry.py` | 修改 | 新增 `RuleStore.get(rule_id, version)`：按版本精确取规则，取不到直接报错，**不静默回退到别的版本** |
| `src/finguard/rules/__init__.py` | 修改 | 导出新模块 |
| `tests/test_dsl.py` | 新增 | 24 项断言：正例解析、反例精确报错、**不执行任意代码** |
| `tests/test_engine.py` | 新增 | 20 项断言：三态、降级、严重度来源、可复算、批量隔离 |
| `tests/test_verify_entry.py` | 修改 | 评审方测试的**去状态依赖**改造（见下） |
| `scripts/verify.py` | 修改 | 注册 `dsl` 套件 |

### 影响到什么功能与效果

| 层面 | 影响 |
| --- | --- |
| **功能** | 规则第一次能真的跑出结论；「取到哪一版规则」与「这一版判成什么」打通了 |
| **对外效果** | 结论**可复算**：同一规则 + 同一事实 → 同一结果，可逐条向审计复现 |
| **合规效果** | ① 判定权只在规则引擎，模型不参与（FR-RAG-01）② 严重度只能取自规则库，模型不得自拟（FR-AUDIT-03）③ 坏规则**不崩整批**，降级为「未判定」并带原因码（8.8 F-02）④ 规则内容是外部文本，**当数据不当代码**，不执行 |
| **可度量** | 支撑 PRD 指标 **Q-02（版本选择正确率）**、**Q-04（人工校验一致率）**、**Q-09（须人工判定正确率）** 的实现侧；覆盖度三分类首次可算 |
| **不影响的** | 事实质量（切片 3）、意见生成与引用闸门（切片 4）、闸门（切片 5） |

### 关键设计点（建议重点看这 5 处）

1. **不用 eval / exec** —— `dsl.py` 里手写 tokenizer + 递归下降解析器。测试里直接断言 `import os`、`__import__('os').system(...)`、`eval('1+1')` 全部被拒，并断言源码中不出现 `eval(` / `exec(`。规则 DSL 是外部内容，这是硬要求。
2. **解析期严格** —— `IF`/`THEN` 必须大写、`hit` 必须小写、label 必须是字符串、尾部有多余内容直接报错。拼写错误在**规则入库时**暴露，而不是等到某天规则跑到才静默走偏。
3. **引擎永不抛异常** —— 解析失败、事实缺失、类型不可比、引擎内部异常，一律降级成「未判定」并带 `reason_code`。批量判定里一条坏规则不影响其他规则（有断言）。
4. **类型不可比 ≠ 判 False** —— `"十万" < 100` 这种不返回「不命中」，而是「未判定 / TYPE_MISMATCH」。布尔值不算数字（`bool` 是 `int` 子类，单独排除）。
5. **`context` 的语义写进了契约** —— 传 `TripleKey` 则引擎**复核**一遍适用范围（防御性）；传 `None` 表示「调用方已完成三键绑定」，引擎不再复核。范围判定的唯一权威仍是 `RuleStore.bind()`。这条语义有测试钉住，不是只写在注释里。

### 本轮改动到评审方的一处代码（需要评审方确认）

`tests/test_verify_entry.py` 里，评审方把「未实现的切片」**写死**成 `--slice 2` 与 `--suite dsl`。切片 2 一实现，这两条断言就失效（退出码从 2 变成 0），全量回归直接红 2 条。

改造方式：**不写死编号** —— 从 `verify.SUITES` 注册表里动态找出当前第一个仍未实现的套件；若将来全部实现，则跳过而不是失败。评审意图（未实现切片不得输出假绿）完全保留，且不再依赖世界状态。

> 这条本身也印证了评审方那条「测试要钉住行为、不要钉住状态」的判断——**这次是它自己踩了这个坑**。

### 有意留白

| 项 | 原因 | 计划 |
| --- | --- | --- |
| DSL 只支持单条 if-then | PRD 只要求可判定的 if-then；组合规则（AND/OR/决策表）会另起一个切片 | 切片 12（改进清单 #2） |
| 未做表达式缓存的跨进程共享 | 进程内 dict 缓存已够用，且要保持判定可复算 | 需要时再引入 |
| 事实为 None 一律当缺失 | 目前没有「显式空值」的业务含义 | 切片 3 事实层补充语义时再定 |
| 规则取用接口尚未暴露给工具层 | 切片 2 只做库内判定，不接工具层 | 切片 4 / 9 接入 |

### 评审方请确认（评审方已逐条答复）

- [x] DSL 语法是否够用 → 够用，AND/OR 与决策表按留白计划走后续切片，不提前建
- [x] 严格性是否太难用 → 可接受；建议切片 9 规则工作台提供实时解析预览
- [x] `context=None` 是否改必填 → 不改；**切片 4/5 调用方一律传 context**（纵深防御）
- [x] 「永不抛异常」是否掩盖问题 → 评审修了两处洞；现边界是「坏数据降级、调用方 bug 抛 TypeError」
- [x] 五个产品不变量 → ①②⑤ 在本切片成立；②③④ 属切片 3/4/5

### 评审方入口

| 项 | 值 |
| --- | --- |
| 评审工作目录 | `D:\桌面\AIagent_project\金融项目\.worktrees\review` |
| 评审分支 | `review/impl`（基于 `feat/impl` 的 `0af3ec6`） |
| 先做什么 | `cd .worktrees/review && python scripts/verify.py --slice 2` 复现结果 |
| 只改什么 | `src/`、`tests/`、`scripts/`，以及本文件的「评审意见」段落 |
| 不改什么 | PRD、需求编号、验收标准、`docs/ROADMAP.md` |
| 提交后 | 停下，由实现方合并 `review/impl` → `feat/impl` 并复跑完整验证 |

### 评审落地记录（评审方 6 处缺陷 + 实现方 5 项咨询决策）

评审方修复（提交 `76e5764` / `016e2b1`，评审后切片 2 为 55 项）：

| # | 问题 | 处理 |
| --- | --- | --- |
| 1 | **NaN 静默判定**：`nan < 100` → 未命中、`nan != 100` → 命中 | NaN 单独拦截 → 未判定 / TYPE_MISMATCH；inf 视为合法无界值并补断言 |
| 2 | **契约边界有洞**：`rule=None` 抛 AttributeError；`facts=None` 被误标 DSL_PARSE_ERROR | 非 Rule / 非 Mapping 抛 TypeError（调用方 bug 该抛）；`facts=None` 视为空事实走 MISSING_FACT |
| 3 | `hit('')` 空 label 可解析 | 解析期拒绝空 / 纯空白 label |
| 4 | 数字字面量正则是 Unicode 模式，全角数字被静默归一 | number 模式改 ASCII 数字类 |
| 5 | 「可哈希」仅对可哈希事实值成立；未判定也标 NOT_APPLICABLE 易被误读 | 写入 docstring：**下游区分不适用 / 未判定必须读 coverage / reason_code** |
| 6 | `assertRaises(Exception)` 过宽 | 收紧为 AttributeError；补 inf / 字符串字典序 / 转义还原断言 |

实现方对 5 项咨询的决策（已全部落地，断言 55 → 58）：

| # | 咨询项 | 决策 | 落地方式 |
| --- | --- | --- | --- |
| 7 | 兜底分支误标 `DSL_PARSE_ERROR`，建议加 `ENGINE_ERROR` | **采纳**。PRD 只要求「原因码非空」，未枚举封闭码表，故无需改 PRD | 新增 `UnresolvedReasonCode.ENGINE_ERROR`；兜底分支改用它；新增断言（mock 引擎内部异常） |
| 8 | 未知转义静默透传 | **采纳**——严格性要在解析期兑现 | `_unquote` 改为扫描式，只认反斜杠、单引号、双引号三种转义，其余抛 DslParseError；新增断言 |
| 9 | 重复字段名 `fact.a < fact.a` 输出「a、a」 | **采纳**——去重保序 | 在 `Comparison.field_names()` 一处去重，fact_refs / reason / values 一次性全部受益；新增断言 |
| 10 | push.ps1 默认推 main、路径硬编码、令牌暴露面 | **采纳**——补闸门 + 可配置 + 说明 | 推送前**强制跑完整回归，不绿不许推**（ROADMAP 检查点要求）；git / gh 路径可用 `FINGUARD_GIT` / `FINGUARD_GH` 覆盖；脚本头注明令牌暴露面与 `gh auth logout` 吊销方式 |
| 11 | `import verify` 触发 stdout/stderr 重配副作用 | **采纳** | `reconfigure` 移入 `main()`；被当库导入时无任何副作用 |

> 咨询 7 的补充说明：`src/finguard/domain/enums.py` 原注释写「改动需同步 PRD」。经核对，PRD 第 515、1085 行等处只要求「未判定项必带原因码 / 原因码非空」，**并未枚举封闭码表**，因此新增码不构成 PRD 变更；已把该注释改为如实描述。

### 合并方式

```bash
# 评审方在自己的 worktree 里：
git checkout review/impl
# ... 审阅与修改 ...
git commit -am "review: 切片 2 评审意见"
# 提交后停下，由实现方合并回 feat/impl
```

### 评审意见（由评审方填写）

> 评审方：agent-b（review/impl，已快进至交付点 `0af3ec6`）。深度审阅：dsl.py / engine.py 全读 + 14 组探针 + push.ps1 工具提交审查。
> **结论：实现与切片 2 目标一致（on target），5 处关键设计点成立，无 eval/exec 经源码与断言双重确认。**
> 发现 6 处缺陷已修复并补回归断言（先红后绿）；5 处咨询项留实现方决策或留档。
> 修复后验证：**切片 2 55 / 55、全量 104 / 104 全部通过**，`--checkpoint commit` 退出码 0。
> **对实现方改动评审方代码的确认**：`test_verify_entry.py` 去状态依赖改造（动态找未实现套件、全实现则跳过）**认可**——评审意图保留，不再依赖世界状态；一处副作用见意见 11。

| # | 位置 | 问题 | 严重度 | 建议改法 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 1 | `src/finguard/rules/engine.py` 比较路径 | **NaN 静默判定**：探针证实 `nan < 100` → MISS/已判定、`nan != 100` → HIT/已判定。无效数据产出确定结论，违背「类型不可比 ≠ 判 False」的设计精神，切片 3 抽取若漏进 NaN 即成错判 | 中 | NaN 单独拦截 → 未判定/TYPE_MISMATCH（原因文案明确）；`_comparable` 双重防御；inf 视为合法无界值并补断言钉住 | ✅ 已修 |
| 2 | `src/finguard/rules/engine.py` `evaluate()` | 「永不抛异常」契约有洞：`rule=None` 抛 AttributeError（兜底构造 Evaluation 时二次炸）；`facts=None`/非 Mapping 被兜底降级但原因码**误标 DSL_PARSE_ERROR**，审计口径失真 | 中 | 契约边界写清：非 Rule / 非 Mapping 抛 TypeError（调用方 bug 该抛）；`facts=None` 视为空事实走 MISSING_FACT；边界写进 docstring | ✅ 已修 |
| 3 | `src/finguard/rules/dsl.py` 解析器 | `hit('')` / 纯空白 label 可解析：label 是结论文字，空 label 会让切片 4 意见层输出空结论 | 中 | 解析期拒绝空/纯空白 label | ✅ 已修 |
| 4 | `src/finguard/rules/dsl.py` `_TOKEN_RE` | 数字字面量 `\d` 是 Unicode：全角 `１２３` 被静默归一成 123，与「严格性」主张及切片 1 rule_id 的 ASCII 闸不一致 | 低 | number 模式改 `[0-9]`（不影响 `\s` 对全角空白的既有处理） | ✅ 已修 |
| 5 | `src/finguard/rules/engine.py` `Evaluation` | 「可哈希」仅对可哈希事实值成立：事实值为 list/dict 时 `hash()` 抛 TypeError（探针证实）；verdict/coverage 映射（未判定也标 NOT_APPLICABLE）此前未文档化，下游易误读 | 低 | docstring 写明可哈希 caveat 与映射规则：**下游区分不适用/未判定必须读 coverage/reason_code** | ✅ 已修（文档） |
| 6 | `tests/test_dsl.py` / `test_engine.py` | `assertRaises(Exception)` 过宽（任何异常都算过）；inf/字符串字典序/转义还原等设计取舍无断言钉住 | 低 | 收紧为 AttributeError；补钉住断言 | ✅ 已修 |
| 7 | `src/finguard/rules/engine.py` 兜底分支 | 引擎内部异常标 `DSL_PARSE_ERROR` 误导审计（reason 文案可区分，原因码不可）。建议新增 `ENGINE_ERROR` 码，但 enums 约定「改动需同步 PRD」，评审方不改 PRD | 低（咨询） | 实现方与 PRD 责任人同步后加码 | ⏸ 留实现方决策 |
| 8 | `src/finguard/rules/dsl.py` `_unquote` | 未知转义静默透传：`'\n'` 保留为字面 backslash-n、`'\q'` 原样通过，与严格性主张不一致（仅支持 `\\` `\'` `\"` 未明示） | 低（咨询） | 解析期拒绝未知转义，或文档明示支持清单 | ⏸ 留实现方决策 |
| 9 | `src/finguard/rules/engine.py` | 重复字段名（`fact.a < fact.a`）缺失时 reason 输出「a、a」、fact_refs 重复 | 低（咨询） | 去重保序 | ⏸ 留实现方决策 |
| 10 | `scripts/push.ps1`（工具提交 `0c2509b`） | 默认推送列表含 `main`——ROADMAP 要求合并 main 前后跑完整回归，脚本本身无闸；机器特定 git 路径硬编码；令牌进环境变量可被同用户进程读取（已不落盘/不进命令行/finally 清理，无 force，注入面干净） | 低（咨询） | 默认分支去掉 main 或加确认；路径可配置；注释注明令牌暴露面 | 📁 留档（不阻断） |
| 11 | `tests/test_verify_entry.py` | `import verify` 触发其顶层 stdout/stderr 重配为 utf-8：GBK 控制台直跑 unittest 时输出编码被副作用改变 | 低（咨询） | 可接受；建议 verify.py 把 reconfigure 移入 `main()` | 📁 留档 |

#### 「评审方请确认」五项的答复

1. **DSL 语法是否够用 / 要不要提前 AND/OR**：够用。PRD FR-RAG-01 只要求单条可判定 if-then，PRD 第 1452 行示例与实现文法完全一致，7 条种子规则全部可表达。AND/OR/决策表按留白计划走后续切片，**不提前建**（避免过度设计）。
2. **严格性是否太难用**：可接受。错误在解析/入库期暴露（正确的位置），报错带字符位置；全角数字与空 label 修复后严格性自洽。建议切片 9 规则工作台提供实时解析预览，把维护成本降下来。
3. **context=None 是否改必填**：不改。范围判定唯一权威是 `RuleStore.bind()`，引擎复核是防御层，语义已有测试钉住。建议切片 4/5 调用方**一律传 context**（纵深防御）；必填化会破坏已绑定流程的批量判定便利性，收益不抵成本。
4. **「永不抛异常」是否掩盖问题**：曾有两处洞，已修（意见 2）——现在边界是「坏数据降级、调用方 bug 抛 TypeError」。残留：兜底把内部异常标 DSL_PARSE_ERROR（意见 7，留实现方加 ENGINE_ERROR）。
5. **五个产品不变量**：① 判定只在引擎 ✓（纯比较、无模型、结论文字来自规则 label）；②③④ 检索/事实层/闸门属切片 3/4/5，本切片不涉及；⑤ 留痕——Evaluation 不可变、可序列化，为切片 6 快照就绪。FR-AUDIT-03 ✓（severity 恒等于 rule.severity，接口无 severity 入参，有断言）；F-02 批量隔离 ✓。

#### 验证证据（本评审会话实际运行）

| 命令 | 结果 |
| --- | --- |
| `python scripts/verify.py --slice 2`（修复前复现） | 44 / 44，退出码 0 |
| 新增 11 条断言在旧代码上运行 | 8 条失败（7 failures + 1 error），钉住类与旧断言零回归 |
| `python scripts/verify.py --slice 2`（修复后） | **55 / 55**，退出码 0 |
| `python scripts/verify.py`（完整回归）/ `--checkpoint commit` | **104 / 104**，退出码 0 |
| 探针 E1—E14 复测 + 正例对照 + 7 条种子规则批量判定 | 全部闭环，种子 R-CR-013 命中正常 |

> 给实现方：合并后请把本文件切片 2「验证命令」预期从 **44 / 44** 同步为 **55 / 55**、全量 **93 → 104**；切片 2 段落缺「评审方入口 / 合并方式」子节（切片 1 有），建议补齐固定格式。PRD、ROADMAP 验收标准未动（ROADMAP 仅实现方勾选，文字未改）。
---

## 切片 3　事实层

| 项 | 内容 |
| --- | --- |
| **分支** | `feat/impl` |
| **切片目标** | 实现 PRD 8.2 的事实层：**双路抽取合并 → 交叉一致性核对 → 冲突入队 → 产出可判定事实集** |
| **本次不含** | 真实 OCR 与真实模型调用（留到切片 9 接入，理由见 PRD 7.6.7 选型轴①②③⑨）、审查意见生成（切片 4）、闸门（切片 5） |
| **验证命令** | `python scripts/verify.py --slice 3`（预期 **44 / 44 通过**）；全量 `python scripts/verify.py` 预期 **151 / 151 通过** |

### 改了什么

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `src/finguard/facts/models.py` | 新增 | `Confidence`（高 / 低）、`SourceChannel`、`FactField`、`FactSet`，以及**结论词闸门** |
| `src/finguard/facts/extract.py` | 新增 | `RouteResult` 与 `merge_dual()`：双路合并，一致→高置信，不一致→低置信 + 并列候选 |
| `src/finguard/facts/crosscheck.py` | 新增 | 两组交叉核对：流水合计 vs 申报营收（带偏离幅度）、授权额度 vs 实际审批人授权 |
| `src/finguard/facts/conflict.py` | 新增 | `ConflictItem` / `ConflictQueue`：并列候选值、人工裁决（理由必填）、回填为高置信 |
| `src/finguard/facts/layer.py` | 新增 | `FactLayer`：编排全流程，产出前强制扫结论词 |
| `tests/test_facts_extract.py` | 新增 | 11 项断言：双路合并各分支 |
| `tests/test_facts_crosscheck.py` | 新增 | 11 项断言：两组核对与阈值 |
| `tests/test_facts_conflict.py` | 新增 | 10 项断言：裁决契约与回填 |
| `tests/test_facts_layer.py` | 新增 | 12 项断言：编排、结论词闸门、跨切片集成 |
| `scripts/verify.py` | 修改 | 注册 `facts` 套件 |

### 影响到什么功能与效果

| 层面 | 影响 |
| --- | --- |
| **功能** | 业务事实第一次有了**可信度分级**：哪些字段可以直接拿去判、哪些必须先问人，一目了然 |
| **对外效果** | 两路抽取不一致时，系统**不猜**——字段被拦在判定链外并列入待裁决；冲突项**并列两个取值**交人工裁决，裁决必须写理由，事后可复盘 |
| **合规效果** | ① 事实层**不输出任何结论**（FR-FACT-07）：取值或字段名里出现「违规 / 合规 / 建议 / compliant」等结论词，**直接失败** ② 低置信**不进判定链**（FR-FACT-03）③ 裁决必须带理由、值必须来自候选列表 |
| **可度量** | 支撑 PRD 指标 **Q-06（字段抽取准确率）**、**Q-07（低置信拦截率）**、**Q-08（勾稽冲突识别率）** 的实现侧 |
| **不影响的** | 判定引擎（切片 2）、意见生成（切片 4）、闸门（切片 5）、回放（切片 6） |

### 关键设计点（建议重点看这 5 处）

1. **「拦截」是机制，不是提示** —— `FactSet.decision_ready()` 是判定链的**唯一入口**，低置信与未裁决冲突字段一律不在其中。集成测试证明：低置信字段被拦后，规则引擎判出 `MISSING_FACT / 未判定`，而不是拿一个可疑值硬判。
2. **结论词闸门是硬失败** —— 事实层产出前扫描字段名与取值的中英文结论词，命中即抛 `ConclusionTermError`。中文按子串匹配；英文按「前后不是字母或数字」的边界匹配，因为字段名是 snake_case（`is_compliant` 必须能命中）。
3. **布尔与数字不算一致** —— Python 里 `0 == False` 为真，但「抽到 0」和「抽到 False」是两回事。`merge_dual` 用更严的相等判定，避免这种隐性不一致被当成高置信放行。
4. **NaN 两路都给也判低置信** —— `nan == nan` 为 False，所以 NaN 不会因为「两路都给了」而被当成一致；它会进待裁决，由人处理。
5. **裁决契约** —— 理由为空 / 裁决人为空 / 裁决值不在候选列表内，三种情况全部拒绝；已裁决项不可重复裁决。裁决回填后字段置信度变高、来源标 `adjudicated`、备注里带上裁决人与理由。

### 有意留白

| 项 | 原因 | 计划 |
| --- | --- | --- |
| 抽取器是**桩件**（`RouteResult` 直接给值） | OCR 与模型调用是整条链路上最不确定的两块，先落地逻辑；PRD 7.6.7 选型轴①②③⑨ 就是为这种切换留的口子 | 切片 9 接入真实 OCR / MiniMax，契约不变 |
| 冲突队列不做**超时自动裁决** | PRD 8.2 写的是「超时未裁决则该规则条目置未判定」，由引擎侧处理，不在事实层偷偷兜底 | 切片 5 状态机接超时升级 |
| 只做两组交叉核对 | PRD 只要求流水 vs 营收、额度 vs 审批人授权 | 需要时按 `CrossChecker` 加一组即可 |
| 结论词表是**人工维护的清单** | 没有可用的中文金融结论词标准库 | 切片 9 规则工作台可让维护人员补充 |

### 评审方请确认

- [ ] 结论词表的中英文清单是否够用；误报（例如 `hit_rate`）是否可接受
- [ ] 「结论词命中直接失败」是否太硬——是否需要降级为「告警 + 标记」而非抛异常
- [ ] 交叉核对的偏离阈值默认 20%（自建口径）是否合理；是否需要按业务线分别配置
- [ ] 事实缺失时事实层**不报冲突**、交引擎按 `MISSING_FACT` 降级，这个分工是否清晰
- [ ] `decision_ready()` 作为判定链唯一入口，是否需要在切片 5 再加一道审计校验（谁调用了它、是否绕过）

### 评审方入口

| 项 | 值 |
| --- | --- |
| 评审工作目录 | `D:\桌面\AIagent_project\金融项目\.worktrees\review` |
| 评审分支 | `review/impl`（基于 `feat/impl` 的切片 3 交付点） |
| 先做什么 | `cd .worktrees/review && python scripts/verify.py --slice 3` 复现结果 |
| 只改什么 | `src/`、`tests/`、`scripts/`，以及本文件的「评审意见」段落 |
| 不改什么 | PRD、需求编号、验收标准、`docs/ROADMAP.md` |
| 提交后 | 停下，由实现方合并 `review/impl` → `feat/impl` 并复跑完整验证 |

### 合并方式

```bash
# 评审方在自己的 worktree 里：
git checkout review/impl
# ... 审阅与修改 ...
git commit -am "review: 切片 3 评审意见"
# 提交后停下，由实现方合并回 feat/impl
```
---

## 待评审队列

| 切片 | 状态 | 等待评审 |
| --- | --- | --- |
| 1 | ✅ 已完成并通过评审（已合并 `review/impl` → `feat/impl`，断言 27 → 49） | 否 |
| 2 | ✅ 已通过评审（合并 `review/impl` → `feat/impl`，评审后 55 → 落地咨询项后 58，全量 107） | 否 |
| 3 | ✅ 已实现，待评审（断言 44，全量 151） | **是** |
| 4—9 | ⬜ 未开始 | 否 |
