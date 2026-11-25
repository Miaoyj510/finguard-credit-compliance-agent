# FinGuard —— 信贷内控合规审查与贷后风险 Agent 平台

> 面向中小银行与城商行的「内控合规审查 + 贷后风险监测」双线 Agent 平台。
> 把合规审查、风险研判、贷后扫描、预警核查、结论签核五段串成一条**规则可溯、证据可回放**的流水线。
> **AI 只出带规则编号的结构化意见，结论由人签。**

本仓库是实现层。需求基线见 [PRD-信贷内控合规审查与贷后风险Agent平台.md](PRD-信贷内控合规审查与贷后风险Agent平台.md)（14 章）。

---

## 1. 三条不可让渡的设计承诺

| 承诺 | 含义 | 落地位置 |
| --- | --- | --- |
| 规则可溯 | 任一结论都带规则编号 + 版本 + 事实引用；缺项在生成阶段被拦截 | `finguard/rules/registry.py`、`domain/rule.py` |
| 证据可回放 | 一件业务件的六层内容可原样复现 | 切片 6 |
| 人在环不可绕过 | 三处人审闸门不可跳过、不可代签 | 切片 5 |

## 2. 目录结构

```
src/finguard/
├─ domain/          领域模型：规则、三键、案件状态、枚举
├─ rules/           L1 规则侧：版本注册表与三键取版本
├─ facts/           事实层（切片 3）：双路抽取、低置信拦截、交叉核对
├─ agents/          单 Agent + Harness（切片 4—5）
├─ api/             FastAPI 接口（切片 9）
└─ eval/            评测框架与 Gate 判定（切片 8）
tests/               每个断言对应 PRD 的一条可核要求
scripts/verify.py    ★ 唯一的验证入口
docs/ROADMAP.md      最小切片路线图 + 检查点制度
docs/HANDOFF.md     每次切片的评审交接说明
```

## 3. 环境要求

- Python **3.9+**（开发机为 3.9.12；容器内 3.11）。代码不依赖 3.10+ 语法。
- 验证脚本**只用标准库**（unittest），不需要 `pip install` 任何东西。
- 后续切片引入的依赖（FastAPI / httpx / pgvector 等）会在 `requirements.txt` 中逐项登记。

## 4. 跑验证（最重要的一节）

```bash
python scripts/verify.py                      # 跑全部已启用套件（累积回归）
python scripts/verify.py --list               # 切片 / 套件状态矩阵
python scripts/verify.py --slice 1            # 只跑第 1 切片的套件
python scripts/verify.py --suite rules        # 只跑指定套件
python scripts/verify.py --checkpoint commit  # 检查点：提交前
python scripts/verify.py --checkpoint daily   # 检查点：每日收工
python scripts/verify.py --checkpoint milestone # 检查点：里程碑（附 Gate 对照）
python scripts/verify.py -v                   # 打印失败详情
```

退出码：`0` 全部通过 / `1` 有失败 / `2` 参数或环境错误。

**约定**：每新增一项能力，必须同时新增对应断言。**没有断言的能力不会出现在验证结果里**——这是本项目唯一的进度真值来源。

## 5. 检查点制度

| 时机 | 命令 | 通过标准 |
| --- | --- | --- |
| 每改完一个函数 | `python scripts/verify.py --slice <当前切片>` | 全绿 |
| 每次提交前 | `python scripts/verify.py --checkpoint commit` | 全绿，且 `--list` 中无新增「待实现」被误标 |
| 每日收工 | `python scripts/verify.py --checkpoint daily` | 全绿；未完成的切片在 HANDOFF 中登记 |
| 里程碑（W2 / W4 / W6 / W8） | `python scripts/verify.py --checkpoint milestone` | 全绿 + Gate 对照表逐条有结论 |
| 合并前（双方完成） | 在主仓根目录跑 `python scripts/verify.py` | 全绿 |

## 6. 分支与 worktree 约定

