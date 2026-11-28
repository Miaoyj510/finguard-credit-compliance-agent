# 字段映射表与校准口径（切片 6.5）

本文件是外部数据集与本项目字段之间的**唯一对照表**。
「口径」列是本表最重要的一列——它说明这个字段**搬的是结构还是数值**。

---

## 0. 口径声明（读表前先读这段）

| 口径 | 含义 | 允许的字段类型 |
| --- | --- | --- |
| **结构参照** | 只借鉴字段的**存在与语义**，数值由我们自己的合成数据生成器决定 | 枚举字段、分类字段 |
| **比例校准** | 搬**无量纲统计量**（占比 / 分位数 / 比率 / 变异系数） | 计数、比率、分位数 |
| **数值禁用** | **绝对数值一律不搬** | 金额、余额、指数值 |

**为什么金额这一列禁用**：外部数据是美元（`credit-50k`）、印尼盾（`bank-statements`）、
迪拉姆（`ifrs9-loan-book`）口径，其金额的**绝对分布**搬进人民币口径必然失真。
货币与规模的换算关系写成 **PRD 附录假设 [D]**，不进代码、不进本表。

**外部行业分类**统一映射到《国民经济行业分类》**GB/T 4754-2017** 门类，
映射存在一对多的，标 `歧义` 并列出候选门类——那是工程假设，属附录 [D] 待验证项。

---

## 1. D-1 `credit-50k`（企业贷款组合，MIT）

### 1.1 事实层字段

| 外部字段 | 本项目字段 | 类型 | 口径 | 说明 |
| --- | --- | --- | --- | --- |
| `maturity_months` | `tenor_months` | 整数 | 比例校准 | 期限。搬档位占比与分位数，不搬绝对分布 |
| `sector` | `industry` | 枚举 | 结构参照 | 映射到 GB/T 4754 门类（见 §4） |
| `loan_type` | `loan_type` | 枚举 | 结构参照 | 循环贷 / 按揭 / 租赁等产品形态分类 |
| `collateral` | `collateral_type` | 枚举 | 结构参照 | 担保方式占比 |
| `initial_rating` | `internal_rating` | 枚举 | 结构参照 | 初始评级档位分布 |
| `leverage` | `debt_to_ebitda_ratio` | 比率 | 比例校准 | 杠杆率 |
| `interest_coverage` | `interest_coverage_ratio` | 比率 | 比例校准 | 利息保障倍数 |
| `debt_to_equity` | `debt_to_equity_ratio` | 比率 | 比例校准 | 产权比率 |
| `ead` | `loan_amount` | 金额 | **数值禁用** | 只借字段名与「有本金敞口」这个语义 |
| `coupon_rate` | `pricing_rate` | 比率 | 比例校准 | 定价利率档位 |
| `credit_score` | — | — | **不映射** | 个人征信口径，与内控合规审查无业务关系；引入即引入个人信息处理负担 |
| `defaulted` / `default_date` | — | — | **不映射** | 违约是**信贷结果**，不是合规判定。本项目判定的是规则命中，不是违约 |
| `pd_annual` / `lgd` / `el` / `rwa` | — | — | **不映射** | 会计与监管资本口径，PRD 未要求 |

### 1.2 贷后账龄（`payment_history.csv`，逐月）

| 外部字段 | 本项目字段 | 口径 | 说明 |
| --- | --- | --- | --- |
| `month_on_book` | `months_on_book` | 比例校准 | 放款后月数，贷后账龄轴 |
| `dpd` | `days_past_due` | 结构参照 | 逾期天数 |
| `dpd_bucket` | `dpd_bucket` | 比例校准 | 账龄档位占比（**切片 7 预警分档的参照**） |
| `missed_payment` | `missed_payment_flag` | 结构参照 | 当期是否漏还 |
| `cumulative_missed` | `cumulative_missed_count` | 比例校准 | 累计漏还次数 |
| `outstanding_balance` | `outstanding_balance` | **数值禁用** | 只取序列的变异系数，不取余额水平 |
| `utilization` | `utilization_ratio` | 比例校准 | 额度使用率 |

---

## 2. D-2 `bank-statements`（对账单与流水，MIT）

