"""只读 API 的**标准库实现**（切片 9.0）。

    · ``ApiApp.handle`` 是**纯函数**（方法 + 路径 → 状态码 + JSON），
      不起服务器就能测——这是本切片测试策略的关键。
    · ``make_server`` 用标准库 ``http.server`` 起服务，**零外部依赖**，
      本机 16GB 的演示形态直接可跑；切片 9.1 换 FastAPI 时 ``handle`` 与
      契约（``api/contracts.py``）都不变。

**只读**：本切片不实现任何写端点。写入只经受控接口与人工操作
（PRD 9.4 权限三原则第 2 条「只读优先」）——写端点在切片 9.1 随鉴权一起上。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Mapping, Optional, Tuple

from ..agents.replay import ReplayEngine
from ..storage.persistence import Repository, SqliteEventLog
from .contracts import API_VERSION, ROUTES, Route, route_for

__all__ = ["ApiApp", "make_server", "validate_response"]


class ApiError(Exception):
    """端点内部错误（转成 500）。"""

    def __init__(self, message: str, status: int = 500) -> None:
        super().__init__(message)
        self.status = status


def validate_response(route: Route, payload: Mapping[str, Any]) -> None:
    """响应必须含契约声明的全部必填字段——**接口形状不许悄悄漂移**。"""
    missing = [f.name for f in route.response if f.required and f.name not in payload]
    if missing:
        raise ApiError(
            "端点 %s 的响应缺字段：%s" % (route.path, "、".join(missing)), 500)


class ApiApp:
    """只读 API 应用（纯函数派发）。"""

    def __init__(
        self,
        repository: Repository,
        *,
        event_log: Optional[SqliteEventLog] = None,
        doc_store: Optional[Any] = None,
    ) -> None:
        self.repository = repository
        self.event_log = event_log or SqliteEventLog(repository)
        self.doc_store = doc_store

    # ---------------- 派发 ----------------
    def handle(
        self,
        method: str,
        path: str,
        query: Optional[Mapping[str, str]] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """处理一次请求，返回 ``(状态码, 响应体)``。"""
        route = route_for(method, path)
        if route is None:
            return 404, {"error": "未知端点：%s %s" % (method, path)}
        params = self._path_params(route, path)
        args = dict(query or {})
        try:
            handler = getattr(self, "_h_" + route.path.strip("/").replace("/", "_")
                             .replace("{", "").replace("}", ""))
            payload = handler(params, args)
        except ApiError as exc:
            return exc.status, {"error": str(exc)}
        except KeyError as exc:
            return 404, {"error": "未找到：%s" % exc}
        validate_response(route, payload)
        return 200, payload

    @staticmethod
    def _path_params(route: Route, path: str) -> Dict[str, str]:
        parts = path.strip("/").split("/")
        route_parts = route.path.strip("/").split("/")
        out: Dict[str, str] = {}
        for rp, p in zip(route_parts, parts):
            if rp.startswith("{"):
                out[rp[1:-1]] = p
        return out

    # ---------------- 端点 ----------------
    def _h_api_health(self, params, args):
        return {
            "status": "ok",
            "version": API_VERSION,
            "case_count": len(self.repository.list_cases()),
            "event_count": len(self.event_log.events()),
        }

    def _h_api_cases(self, params, args):
        cases = [
            {
                "case_id": c["case_id"],
                "case_type": c["case_type"],
                "product_type": c["product_type"],
                "org_level": c["org_level"],
                "business_date": c["business_date"],
                "stage": c["stage"],
            }
            for c in self.repository.list_cases()
        ]
        return {"cases": cases, "count": len(cases)}

    def _h_api_cases_case_id(self, params, args):
        row = self.repository.load_case(params["case_id"])
        if row is None:
            raise ApiError("案件不存在：%s" % params["case_id"], 404)
        payload = row["payload"]
        return {
            "case_id": row["case_id"],
            "case_type": row["case_type"],
            "product_type": row["product_type"],
            "org_level": row["org_level"],
            "business_date": row["business_date"],
            "stage": row["stage"],
            "coverage": payload.get("coverage", {}),
            "opinions": list(self.repository.load_opinions(row["case_id"])),
        }

    def _h_api_cases_case_id_events(self, params, args):
        case_id = params["case_id"]
        if self.repository.load_case(case_id) is None:
            raise ApiError("案件不存在：%s" % case_id, 404)
        events = self.event_log.events(case_id)
        chain = self.event_log.verify()
        return {
            "case_id": case_id,
            "events": [e.to_dict() for e in events],
            "count": len(events),
            "chain_ok": chain.ok,
        }

    def _h_api_cases_case_id_dossier(self, params, args):
        case_id = params["case_id"]
        dossier = self.repository.load_dossier(case_id)
        verification = dossier.verify()
        return {
            "case_id": case_id,
            "layers": [s.to_dict() for s in dossier.layers()],
            "completeness": verification.completeness.value,
            "missing_layers": [l.value for l in verification.missing_layers],
            "usable_for_audit": verification.usable_for_audit,
        }

    def _h_api_cases_case_id_replay(self, params, args):
        case_id = params["case_id"]
        dossier = self.repository.load_dossier(case_id)
        engine = ReplayEngine(self.event_log, dossier)
        from_seq = int(args.get("from_seq", 1) or 1)
        expected = args.get("expected_stage") or None
        result = engine.replay(case_id, from_seq=from_seq, expected_stage=expected)
        return {
            "case_id": case_id,
            "from_seq": result.from_seq,
            "trace": result.trace.to_dict(),
            "matches_dossier": result.matches_dossier,
            "usable_for_audit": result.usable_for_audit,
        }

    def _h_api_knowledge_search(self, params, args):
        query = (args.get("q") or "").strip()
        if not query:
            raise ApiError("检索词 q 必填", 400)
        if self.doc_store is None:
            return {"query": query, "hits": [], "count": 0}
        from ..knowledge.retrieval import DocRetriever
        retriever = DocRetriever(self.doc_store)
        top_k = int(args.get("top_k", 5) or 5)
        hits = retriever.search(query, top_k=top_k, role=args.get("role") or None)
        return {
            "query": query,
            "hits": [h.to_dict() for h in hits],
            "count": len(hits),
        }


def make_server(app: ApiApp, host: str = "127.0.0.1", port: int = 8765):
    """用标准库起一个只读 API 服务（演示形态用）。"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _respond(self, status: int, payload: Mapping[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:      # noqa: N802
            parsed = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            status, payload = app.handle("GET", parsed.path, query)
            self._respond(status, payload)

        def log_message(self, fmt, *args):   # 静音默认访问日志
            return

    return ThreadingHTTPServer((host, port), Handler)