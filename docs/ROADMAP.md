# 最小切片路线图与检查点制度

本项目的实施单位是**切片**：每个切片必须小到可以独立验证、独立评审、独立回滚。
一个切片 = 一层能力 + 一组断言 + 一次评审。

**铁律**：切片没通过验证，不进入下一个切片；切片没经过评审，不进入下一个切片。

> **验收框（☑ / ☐）的语义**：☑ 表示「**该切片的实现验收标准已满足**」，
> **不代表已通过评审**。评审结论看 `docs/HANDOFF.md` 的「待评审队列」。
> 这样勾选动作不必等评审，避免"评审是闸"与"验收要记录"两件事互相阻塞。

---

## 1. 切片总表

| 切片 | 主题 | 交付物 | 验收标准（可勾选） | 对应 PRD | 验证套件 |
| --- | --- | --- | --- | --- | --- |
| **1** | 规则模型与三键版本绑定 | `domain/rule.py`、`domain/case.py`、`domain/enums.py`、`rules/registry.py`、`rules/seeds.py` | ☐ 规则缺必填字段即拒绝入库 ☐ 三键唯一命中取到唯一版本 ☐ 历史时点取到历史版本 ☐ 生效区间左闭右开 ☐ 产品/机构不适用 → 须人工判定 ☐ 版本区间重叠 → 须人工判定并列出候选 ☐ **绝不按最新版硬判** ☐ 绑定结果可复算 | 5.2 / 5.3、FR-RAG-03/04/05 | `rules` |
| **2** ✅ | DSL 判定引擎 | `rules/dsl.py`、`rules/engine.py` | ☑ if-then 表达式可解析 ☑ 返回命中/不命中/不适用三态 ☑ 事实缺失 → 未判定 ☑ 判定可复算（同输入同结果） ☑ 严重度取自规则库 | 5.4、FR-RAG-01、FR-AUDIT-03 | `dsl` |
| **3** ✅ | 事实层 | `facts/models.py`、`facts/extract.py`、`facts/crosscheck.py`、`facts/conflict.py`、`facts/layer.py` | ☑ 双路抽取一致 → 高置信 ☑ 不一致 → 低置信且**拦截在判定链外** ☑ 流水 vs 营收核对 ☑ 额度 vs 审批人核对 ☑ 冲突项并列两取值 ☑ 事实层输出无结论词 ☑ 裁决后重跑核对 ☑ 勾稽冲突裁决**不改写事实取值** | 8.2、FR-FACT-01~07 | `facts`（66 项） |
| **4** ✅ | 审查意见与引用闸门 | `domain/opinion.py`、`agents/auditor.py` | ☑ 意见必带引用三件套 ☑ 缺任一项被拦截 ☑ 覆盖度三类计数自洽 ☑ 未判定必带原因码 ☑ 禁止行为 FB-01~FB-05 可检出 ☑ 严重度不可自拟 ☑ 引用含字段路径+取值+来源通道 ☑ quotes 注入被拦 | 8.3、FR-AUDIT-01~08、NFR-01/03 | `review`（42 项） |
| **5** ✅ | Harness 编排与三处人审闸门 | `agents/harness/`、`domain/case_state.py` | ☑ 17 状态可达且无绕行边 ☑ Handoff 缺字段即中断 ☑ 三处闸门不可绕过、不可代签 ☑ 超时只升级不放行 ☑ 闸门零绕行 ☑ 放行须有完整签核证据 ☑ 阶段字段不可直接赋值 ☑ 判定链进入次数可测量 | 8.2 / 8.4 / 8.5 / 8.6 / 8.7、FR-GATE-01~06、SEC-01/03/04 | `harness`（96 项） |
| **6** ✅ | 事件日志、六层卷宗与回放 | `storage/event_log.py`、`storage/dossier.py`、`agents/replay.py` | ☑ 事件日志只追加、哈希链可校验 ☑ 六层齐全性两档校验 ☑ 同案同起点重放结论一致 ☑ 缺任一层判为不完整 ☑ 链断与卷宗被改均可检出 ☑ 不完整/对不上不可用于监管取证 | 8.9 / 8.10、FR-GATE-05/06、P-10、SEC-06 | `replay`（79 项） |
| **6.5** ✅ | 外部数据集固化与参数校准 | `data/external/`、`scripts/fetch_datasets.py`、`scripts/calibrate.py`、`knowledge/guard.py`、`knowledge/taxonomy.py` | ☑ 清单以 SHA256 钉死 ☑ 哈希不符即失败 ☑ 归档不执行、可执行成员拒收 ☑ 解压防目录穿越 ☑ 校准只出相对量 ☑ 外部语料过入厂闸门并与运行时隔离 ☑ 原始件不进 Git ☑ D-3 只取结构规则不引入 ECL | 9.7、12.3 评测集口径、13.2 数据范围 | `data`（31 项） |
| **7** ✅ | 贷后循环与核查 | `agents/post_monitor.py`、`agents/verifier.py`、`agents/post_loop.py`、`domain/post.py`、`rules/post_seeds.py` | ☑ 常驻循环与主流程解耦 ☑ 预警带触发规则版本与证据快照 ☑ 核查初核 ☑ GATE-C 处置四档 ☑ 提前量可测 ☑ 未签核不得处置 ☑ 超时只升级不放行 ☑ 采纳率/误报率可下钻到规则 | 8.4、6.4 FR-POST-01~07 | `post`（49 项） |
| **7.2** ✅ | RAG 文档侧：分库、双粒度切片、检索引用与权限模型 | `knowledge/corpus.py`、`knowledge/chunker.py`、`knowledge/retrieval.py`、`knowledge/analog.py`、`knowledge/permissions.py` | ☑ 规则库与文档库物理分库、判定路径不接触文档库 ☑ 条款级 + 要素级双粒度切片 ☑ 切片保留章条号 / 文号 / 生效日 / 判定口径 / 证据字段 ☑ 相邻条款重叠 1 句 ☑ 检索只返回引用、无结论字段 ☑ 历史意见只作参考类比且必带当时规则版本 ☑ 越权检索按角色过滤 | 6.1 FR-RAG-02/06/07/08、7.2、9.2、9.3、9.4、11.4 S-06 | `rag`（33 项） |
| **7.5** ✅ | 界面设计系统与业务界面原型 | `design-system/finguard/`、`web/` | ☑ 设计系统落盘且记录修订决策 ☑ 四个业务界面按 PRD 10.2 实现 ☑ 人机分层与未判定高亮 ☑ 闸门不可绕过在界面表达 ☑ 状态不只靠颜色 ☑ 无障碍与打印样式齐备 ☑ 离线可用无 CDN 依赖 ☑ 界面质量可断言 ☑ **登录页补齐认证入口**（实名工号 / 岗位 / 授权层级 / 可签核范围 / 设备指纹 / 演示环境明示） | 10.2 界面示意、10.1 流程界面落点、7.6.7⑩ 身份与鉴权、9.4 权限模型 | `ui`（63 项） |
| **8.0** ✅ | 金标标注：合成案件生成器、任务包分离与双盲比对 | `eval/synth.py`、`eval/gold.py`、`scripts/build_gold_pack.py`、`scripts/compare_gold.py`、`docs/GOLD_LABELING.md` | ☑ 技术性真值由生成器直接产出 ☑ 任务包与技术真值物理分离 ☑ 盲标批次为裁判未见过的新批次 ☑ 五个判断字段枚举封闭 ☑ 双盲机械比对出一致率与分歧清单 ☑ 仲裁规则成文 | 12.3 评测集口径、13.1 B 线金标标注、6.1 FR-RAG-04/05 | `gold`（17 项） |
| **8.1** | 评测框架与 Gate 判定 | `eval/`、`data/gold/` | ☐ 4 套评测集可加载 ☐ 36/38 项指标可复算 ☐ 7/8 个 Gate 可判定 ☐ 硬指标无折中 ☐ 未达标如实记录 | 11—12 章 | `eval` |
| **9** | 部署与生产就绪 | `deploy/`、`Dockerfile`、`docker-compose.yml` | ☐ 三环境隔离 ☐ 一键部署可重复 ☐ 灰度三档 + 回滚 ≤15 分钟 ☐ 50 并发压测报告 ☐ Runbook 交付 | 7.6 / 7.7、DP-12~DP-18 | `deploy` |

