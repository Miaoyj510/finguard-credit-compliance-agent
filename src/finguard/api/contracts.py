"""只读 API 的**契约**（切片 9.0）。

**契约先行、框架可替换**：本模块只描述"有哪些端点、每个端点返回什么字段"，
**不含任何 Web 框架依赖**（纯标准库）。这样：

    · 本地演示形态用标准库 ``http.server`` 起（零安装、零依赖）
    · 生产形态切片 9.1 换 FastAPI / uvicorn —— **契约一行不改**

契约是**可测的**：每个响应字段都声明了名字与类型，测试直接断言
"响应里必须有这些字段"——接口形状不会悄悄漂移。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

__all__ = ["Field", "Route", "ROUTES", "route_for", "API_VERSION"]

API_VERSION = "0.1"


@dataclass(frozen=True)
class Field:
    """一个响应字段的契约。"""

    name: str
    type: str                 # string | int | float | bool | object | array
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class Route:
    """一个端点的契约。"""

    method: str
    path: str
    summary: str
    response: Tuple[Field, ...] = ()
    query: Tuple[Field, ...] = ()
    tags: Tuple[str, ...] = ()

    def path_params(self) -> Tuple[str, ...]:
        out = []
        for part in self.path.strip("/").split("/"):
            if part.startswith("{") and part.endswith("}"):
                out.append(part[1:-1])
        return tuple(out)


ROUTES: Tuple[Route, ...] = (
    Route("GET", "/api/health", "健康检查与规模",
          response=(Field("status", "string"), Field("version", "string"),
                    Field("case_count", "int"), Field("event_count", "int")),
          tags=("运维",)),
    Route("GET", "/api/cases", "案件列表",
          response=(Field("cases", "array"), Field("count", "int")),
          tags=("业务",)),
    Route("GET", "/api/cases/{case_id}", "案件详情",
          response=(Field("case_id", "string"), Field("case_type", "string"),
                    Field("product_type", "string"), Field("org_level", "string"),
                    Field("business_date", "string"), Field("stage", "string"),
                    Field("coverage", "object"), Field("opinions", "array")),
          tags=("业务",)),
    Route("GET", "/api/cases/{case_id}/events", "案件事件流（只追加）",
          response=(Field("case_id", "string"), Field("events", "array"),
                    Field("count", "int"), Field("chain_ok", "bool")),
          tags=("业务", "审计")),
    Route("GET", "/api/cases/{case_id}/dossier", "案件卷宗六层与齐全性",
          response=(Field("case_id", "string"), Field("layers", "array"),
                    Field("completeness", "string"), Field("missing_layers", "array"),
                    Field("usable_for_audit", "bool")),
          tags=("业务", "审计")),
    Route("GET", "/api/cases/{case_id}/replay", "案件回放结果",
          response=(Field("case_id", "string"), Field("from_seq", "int"),
                    Field("trace", "object"), Field("matches_dossier", "bool"),
                    Field("usable_for_audit", "bool")),
          query=(Field("from_seq", "int", False, "回放起点 seq"),
                 Field("expected_stage", "string", False, "期望的案件当前阶段")),
          tags=("审计",)),
    Route("GET", "/api/knowledge/search", "依据检索（只返回引用）",
          response=(Field("query", "string"), Field("hits", "array"),
                    Field("count", "int")),
          query=(Field("q", "string", True, "检索词"),
                 Field("role", "string", False, "调用方角色（越权过滤）"),
                 Field("top_k", "int", False, "返回条数")),
          tags=("知识",)),
)


def route_for(method: str, path: str) -> Optional[Route]:
    """按**路径形状**找端点（``{case_id}`` 当通配）。"""
    method = method.upper()
    parts = path.strip("/").split("/")
    for route in ROUTES:
        if route.method != method:
            continue
        route_parts = route.path.strip("/").split("/")
        if len(route_parts) != len(parts):
            continue
        if all(rp.startswith("{") or rp == p for rp, p in zip(route_parts, parts)):
            return route
    return None