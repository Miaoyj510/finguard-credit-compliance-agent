#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""外部数据参数校准（切片 6.5）。

**只校准相对量**（评审意见第 3 点）：外部数据是美元 / 印尼盾 / 迪拉姆口径，
金额的绝对分布搬进人民币口径必错。因此本脚本产出的每一个指标都是
**无量纲统计量**——占比、分位数、比率、变异系数。
货币与规模的换算关系写成 PRD 附录假设 [D]，**不进代码**。

产出 ``data/external/calibration.json``，每个数字都带出处
（数据集 / 文件 / 字段 / 样本量 / 计算式），评审方可独立复算。
**该文件可由本脚本再生**，切片 6 回放时它是「当时参数」的证据。

用法
----
    python -I scripts/calibrate.py            # 读 data/external/_raw/ 产出校准文件
    python -I scripts/calibrate.py --out <路径>
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from finguard.knowledge.taxonomy import (  # noqa: E402
    TERMINAL_BUCKET_LABELS,
    RELATIVE_ONLY_NOTE,
    bucket_tenor,
    map_sector,
)

RAW_DIR = os.path.join(ROOT, "data", "external", "_raw")
DEFAULT_OUT = os.path.join(ROOT, "data", "external", "calibration.json")

#: 允许出现在校准结果里的单位——**全是相对量**
ALLOWED_UNITS = ("ratio", "quantile", "count", "distribution", "rule")


def _metric(name, value, unit, source_file, fields, n, formula, note="") -> Dict[str, Any]:
    """构造一个带出处的指标。"""
    if unit not in ALLOWED_UNITS:
        raise ValueError("校准指标只允许相对量单位，得到 %r" % unit)
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "source_file": source_file,
        "fields": list(fields),
        "n": n,
        "formula": formula,
        "note": note,
    }


def _share(counter: Counter, total: int) -> Dict[str, float]:
    if not total:
        return {}
    return {k: round(v / total, 6) for k, v in sorted(counter.items())}


def _quantiles(values: Sequence[float], points=(0.1, 0.25, 0.5, 0.75, 0.9)) -> Dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    out: Dict[str, float] = {}
    for p in points:
        idx = int(round(p * (len(ordered) - 1)))
        out["p%02d" % int(p * 100)] = ordered[idx]
    return out


def _cv(values: Sequence[float]) -> Optional[float]:
    """变异系数（无量纲）。"""
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    if mean == 0:
        return None
    return round(statistics.pstdev(values) / abs(mean), 6)