| 分支 | 用途 | 工作目录 |
| --- | --- | --- |
| `main` | 稳定基线（PRD + 文档），合并目标 | 仓库根目录 |
| `feat/impl` | **实现工作分支**（本仓库作者） | `.worktrees/impl/` |
| `review/impl` | **评审分支**（代码评审方） | `.worktrees/review/` |

规则：

1. 两个角色**各自在自己的 worktree 里工作**，不碰对方的目录与分支。
2. 实现方每完成一个切片 → 提交到 `feat/impl` → 在 `docs/HANDOFF.md` 写交接说明 → **停下等评审**。
3. 评审方在 `review/impl` 上审阅与修改 → 提交 → 合并回 `feat/impl`。
4. 双方都完成后 → 合并到 `main` → 在主仓根目录**完整跑一遍测试**。

## 7. 当前进度

| 切片 | 主题 | 状态 |
| --- | --- | --- |
| 1 | 规则模型与三键版本绑定 | ✅ 已完成并通过评审（49 项断言全绿） |
| 2 | DSL 判定引擎 | ✅ 已完成（44 项断言全绿，合计 93 项） |
| 3 | 事实层 | ⬜ 待实现 |
| 4 | 审查意见与引用闸门 | ⬜ 待实现 |
| 5 | Harness 编排与三处人审闸门 | ⬜ 待实现 |
| 6 | 事件日志、六层卷宗与回放 | ⬜ 待实现 |
| 7 | 贷后循环与核查 | ⬜ 待实现 |
| 8 | 评测框架与 Gate 判定 | ⬜ 待实现 |
| 9 | 部署与生产就绪 | ⬜ 待实现 |

## 8. 术语与 PRD 对应

| 代码里的名字 | PRD 里的说法 |
| --- | --- |
| `TripleKey` | 三键：业务发生日 × 产品类型 × 机构层级 |
| `RuleStore.bind()` | 三键取版本（FR-RAG-04 / 05） |
| `UnresolvedRule` | 须人工判定（`NO_VERSION_MATCH` / `AMBIGUOUS_VERSION`） |
| `Verdict` | 判定三态：命中 / 不命中 / 不适用 |
| `Severity` | 严重度（只来自规则库，模型不得自拟） |
| `CoverageFlag` | 覆盖度：已判定 / 不适用 / 未判定 |

---

## 9. 评审方入口（另一个 agent 从这里开始）

| 项 | 值 |
| --- | --- |
| **评审工作目录** | `D:\桌面\AIagent_project\金融项目\.worktrees\review` |
| **评审分支** | `review/impl` |
| **被审分支** | `feat/impl`（切片 1 提交 `736ce8b`） |
| **只做代码审阅** | 不改 PRD、不改需求编号、不改 `docs/ROADMAP.md` 的验收标准 |
| **可改** | `src/`、`tests/`、`scripts/`、本文件（`docs/HANDOFF.md`）的「评审意见」段落 |
| **禁止** | 在 `feat/impl` 或 `main` 上直接提交；修改其他 agent 的 worktree 目录 |

评审方在**自己的 worktree** 里执行：

```bash
cd .worktrees/review
python scripts/verify.py --slice 1     # 先复现实现方的结果（预期 49/49）
# 审阅 → 如需修改 → git add -A && git commit -m "review: ..."
```

改完提交后**停下**，由实现方把 `review/impl` 合并回 `feat/impl`，再跑一次完整验证。

---

## 10. 已知环境事项

- 沙箱内的命令执行通道在 2026-11-25 出现过 `helper_unknown_error: setup refresh had errors`，**授权通道（require_escalated）正常**。若沙箱通道恢复，普通命令优先用普通通道。
- 本机 `git`：`C:\Users\DELL\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe`（也可用系统 git）。
- 本机 Python：3.9.12（系统，含 pytest 7.1.1）；另有 3.12.14（Codex 运行时）。**验证脚本只用标准库**，两版都能跑。