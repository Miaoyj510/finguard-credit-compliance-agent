# 金标仲裁日志

> 规范见 `ANNOTATION_GUIDE.md` 第 6 节。编号连续，只增不删；每条含分歧内容、类别（G/S/D）、处置与依据。

## 批次 1（EVAL-REV 前 30 件，标注人 B = agent-b）

### B1-001 — 首批自检：12 件金标笔误（G 类，已修）

交叉校验（`tests/test_gold_cases.py`）首跑报 12 件分歧，逐件仲裁**全部为金标笔误、管线零缺陷**：

| 子类 | 件 | 笔误内容 | 根因 |
| --- | --- | --- | --- |
| 产品范围误绑 | GOLD-REV-003/004/005/013 | 把「大额授信」专属 R-CR-030 绑进了对公流贷案件 | 标注时按"对公信贷桶"直觉带入了大额规则 |
| 产品范围误绑 | GOLD-REV-006/007/009/010/015 | 同上（对公流贷案件给了 R-CR-030 绑定与判定） | 同上 |
| 版本边界误判 | GOLD-REV-014 | 2025-05-20 标成 v3.2.0（应 v3.0.0，3.2.0 自 2025-07-01 起） | 左闭右开边界日心算错 |
| 版本边界误判 | GOLD-REV-028 | 2025-12-31 标成 R-CR-041 重叠须人工判定（应唯一命中 v1.1.0：v1.0.0 右开、当日已失效） | 同切片 1 验证问题 2/3 的同款陷阱 |
| 缺事实声明 | GOLD-REV-008 | missing_facts 漏列 monthly_revenue | 事实表与缺失声明不同步 |

处置：改金标（生成器修正后重出 JSON），gold_version 保持 `0.1.0-draft`（未冻结批次内修正不升版）；**管线未发现缺陷**。
教训入规范书第 3 条：边界日与产品范围是本项目标注两大高频陷阱，标注后必须跑交叉校验。

### A-01 — 覆盖度定义分歧（D 类，待仲裁）

| 项 | 内容 |
| --- | --- |
| 分歧 | PRD 8.4 样例与 10.2.1 线框把**绑定级**须人工判定计入 coverage.unresolved（「未判定 2（NO_VERSION_MATCH×2）」）；实现侧 `Auditor` 的 coverage 只计 evaluation（绑定级不在内），且 `bound_rule_total` 交叉校验按「total = 绑定数」自洽 |
| 影响 | 金标 coverage_prd 与管线 coverage 数值不同；切片 8 跑 Q-10（覆盖度字段完整性）与 GATE-1 时按哪个口径计分会直接改变结论 |
| 现状 | 金标按 PRD 口径冻结；实现不动 |
| 建议 | 仲裁人二选一：① 实现侧 `Auditor.build` 增 `unresolved_bindings` 入参，coverage 并入绑定级（PRD 不动）；② PRD 8.4/10.2.1 修订为「coverage 只计已绑定规则，绑定级须人工判定单列 unresolved_entries」（实现不动）。评审方倾向 ①（线框是给用户看的，用户视角的「未判定」天然含须人工判定） |
| 状态 | ✅ **已裁决（2026-11-28，仲裁人=用户）：选项 ①，改实现对齐 PRD**。金标口径不变（本就按 PRD）。实现规格见下，由实现方在下一「决策落地」提交执行（实现方正处切片 8，同文件并行改动由合并方负责对齐） |

**A-01 实现规格（裁决附件，供实现方执行）**：

1. `Auditor.build(...)` 增关键字入参 `unresolved_bindings: Sequence = ()`（元素取 `case.unresolved` 的 UnresolvedRule 或同构映射：rule_id / reason_code / detail）；
2. coverage 计算：`unresolved += len(unresolved_bindings)`、`total += len(unresolved_bindings)`；并为每条绑定级未判定生成 `UnresolvedEntry`（reason_code 用绑定级码 NO_VERSION_MATCH / AMBIGUOUS_VERSION），与判定级未判定条目并列进 `unresolved_entries`；
3. `CoverageSummary` 增字段 `binding_unresolved: int = 0` 并入 to_dict；`is_consistent` 在 `bound_rule_total is not None` 时的交叉校验改为 `bound_rule_total + binding_unresolved == total`（原「bound_rule_total == total」随之失效，切片 4 相关断言同步改写并钉住新语义）；
4. 调用方传参：切片 8 评测加载器、卷宗 L4 组装、`build_handoff` 的 coverage_summary 来源处一律传 `case.unresolved`；未传时默认 `()` 保持旧行为（向后兼容）；
5. 断言：补 PRD 10.2.1 线框场景——2 条 NO_VERSION_MATCH 的案件 coverage 为「未判定 2、原因码可列」，且 `judged + not_applicable + unresolved == total` 与 bound_rule_total 交叉校验同时成立。