# ---------------------------------------------------------------- D-1
def calibrate_credit50k(root: str) -> List[Dict[str, Any]]:
    """企业贷款组合：期限结构、行业占比、账龄与评级迁移。

    **不做**绝对金额统计（EAD / EL 等一律不取）——那是被评审方明确否掉的。
    """
    out: List[Dict[str, Any]] = []
    portfolio = os.path.join(root, "loan_portfolio.csv")
    if not os.path.isfile(portfolio):
        return out

    tenors: List[int] = []
    buckets: Counter = Counter()
    sectors: Counter = Counter()
    ratings: Counter = Counter()
    collateral: Counter = Counter()
    total = 0
    defaulted = 0
    with io.open(portfolio, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            total += 1
            try:
                months = int(row["maturity_months"])
            except (TypeError, ValueError):
                continue
            tenors.append(months)
            buckets[bucket_tenor(months)] += 1
            sectors[row["sector"]] += 1
            ratings[row["initial_rating"]] += 1
            collateral[row["collateral"]] += 1
            if row.get("defaulted") == "1":
                defaulted += 1

    out.append(_metric(
        "tenor_quantile_months", _quantiles(tenors), "quantile",
        "loan_portfolio.csv", ["maturity_months"], len(tenors),
        "对 maturity_months 取分位数（相对分位，不含金额口径）",
    ))
    out.append(_metric(
        "tenor_bucket_share", _share(buckets, total), "distribution",
        "loan_portfolio.csv", ["maturity_months"], total,
        "期限档位占比：档位见 knowledge/taxonomy.TERMINAL_BUCKETS",
        "档位边界为相对区间，与金额口径无关",
    ))
    out.append(_metric(
        "sector_share_raw", _share(sectors, total), "distribution",
        "loan_portfolio.csv", ["sector"], total,
        "外部行业原始占比（未映射）",
    ))
    mapped: Counter = Counter()
    ambiguous: Dict[str, List[str]] = {}
    for external, count in sectors.items():
        m = map_sector(external)
        mapped[m.target_code] += count
        if m.ambiguous:
            ambiguous[external] = [m.target_code] + list(m.alternatives)
    out.append(_metric(
        "sector_share_gbt4754", _share(mapped, total), "distribution",
        "loan_portfolio.csv", ["sector"], total,
        "行业占比（映射到 GB/T 4754-2017 门类）",
        "映射为一对多的外部行业已标记歧义，见 ambiguous_sector_mapping",
    ))
    out.append(_metric(
        "ambiguous_sector_mapping", ambiguous, "rule",
        "loan_portfolio.csv", ["sector"], len(sectors),
        "列出映射存在歧义的外部行业及其候选门类",
        "该映射是工程假设，属 PRD 附录 [D] 待验证项",
    ))
    out.append(_metric(
        "initial_rating_share", _share(ratings, total), "distribution",
        "loan_portfolio.csv", ["initial_rating"], total,
        "初始评级占比（相对量）",
    ))
    out.append(_metric(
        "collateral_share", _share(collateral, total), "distribution",
        "loan_portfolio.csv", ["collateral"], total,
        "担保方式占比（相对量）",
    ))
    out.append(_metric(
        "default_rate", round(defaulted / total, 6) if total else None, "ratio",
        "loan_portfolio.csv", ["defaulted"], total,
        "违约笔数 / 总笔数",
        "仅作结构参照，不作为本项目任何指标的公开对标值",
    ))
    return out


def calibrate_payment_history(root: str) -> List[Dict[str, Any]]:
    """逐月还款账龄：逾期天数分布、账龄迁移比例、余额波动率。

    96 MB 级文件，**流式**读，不整体载入内存。
    """
    out: List[Dict[str, Any]] = []
    path = os.path.join(root, "payment_history.csv")
    if not os.path.isfile(path):
        return out
    dpd_buckets: Counter = Counter()
    mob_dpd: Dict[int, Counter] = defaultdict(Counter)
    balances: List[float] = []
    n = 0
    with io.open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            n += 1
            bucket = (row.get("dpd_bucket") or "").strip() or "unknown"
            dpd_buckets[bucket] += 1
            try:
                mob = int(row.get("month_on_book") or 0)
            except (TypeError, ValueError):
                mob = 0
            mob_dpd[mob][bucket] += 1
            try:
                balances.append(float(row.get("outstanding_balance") or 0.0))
            except (TypeError, ValueError):
                pass
    out.append(_metric(
        "dpd_bucket_share", _share(dpd_buckets, n), "distribution",
        "payment_history.csv", ["dpd_bucket"], n,
        "逾期账龄档位占比",
        "账龄档位为相对分档；不含任何金额",
    ))
    out.append(_metric(
        "dpd_share_by_mob",
        {
            str(mob): _share(counter, sum(counter.values()))
            for mob, counter in sorted(mob_dpd.items())
        },
        "distribution",
        "payment_history.csv", ["month_on_book", "dpd_bucket"], n,
        "按账龄（放款后月数）分组的逾期档位占比——贷后迁移结构",
    ))
    out.append(_metric(
        "balance_cv", _cv(balances), "ratio",
        "payment_history.csv", ["outstanding_balance"], len(balances),
        "余额序列的变异系数（无量纲）",
        "只取离散度，不取余额绝对水平",
    ))
    return out


# ---------------------------------------------------------------- D-2
def calibrate_bank_statements(root: str) -> List[Dict[str, Any]]:
    """对账单流水：月度汇总的环比变化率分布、交易笔数分布。

    **只算相对量**：月度汇总金额的**变化率**与变异系数，不输出金额本身。
    """
    out: List[Dict[str, Any]] = []
    corpus = os.path.join(root, "datasets", "statement-transactions", "sample", "corpus.csv")
    if not os.path.isfile(corpus):
        return out
    monthly: Dict[str, float] = defaultdict(float)
    counts: Counter = Counter()
    n = 0
    with io.open(corpus, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            n += 1
            month = (row.get("Date") or "")[:7]
            if not month:
                continue
            counts[month] += 1
            try:
                amount = float(row.get("amount") or 0.0)
            except (TypeError, ValueError):
                continue
            monthly[month] += amount
    months = sorted(monthly)
    mom: List[float] = []
    for prev, cur in zip(months, months[1:]):
        if monthly[prev] == 0:
            continue
        mom.append((monthly[cur] - monthly[prev]) / abs(monthly[prev]))
    out.append(_metric(
        "monthly_amount_mom_change", _quantiles(mom) if mom else {}, "quantile",
        "statement-transactions/sample/corpus.csv", ["Date", "amount"], len(mom),
        "月度汇总金额的环比变化率分位数（相对量）",
        "不输出金额绝对值：印尼盾口径与人民币不可直接搬",
    ))
    out.append(_metric(
        "monthly_amount_mom_cv", _cv(mom), "ratio",
        "statement-transactions/sample/corpus.csv", ["Date", "amount"], len(mom),
        "月度环比变化率的变异系数（无量纲）",
    ))
    out.append(_metric(
        "txn_count_per_month_share", _share(counts, sum(counts.values())), "distribution",
        "statement-transactions/sample/corpus.csv", ["Date"], n,
        "每月交易笔数占比（相对量）",
    ))
    return out


# ---------------------------------------------------------------- D-3
def calibrate_ifrs9(root: str) -> List[Dict[str, Any]]:
    """贷后账簿：**只取 staging 结构规则**，不做 ECL / PD 会计口径计算。

    取三样结构：30/90 天背板阈值、评级下调档数阈值、账龄档位分布。
    这是**合规口径**的预警触发结构，不是会计口径的预期信用损失。
    """
    out: List[Dict[str, Any]] = []
    snapshot = os.path.join(root, "FactLoanSnapshot.csv")
    if not os.path.isfile(snapshot):
        return out
    buckets: Counter = Counter()
    dpd_days: Counter = Counter()
    n = 0
    with io.open(snapshot, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            n += 1
            try:
                dpd = int(float(row.get("DaysPastDue") or 0))
            except (TypeError, ValueError):
                dpd = 0
            if dpd == 0:
                bucket = "0"
            elif dpd <= 30:
                bucket = "1-30"
            elif dpd <= 90:
                bucket = "31-90"
            else:
                bucket = "90+"
            buckets[bucket] += 1
            dpd_days[min(dpd, 90)] += 1
    out.append(_metric(
        "dpd_backstop_share", _share(buckets, n), "distribution",
        "FactLoanSnapshot.csv", ["DaysPastDue"], n,
        "逾期背板档位占比（0 / 1-30 / 31-90 / 90+）",
    ))
    out.append(_metric(
        "staging_backstop_rules",
        {
            "stage2_trigger_dpd_days": 30,
            "stage3_trigger_dpd_days": 90,
            "stage2_trigger_rating_notches_worse": 3,
            "source": "findi360 数据卡与 data_dictionary.csv 记载的分期规则",
        },
        "rule",
        "FactLoanSnapshot.csv + DimLoan.csv", ["DaysPastDue", "CurrentRating"], n,
        "staging 结构的**触发阈值**（结构规则，非金额计算）",
        "本项目是合规口径预警，**不**计算 ECL——会计口径不在 PRD 范围内",
    ))
    return out


CALIBRATORS = (
    ("credit-50k", calibrate_credit50k),
    ("credit-50k", calibrate_payment_history),
    ("bank-statements", calibrate_bank_statements),
    ("ifrs9-loan-book", calibrate_ifrs9),
)


def build_calibration(raw_dir: str = RAW_DIR) -> Dict[str, Any]:
    datasets: Dict[str, List[Dict[str, Any]]] = {}
    for ds_id, func in CALIBRATORS:
        root = os.path.join(raw_dir, ds_id)
        if not os.path.isdir(root):
            continue
        metrics = func(root)
        if metrics:
            datasets.setdefault(ds_id, []).extend(metrics)
    return {
        "generated_by": "scripts/calibrate.py",
        "regenerate_command": "python -I scripts/calibrate.py",
        "policy": RELATIVE_ONLY_NOTE,
        "units_allowed": list(ALLOWED_UNITS),
        "tenor_buckets": list(TERMINAL_BUCKET_LABELS),
        "datasets": [
            {"id": ds_id, "metrics": metrics}
            for ds_id, metrics in sorted(datasets.items())
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="外部数据参数校准（只产相对量）")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--raw-dir", default=RAW_DIR)
    args = ap.parse_args()

    data = build_calibration(args.raw_dir)
    if not data["datasets"]:
        print("未找到原始件：%s" % args.raw_dir)
        print("请先运行：python -I scripts/fetch_datasets.py")
        return 2
    with io.open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
    total = sum(len(d["metrics"]) for d in data["datasets"])
    print("已写出 %s（%d 个数据集 / %d 个指标）" % (args.out, len(data["datasets"]), total))
    return 0


if __name__ == "__main__":
    sys.exit(main())