| 外部字段 | 本项目字段 | 口径 | 说明 |
| --- | --- | --- | --- |
| `Date` | `biz_date` | 结构参照 | 交易日期 |
| `amount` | `transaction_amount` | **数值禁用** | 金额本身不搬；只搬月度汇总的**环比变化率**与变异系数 |
| `balance` | `account_balance` | **数值禁用** | 同上 |
| `desc` | `transaction_desc` | 结构参照 | **要素抽取的语料对象**（PRD FR-RAG-07 要素级切片） |
| `beneficiary_name` | `counterparty_name` | 结构参照 | 交易对手方 |
| `transaction_type` | `transaction_type` | 结构参照 | 借贷方向 |
| `role` | `counterparty_role` | 结构参照 | 对手方角色 |
| `sid` | `statement_id` | 结构参照 | 对账单编号（多期流水的主键） |
| `inj` / `scenario` / `kind` | — | **外部对照专用** | **只作外部对照集**：用来验证我们的金标标注法有没有漏掉某一类模式。**不充当判分金标**（PRD 12.3 的判分金标必须标在自家合成语料上） |

---

## 3. D-3 `ifrs9-loan-book`（贷后账簿，CC BY 4.0 · 署名 findi360）

**只做结构参照**：IFRS-9 的 PD / ECL 是**会计口径**，本项目贷后预警是**合规口径**。

| 外部字段 | 本项目字段 | 口径 | 说明 |
| --- | --- | --- | --- |
| `DaysPastDue` | `days_past_due` | 结构参照 | 逾期天数 |
| `CurrentRating` | `current_rating` | 结构参照 | 期末评级 |
| `Segment`（SME / Corporate） | `customer_segment` | 结构参照 | 客户类型，**正好对上**本项目的「小额经营贷 / 对公信贷」二分 |
| `Sector` | `industry` | 结构参照 | 行业 |
| `OutstandingBalance` | `outstanding_balance` | **数值禁用** | 只借字段名 |
| `PD12mPct` / `PDLifetimePct` | — | **不映射** | 会计口径违约概率，本项目不做预测建模 |
| — | `staging_backstop_rules` | **结构参照** | **只取触发阈值**：Stage 2 = 逾期 > 30 天或评级下调 ≥ 3 档；Stage 3 = 逾期 > 90 天。**ECL 计算不进流程** |

---

## 4. 行业映射（GB/T 4754-2017）

| 外部行业（D-1） | GB/T 4754 门类 | 歧义 | 候选门类 |
| --- | --- | --- | --- |
| Financials | J 金融业 | 否 | — |
| Real_Estate | K 房地产业 | 否 | — |
| Industrials | C 制造业 | 否 | — |
| Healthcare | Q 卫生和社会工作 | 否 | — |
| Consumer | F 批发和零售业 | **是** | O 居民服务 |
| Retail | F 批发和零售业 | **是** | O 居民服务 |
| Technology | I 信息传输、软件和信息技术服务业 | **是** | M 科学研究和技术服务 |
| Telecom | I 信息传输、软件和信息技术服务业 | **是** | G 交通运输、仓储和邮政 |
| Energy | D 电力、热力、燃气及水生产和供应业 | **是** | B 采矿业 |
| Utilities | D 电力、热力、燃气及水生产和供应业 | **是** | B 采矿业 |

> 六个外部行业落进同一门类对，映射是一对多的——这是**工程假设**，登记进 PRD 附录 [D] 待验证项。
> 未登记的外部行业**不猜**：`map_sector()` 直接抛 `KeyError`，要求先补登记。

---

## 5. 校准指标清单

`data/external/calibration.json` 里的每个指标都带出处（数据集 / 文件 / 字段 / 样本量 / 计算式）。
当前 **3 个数据集 / 16 个指标**，全部为无量纲量：

| 数据集 | 指标 | 单位 |
| --- | --- | --- |
| `credit-50k` | `tenor_quantile_months` / `tenor_bucket_share` | quantile / distribution |
| `credit-50k` | `sector_share_raw` / `sector_share_gbt4754` / `ambiguous_sector_mapping` | distribution / distribution / rule |
| `credit-50k` | `initial_rating_share` / `collateral_share` / `default_rate` | distribution / distribution / ratio |
| `credit-50k` | `dpd_bucket_share` / `dpd_share_by_mob` / `balance_cv` | distribution / distribution / ratio |
| `bank-statements` | `monthly_amount_mom_change` / `monthly_amount_mom_cv` / `txn_count_per_month_share` | quantile / ratio / distribution |
| `ifrs9-loan-book` | `dpd_backstop_share` / `staging_backstop_rules` | distribution / rule |

**该文件由 `scripts/calibrate.py` 可再生**：`python -I scripts/calibrate.py`。
它是切片 6 回放时「当时参数」的证据，不能是来历不明的数字。