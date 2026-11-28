"""切片 9.0 · 持久化与只读 API 断言。

三个关键证据：

    1. **跨进程哈希链**：换一个 Repository 实例（模拟另一个进程）打开同一个库，
       链仍能整链重算通过——只追加的保证从内存搬到了文件。
    2. **回放引擎零改动**：切片 6 的 ``ReplayEngine`` 直接吃落库版事件日志，
       一行都不用改——当初把接口收窄到鸭子类型的好处。
    3. **契约不许漂移**：每个端点的响应必须含契约声明的必填字段。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from finguard.agents.replay import ReplayEngine
from finguard.api.contracts import ROUTES, route_for
from finguard.api.server import ApiApp, ApiError, validate_response
from finguard.storage.dossier import Dossier, DossierLayer
from finguard.storage.persistence import Repository, SqliteEventLog


class TestRepository(unittest.TestCase):
    def setUp(self):
        self.repo = Repository()

    def test_案件存读往返(self):
        self.repo.save_case(
            "FG-1", case_type="corp_credit_review", product_type="小额经营贷",
            org_level="二级分行", business_date="2026-10-28", stage="S3",
            payload={"coverage": {"judged": 1}},
        )
        row = self.repo.load_case("FG-1")
        self.assertEqual(row["stage"], "S3")
        self.assertEqual(row["payload"]["coverage"]["judged"], 1)

    def test_案件重复保存是更新不是插入(self):
        for stage in ("S3", "S4"):
            self.repo.save_case(
                "FG-1", case_type="t", product_type="p", org_level="o",
                business_date="2026-10-28", stage=stage,
            )
        self.assertEqual(len(self.repo.list_cases()), 1)
        self.assertEqual(self.repo.load_case("FG-1")["stage"], "S4")

    def test_卷宗层与意见存读(self):
        from finguard.domain.post import PostDisposition
        dossier = Dossier("FG-1")
        snapshot = dossier.write(DossierLayer.L5, {"gates": {"GATE-A": {"signed_by": "u"}}})
        self.repo.save_dossier_layer("FG-1", snapshot)
        loaded = self.repo.load_dossier("FG-1")
        self.assertEqual(len(loaded.layers()), 1)
        # 只写了一层 -> 六层不齐 -> 判不完整且不可取证（PRD 8.9）
        verification = loaded.verify()
        self.assertEqual(verification.completeness.value, "不完整")
        self.assertEqual(len(verification.missing_layers), 5)
        self.assertFalse(verification.usable_for_audit)


class TestCrossProcessChain(unittest.TestCase):
    """哈希链从内存搬到文件后，跨进程仍然可校验。"""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.remove(self.path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            p = self.path + suffix
            if os.path.exists(p):
                os.remove(p)

    def test_另一进程打开库仍能整链校验(self):
        first = Repository(self.path)
        log = SqliteEventLog(first)
        log.append("FG-1", "stage_transition", {"to_stage": "S1"})
        log.append("FG-1", "gate_signed", {"gate_id": "GATE-A"})
        first.close()

        second = Repository(self.path)          # 模拟另一个进程
        log2 = SqliteEventLog(second)
        self.assertEqual(len(log2.events()), 2)
        self.assertTrue(log2.verify().ok)
        self.assertEqual([e.seq for e in log2.events()], [1, 2])
        second.close()

    def test_落库事件被改动则链校验失败(self):
        repo = Repository(self.path)
        log = SqliteEventLog(repo)
        log.append("FG-1", "stage_transition", {"to_stage": "S1"})
        repo.close()
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE events SET payload = ? WHERE seq = 1",
                     ('{"to_stage": "S9"}',))
        conn.commit()
        conn.close()

        repo2 = Repository(self.path)
        result = SqliteEventLog(repo2).verify()
        self.assertFalse(result.ok)
        self.assertEqual(result.broken_seq, 1)
        repo2.close()

    def test_回放引擎吃落库事件零改动(self):
        """切片 6 的 ReplayEngine 直接吃 SqliteEventLog——接口是鸭子类型。"""
        repo = Repository(self.path)
        log = SqliteEventLog(repo)
        log.append("FG-1", "stage_transition", {"to_stage": "S1"})
        log.append("FG-1", "stage_transition", {"to_stage": "S2"})
        dossier = Dossier("FG-1")
        engine = ReplayEngine(log, dossier)
        result = engine.replay("FG-1")
        self.assertEqual(result.trace.stages, ("S1", "S2"))
        repo.close()


class TestApiContract(unittest.TestCase):
    def setUp(self):
        self.repo = Repository()
        self.app = ApiApp(self.repo)
        self.repo.save_case(
            "FG-1", case_type="corp_credit_review", product_type="小额经营贷",
            org_level="二级分行", business_date="2026-10-28", stage="S3",
            payload={"coverage": {"judged": 41, "not_applicable": 9, "unresolved": 2}},
        )

    def test_每个端点都有契约(self):
        for route in ROUTES:
            self.assertTrue(route.response, "%s 缺响应契约" % route.path)

    def test_健康检查(self):
        status, payload = self.app.handle("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

    def test_案件列表(self):
        status, payload = self.app.handle("GET", "/api/cases")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["cases"][0]["case_id"], "FG-1")

    def test_案件详情含覆盖度与意见(self):
        status, payload = self.app.handle("GET", "/api/cases/FG-1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["coverage"]["judged"], 41)
        self.assertIsInstance(payload["opinions"], list)

    def test_事件端点给出链校验结果(self):
        log = SqliteEventLog(self.repo)
        log.append("FG-1", "stage_transition", {"to_stage": "S1"})
        status, payload = self.app.handle("GET", "/api/cases/FG-1/events")
        self.assertEqual(status, 200)
        self.assertTrue(payload["chain_ok"])
        self.assertEqual(payload["count"], 1)

    def test_卷宗端点给齐全性(self):
        status, payload = self.app.handle("GET", "/api/cases/FG-1/dossier")
        self.assertEqual(status, 200)
        self.assertEqual(payload["completeness"], "不完整")
        self.assertFalse(payload["usable_for_audit"])

    def test_回放端点与引擎一致(self):
        log = SqliteEventLog(self.repo)
        log.append("FG-1", "stage_transition", {"to_stage": "S1"})
        status, payload = self.app.handle("GET", "/api/cases/FG-1/replay")
        self.assertEqual(status, 200)
        self.assertEqual(payload["trace"]["stages"], ["S1"])

    def test_未知端点404(self):
        status, _ = self.app.handle("GET", "/api/nope")
        self.assertEqual(status, 404)

    def test_缺检索词400(self):
        status, _ = self.app.handle("GET", "/api/knowledge/search")
        self.assertEqual(status, 400)

    def test_响应缺字段被契约拦住(self):
        route = route_for("GET", "/api/health")
        with self.assertRaises(ApiError):
            validate_response(route, {"status": "ok"})

    def test_检索端点只返回引用(self):
        from finguard.knowledge.chunker import PolicyDocument, chunk_policy_clauses
        from finguard.knowledge.corpus import DocStore
        doc = PolicyDocument(
            doc_id="DOC-1", title="制度", source_ref="〔2025〕1 号", version="1",
            body="第一章 总则\n第一条 申报月营收与流水偏离的应当说明理由并计算偏离幅度。\n",
            acl=("role:内控合规审查岗",),
        )
        store = DocStore(chunk_policy_clauses(doc))
        app = ApiApp(self.repo, doc_store=store)
        status, payload = app.handle("GET", "/api/knowledge/search", {"q": "申报月营收 流水"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["count"] >= 1)
        for hit in payload["hits"]:
            for banned in ("verdict", "severity", "结论", "命中"):
                self.assertNotIn(banned, hit)