### B1-002 — 双人标注分歧

**零分歧。** 标注人 A（实现方 agent-a）按规范书 v1 第 0 节「**或提供系统输出作对照**」，
提供管线对这 30 件的判定输出，与标注人 B 的手工金标逐件逐字段对照：

| 项 | 值 |
| --- | --- |
| 对照件数 | 30 |
| 一致件数 | **30** |
| 分歧项数 | **0** |
| 对照脚本 | `scripts/gold_crosscheck.py` |
| 对照报告 | `data/gold/crosscheck_batch1.json` |
| 交叉校验断言 | `tests/test_gold_crosscheck.py`（与 B 侧的 `tests/test_gold_cases.py` 相互独立） |

**对照过程发现一处口径分歧（已修正，判定为对照脚本缺陷而非金标缺陷）**：
对照脚本首版把「范围排除」（产品/机构不适用）另立一档「不适用」，与 B 的金标不一致
（30 件全部报分歧）。经查，**金标是对的**：切片 1 的 ROADMAP 验收标准明写
「产品/机构不适用 → 须人工判定」，切片 1 评审意见 9 亦确认 `RuleStore.bind()`
把「范围排除」与「时间版本缺口」同归 NO_VERSION_MATCH 是**刻意**的（语义按验收标准，
只提示 detail 文案未区分二者）。对照脚本按验收标准修正后即与金标完全一致。

结论三条：

1. **规则口径是清晰的**——独立的两套实现（手工推演 / 管线）从同一份规则文本推出同一结果，
   这正是「规则可判定」在金标层面的证据；
2. **管线无缺陷**——B1-001 首跑发现的 12 件分歧全部在**金标侧**（笔误），修正后零分歧；
3. **A-01 裁决与金标一致**——覆盖度按 PRD 口径（绑定级须人工判定计入未判定），
   与 B 的 `coverage_prd` 逐件吻合。

### B1-003 — 协议缺陷 P-1：拆分标注与双标要求冲突（待修订）

GOLD_LABELING §2「A 组奇数 / B 组偶数」与 §5 仲裁、compare_gold 交集语义、PRD 12.3 双标要求冲突；拆分口径下案件级一致率恒为空集。处置：改为双方全量标注（奇偶仅作起草顺序）。责任：实现方修订协议文档与排期说明；修订前 B 侧不开标。

### B1-004 — 协议缺陷 P-2：真值入库使盲性成为荣誉制度（缓解已定）

`pack-*/truth.jsonl` 在共享仓库内，盲性无法技术强制。缓解：B 侧开标前在自己工作树删除 truth 文件并声明；比对/仲裁在仲裁人侧跑。长期：truth 移 gitignore + 仲裁人持有（切片 8 评审时定）。

### B1-005 — 污染披露 P-3：FG-B-0001…0005 对 B 侧失盲

评审方协议审计时读过 A 侧试点 5 条标注（含奇偶）。
**✅ 已裁决（2026-11-28，仲裁人=用户）：选项 ①——换新前缀 FG-C 重生成试点 5 件。**
执行规格（实现方；生成器与任务包归 A 线）：

1. `build_gold_pack.py` 以**新 seed + 前缀 FG-C** 生成 5 件，替换 pack-b 的 1—5 号位
   （case_ids 改 FG-C-0001…0005，materials / truth / manifest 同步重出）；
2. `data/gold/labels/impl-agent.jsonl` 现有 5 行（FG-B-0001…0005）**作废保留**：
   加 `"void": true` 与作废原因（试点 + B 侧失盲），不参与一致率计算，仅作协议历史；
3. A 侧对 FG-C 5 件重跑试点标注（生成后**不得再打开 truth.jsonl**，P-2 荣誉制度同前）；
4. manifest 的 blind 说明补记：FG-B 1—5 因 B 侧失盲被 FG-C 替换，替换记录见本条；
5. 完成后 B 侧开标条件：P-1 修订（B1-003）+ 本条落地 + P-2 缓解（B 侧工作树删 truth）。

