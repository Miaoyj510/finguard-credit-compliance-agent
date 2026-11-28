"""行业分类映射与「相对量」口径（切片 6.5）。

两条纪律：

1. **行业映射到 GB/T 4754**：外部数据集的行业分类是它自己国家的口径
   （D-1 是 10 个美欧行业类），直接当产品线用会失真。本模块把外部行业
   映射到《国民经济行业分类》GB/T 4754-2017 的门类，**并显式标记歧义**
   （多个外部行业落到同一门类时，列出备选）。

2. **只校准相对量，不搬绝对量**：外部数据是美元 / 印尼盾 / 迪拉姆口径，
   金额的**绝对分布**搬进人民币口径必错。因此本模块定义的可校准对象
   全部是**无量纲统计量**：占比、分位数、比率、变异系数。
   货币与规模的换算写成附录假设 [D]，不进代码。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

__all__ = [
    "GBT4754",
    "SectorMapping",
    "SECTOR_MAP",
    "map_sector",
    "TERMINAL_BUCKETS",
    "TERMINAL_BUCKET_LABELS",
    "bucket_tenor",
    "RELATIVE_ONLY_NOTE",
]


#: 《国民经济行业分类》GB/T 4754-2017 门类（只列本项目会用到的）
GBT4754: Dict[str, str] = {
    "A": "农、林、牧、渔业",
    "B": "采矿业",
    "C": "制造业",
    "D": "电力、热力、燃气及水生产和供应业",
    "E": "建筑业",
    "F": "批发和零售业",
    "G": "交通运输、仓储和邮政业",
    "H": "住宿和餐饮业",
    "I": "信息传输、软件和信息技术服务业",
    "J": "金融业",
    "K": "房地产业",
    "L": "租赁和商务服务业",
    "M": "科学研究和技术服务业",
    "N": "水利、环境和公共设施管理业",
    "O": "居民服务、修理和其他服务业",
    "P": "教育",
    "Q": "卫生和社会工作",
    "R": "文化、体育和娱乐业",
    "S": "公共管理、社会保障和社会组织",
    "T": "国际组织",
}


@dataclass(frozen=True)
class SectorMapping:
    """外部行业 → GB/T 4754 门类的映射结果。"""

    external: str
    target_code: str
    target_name: str
    ambiguous: bool = False
    alternatives: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "external": self.external,
            "target_code": self.target_code,
            "target_name": self.target_name,
            "ambiguous": self.ambiguous,
            "alternatives": list(self.alternatives),
        }


#: 外部行业 → (GB/T 4754 门类码, 是否歧义, 备选门类)
SECTOR_MAP: Dict[str, Tuple[str, bool, Tuple[str, ...]]] = {
    "Financials": ("J", False, ()),
    "Real_Estate": ("K", False, ()),
    "Consumer": ("F", True, ("O",)),
    "Industrials": ("C", False, ()),
    "Technology": ("I", True, ("M",)),
    "Energy": ("D", True, ("B",)),
    "Healthcare": ("Q", False, ()),
    "Utilities": ("D", True, ("B",)),
    "Retail": ("F", True, ("O",)),
    "Telecom": ("I", True, ("G",)),
}


def map_sector(external: str) -> SectorMapping:
    """把外部行业映射到 GB/T 4754 门类。

    未登记的外部行业**不猜**：抛 KeyError，由调用方登记进附录「待验证项」。
    """
    if external not in SECTOR_MAP:
        raise KeyError(
            "外部行业 %r 未登记 GB/T 4754 映射，请先补进 SECTOR_MAP 或附录待验证项"
            % (external,)
        )
    code, ambiguous, alternatives = SECTOR_MAP[external]
    return SectorMapping(
        external=external,
        target_code=code,
        target_name=GBT4754[code],
        ambiguous=ambiguous,
        alternatives=alternatives,
    )


#: 期限档位（相对分档，不含金额口径）
TERMINAL_BUCKETS: Tuple[Tuple[str, Optional[int], Optional[int]], ...] = (
    ("≤12个月", None, 12),
    ("13—24个月", 13, 24),
    ("25—36个月", 25, 36),
    ("37—60个月", 37, 60),
    (">60个月", 61, None),
)

TERMINAL_BUCKET_LABELS: Tuple[str, ...] = tuple(b[0] for b in TERMINAL_BUCKETS)


def bucket_tenor(months: int) -> str:
    """把期限落进档位。超出范围的值一律落进最后一档，不静默丢弃。"""
    for label, low, high in TERMINAL_BUCKETS:
        if low is None:
            if months <= high:              # type: ignore[operator]
                return label
            continue
        if high is None:
            if months >= low:               # type: ignore[operator]
                return label
            continue
        if low <= months <= high:           # type: ignore[operator]
            return label
    return TERMINAL_BUCKET_LABELS[-1]


#: 相对量口径声明（写进 calibration.json 与 README，避免"搬绝对量"的误解）
RELATIVE_ONLY_NOTE = (
    "校准对象一律为无量纲统计量（占比 / 分位数 / 比率 / 变异系数）。"
    "外部数据的金额为美元、印尼盾等口径，其绝对分布不搬入本项目；"
    "货币与规模的换算关系写成 PRD 附录假设 [D]，不进代码。"
)