## 2. 检查点制度

| 时机 | 谁跑 | 命令 | 通过标准 |
| --- | --- | --- | --- |
| 改完一个函数 | 实现方 | `python scripts/verify.py --slice <当前切片>` | 全绿 |
| 每次提交前 | 实现方 | `python scripts/verify.py --checkpoint commit` | 全绿 |
| 每日收工 | 实现方 | `python scripts/verify.py --checkpoint daily` | 全绿；未完成项登记进 HANDOFF |
| 切片交付前 | 实现方 | `python scripts/verify.py --slice N` + 更新 `docs/HANDOFF.md` | 全绿 + 交接说明写清 |
| 评审开始时 | 评审方 | `python scripts/verify.py` | 复现实现方的结果 |
| 评审提交前 | 评审方 | `python scripts/verify.py` | 全绿 |
| 里程碑 | 双方 | `python scripts/verify.py --checkpoint milestone` | 全绿 + Gate 对照有结论 |
| 合并到 main 前 | 双方 | 在主仓根目录 `python scripts/verify.py` | 全绿（**完整回归，不是单切片**） |

## 3. worktree 与合并流程

```
主仓根目录（main）                    .worktrees/impl（feat/impl）      .worktrees/review（review/impl）
     │                                      │                                  │
     │  实现方在这里工作 → 提交 → 停下等评审                                    │
     ├──────────────────────────────────────┤                                  │
     │                                      │  评审方在这里看同一份代码 → 改 → 提交 │
     │                                      │◄─────────────────────────────────┤
     │                          合并 review/impl → feat/impl                   │
     │  双方完成后：合并 feat/impl → main，在主仓根目录完整跑一遍测试             │
```

规则：

1. **不碰对方的目录与分支**——任何一方都不得在对方的 worktree 里改文件或提交。
2. 实现方每完成一个切片就提交 + 写 `docs/HANDOFF.md` + **停下等评审**。
3. 评审方的修改走 `review/impl`，合并回 `feat/impl` 时由实现方执行，避免来回覆盖。
4. 合并到 `main` 前后各跑一次完整测试；合并后这次是**发布前最后一道闸**。

## 4. 版本与提交约定

- 提交信息：`[切片 N] 简述` + 空行 + `影响：PRD 章节与 FR/NFR 编号` + 空行 + `验证：python scripts/verify.py --slice N`
- 一个切片一次提交；修 bug 单独提交，不与新功能混在一起。
- 不提交：`.env`、真实客户数据、模型调用日志里的业务正文。
