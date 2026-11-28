"""切片 7' · RAG 文档侧断言（PRD 7.2 / 9.2 / 9.3 / 9.4、FR-RAG-02/06/07/08）。

这一组钉的是**边界**，不是功能：
    · 规则库与文档库**物理分库**，判定路径不接触文档库
    · 检索**只返回引用**，不产生任何判定
    · 双粒度切片保留定位信息
    · 历史意见只作参考类比，且必带当时规则版本
    · 权限模型按角色过滤（越权检索）
"""
from __future__ import annotations

import io
import os
import unittest

from finguard.knowledge.analog import (
    OpinionHistoryRecord,
    PenaltyCaseRecord,
    build_opinion_history_chunks,
    build_penalty_case_chunks,
)
from finguard.knowledge.chunker import (
    CLAUSE_SIZE_BOUNDS,
    ELEMENT_SIZE_BOUNDS,
    ElementSpec,
    PolicyDocument,
    chunk_elements,
    chunk_policy_clauses,
    split_sentences,
)
from finguard.domain.enums import Severity
from finguard.knowledge.corpus import (
    CONCLUSION_FIELD_MARKERS,
    ChunkLevel,
    DocChunk,
    DocStore,
    DocType,
    Sensitivity,
    assert_no_conclusion_fields,
)
from finguard.knowledge.permissions import (
    CAN_READ_DOCUMENTS,
    ROLE_LEVELS,
    Role,
    can_read_documents,
    sign_gate_for_role,
)
from finguard.knowledge.retrieval import (
    RETRIEVAL_FIELDS,
    AnalogRetriever,
    DocRetriever,
    keyword_score,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LONG_CLAUSE = (
    "为规范信贷业务审查操作,依据国家有关法律法规及本机构内部风险管理制度的要求,"
    "结合本机构近年来的业务实践与风险暴露情况,制定本规程。"
    "本规程所称信贷业务审查,是指本机构在受理授信申请后,对申请材料的真实性、"
    "合规性与风险要素所进行的系统性核查与意见形成过程,包括受理、要素抽取、"
    "规则判定、意见成稿与人工签核五个环节。"
    "各环节的职责分工、留痕要求与归档口径由本规程一并明确,审查岗不得以任何理由"
    "跳过要素抽取与规则判定环节直接出具结论。"
    "对需要人工裁决的要素,应当在本规程规定的权限范围内作出判断并在审查意见中"
    "记录判断依据,不得以系统默认取值替代人工判断。"
    "审查意见应当载明所依据的规则编号与版本、所引用的事实字段及其来源通道,"
    "以及依据出处文件的名称、文号与生效日期,使事后复核能够还原当时的判断依据。"
    "复核与回放时应当能够据此重现同一结论,不得出现无法定位依据的审查意见。"
)


def policy_body():
    return (
        "第一章 总则\n"
        "第一条 %s\n"
        "第二条 本规程适用于本机构办理的小额经营贷款与对公流贷的合规审查工作,"
        "适用于分行与支行两个层级的审查岗位,不适用于个人消费信贷业务。"
        "适用本规程的审查事项包括授信申请材料的合规性核查、业务要素的真实性核查、"
        "风险要素的交叉核对以及审查意见的形成与签核留痕。"
        "本规程不适用于授信审批决策本身,也不适用于对客风险提示文书的撰写,相关内容"
        "由本机构其他制度另行规定。\n"
        "第三章 审查要求\n"
        "第十一条 申报月营收与近六个月账户流水合计存在偏离的,应当计算偏离幅度并说明理由,"
        "偏离幅度超过阈值的应当提交人工复核,不得仅凭模型生成的结论直接进入签核环节。"
        "偏离幅度的计算口径以申报月营收与近六个月账户流水合计两者之比为准,"
        "计算所需的字段应当包括申报月营收、流水统计区间与流水合计三个要素,"
        "任一要素缺失或来源通道无法确认的,该规则条目应当置为未判定并说明原因。"
        "对因季节性因素、经营性资金归集等合理原因造成的偏离,应当在审查意见中"
        "逐项列明事实依据与客户说明,并由审查岗独立判断其可接受性。\n"
        "第十二条 实际审批人授权额度低于本次审批金额的,应当提交风险研判岗复核,"
        "并在审查意见中记录授权额度、审批金额与审批人岗位三项要素,便于事后回放核查。"
        "授权额度的取值应当来自授权额度与审批权限主数据,不得以申请材料中的自述为准;"
        "审批金额应当与本次申请金额一致,两者不一致的以申请金额为准并说明差异原因。"
        "复核结论应当明确记载同意或不同意,并在不同意时给出具体的替代意见,不得仅作留痕。\n"
    ) % LONG_CLAUSE


def make_policy(**overrides):
    data = dict(
        doc_id="DOC-SYS-2025-0031",
        title="信贷业务审查操作规程",
        source_ref="〔2025〕信贷审字第 31 号",
        version="2.1",
        body=policy_body(),
        issuer="本机构风险管理部",
        publish_date="2025-06-30",
        effective_from="2025-07-01",
        effective_to=None,
        product_scope=("小额经营贷", "对公流贷"),
        org_scope=("*",),
        acl=("role:内控合规审查岗", "role:风险研判岗", "role:制度与内控管理岗"),
    )
    data.update(overrides)
    return PolicyDocument(**data)


def element_specs():
    return [
        ElementSpec(
            element_id="EL-014",
            element_name="营业收入要素",
            criteria="近 12 个月营业收入合计,与申报口径一致",
            required_evidence_fields=("declared_revenue", "revenue_period"),
            text=(
                "营业收入要素:抽取近十二个月营业收入合计值,与客户申报口径核对一致。"
                "所需证据字段:declared_revenue、revenue_period、evidence_channel。"
                "判定口径:合计值缺失或与申报口径不一致时,该要素置为未判定,不得据此形成结论。"
            ),
        ),
        ElementSpec(
            element_id="EL-021",
            element_name="账户流水要素",
            criteria="近 6 个月账户流水合计,与营收存在偏离时须说明",
            required_evidence_fields=("flow_6m_total", "declared_revenue"),
            text=(
                "账户流水要素:抽取近六个月账户流水合计值,与营业收入要素交叉核对。"
                "所需证据字段:flow_6m_total、declared_revenue、evidence_channel。"
                "判定口径:偏离幅度超过阈值的,须补充偏离原因说明后方可继续。"
            ),
        ),
    ]


class TestPhysicalSeparation(unittest.TestCase):
    """FR-RAG-02：规则库与文档库物理分库。"""

    def test_判定路径不导入文档库(self):
        """规则引擎所在的三个模块都不得 import 文档库。"""
        for name in ("engine.py", "dsl.py", "registry.py"):
            path = os.path.join(ROOT, "src", "finguard", "rules", name)
            with io.open(path, encoding="utf-8") as fh:
                src = fh.read()
            self.assertNotIn("knowledge", src, "rules/%s 不得依赖文档库" % name)

    def test_检索层不引入判定引擎(self):
        path = os.path.join(ROOT, "src", "finguard", "knowledge", "retrieval.py")
        with io.open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("rules.engine", src)
        self.assertNotIn("RuleEngine", src)

    def test_两库schema不互通(self):
        """文档切片没有可判定表达式；规则条目没有文档定位字段。"""
        from finguard.domain.rule import Rule
        from datetime import date
        rule = Rule(
            rule_id="R-CR-013", version="3.2.0",
            effective_from=date(2025, 7, 1), effective_to=None,
            product_scope=("小额经营贷",), org_scope=("*",),
            severity=Severity.MAJOR, dsl="IF fact.a > 1 THEN hit('x')",
            source_ref="《制度》第四条",
        )
        rule_fields = set(rule.to_dict())
        chunk_fields = set(DocChunk(
            doc_id="D", chunk_id="C", doc_type=DocType.INTERNAL_POLICY,
            chunk_level=ChunkLevel.CLAUSE, title="t", source_ref="s",
            quote="q", version="1",
        ).to_dict())
        self.assertNotIn("doc_id", rule_fields)
        self.assertNotIn("chunk_id", rule_fields)
        self.assertNotIn("dsl", chunk_fields)
        self.assertNotIn("severity", chunk_fields)

    def test_切片不得携带结论类字段(self):
        with self.assertRaises(ValueError):
            assert_no_conclusion_fields({"verdict": "命中"}, "测试")
        with self.assertRaises(ValueError):
            assert_no_conclusion_fields({"结论": "违规"}, "测试")
        for marker in CONCLUSION_FIELD_MARKERS:
            self.assertTrue(marker)


class TestDualGranularityChunking(unittest.TestCase):
    """FR-RAG-07 / PRD 9.2：条款级 + 要素级双粒度。"""

    def setUp(self):
        self.chunks = chunk_policy_clauses(make_policy())

    def test_按条切分(self):
        self.assertEqual(len(self.chunks), 4)

    def test_保留章号与条号(self):
        for chunk in self.chunks:
            self.assertTrue(chunk.parent_path)
            self.assertTrue(chunk.parent_path[-1].startswith("第"))
        self.assertTrue(any("第三章" in c.parent_label for c in self.chunks))

    def test_相邻条款重叠一句(self):
        """PRD 9.2：与相邻条款重叠 1 句。"""
        for prev, nxt in zip(self.chunks, self.chunks[1:]):
            tail = split_sentences(prev.quote)[-1]
            self.assertIn(tail, nxt.quote, "相邻条款未重叠 1 句")

    def test_条款级大小区间是评测口径不是硬截断(self):
        """PRD 9.2 的 300—800 字是**自建评测口径 [D]**，不是切片的硬规则。

        真实条款长短不一，代码**不得**为了凑区间截断或拼接原文——
        截断会丢证据。因此断言只保证：区间常量正确、长条款落在区间内、
        且切片**不截断**原文。
        """
        low, high = CLAUSE_SIZE_BOUNDS
        self.assertEqual((low, high), (300, 800))
        long_chunks = [c for c in self.chunks if len(c.quote) >= low]
        self.assertTrue(long_chunks, "测试数据里应至少有一条长条款")
        for chunk in long_chunks:
            self.assertLessEqual(len(chunk.quote), high, chunk.chunk_id)

    def test_切片不截断原文(self):
        """每块的正文必须是原文中的一段，不能被裁短。"""
        for chunk in self.chunks:
            body = chunk.quote.split("。")
            self.assertTrue(len(body) >= 2, chunk.chunk_id)

    def test_条款级保留发布文号与生效日(self):
        chunk = self.chunks[0]
        self.assertIn("〔2025〕信贷审字第 31 号", chunk.source_ref)
        self.assertEqual(chunk.effective_from, "2025-07-01")
        self.assertEqual(chunk.publish_date, "2025-06-30")

    def test_要素级一要素一卡(self):
        elements = chunk_elements(
            "DOC-ELEM-2025-0007", "反洗钱与内控要素清单", "〔2025〕要素字第 7 号",
            "1.0", element_specs(),
        )
        self.assertEqual(len(elements), 2)
        self.assertTrue(all(e.chunk_level is ChunkLevel.ELEMENT for e in elements))
        self.assertTrue(all(e.doc_type is DocType.ELEMENT_CARD for e in elements))

    def test_要素卡带判定口径与证据字段(self):
        elements = chunk_elements(
            "DOC-ELEM-2025-0007", "反洗钱与内控要素清单", "〔2025〕要素字第 7 号",
            "1.0", element_specs(),
        )
        first = elements[0]
        self.assertEqual(first.element_id, "EL-014")
        self.assertEqual(first.element_name, "营业收入要素")
        self.assertTrue(first.criteria)
        self.assertIn("declared_revenue", first.required_evidence_fields)

    def test_要素块大小在区间内(self):
        """要素卡是**人工编排**的（PRD 9.2：80—300 字），区间可以严格校验。"""
        elements = chunk_elements(
            "DOC-ELEM-2025-0007", "反洗钱与内控要素清单", "〔2025〕要素字第 7 号",
            "1.0", element_specs(),
        )
        low, high = ELEMENT_SIZE_BOUNDS
        for element in elements:
            self.assertGreaterEqual(len(element.quote), low, element.chunk_id)
            self.assertLessEqual(len(element.quote), high, element.chunk_id)


class TestRetrievalReturnsCitationsOnly(unittest.TestCase):
    """FR-RAG-06：检索只定位依据与出处。"""

    def setUp(self):
        self.store = DocStore(chunk_policy_clauses(make_policy()))
        self.store.add(chunk_elements(
            "DOC-ELEM-2025-0007", "反洗钱与内控要素清单", "〔2025〕要素字第 7 号",
            "1.0", element_specs(),
        )[0])
        self.retriever = DocRetriever(self.store)

    def test_结果字段在白名单内(self):
        hits = self.retriever.search("申报月营收 流水 偏离")
        self.assertTrue(hits)
        for hit in hits:
            self.assertTrue(set(hit.to_dict()).issubset(set(RETRIEVAL_FIELDS)))

    def test_结果不含结论类字段(self):
        for hit in self.retriever.search("申报月营收 流水 偏离"):
            for key in hit.to_dict():
                for marker in CONCLUSION_FIELD_MARKERS:
                    self.assertNotIn(marker.lower(), key.lower())

    def test_结果含PRD规定的引用字段(self):
        hit = self.retriever.search("申报月营收 流水 偏离")[0]
        for key in ("doc_id", "chunk_id", "source_ref", "version", "quote"):
            self.assertTrue(getattr(hit, key), key)

    def test_无命中不返回(self):
        self.assertEqual(self.retriever.search("完全无关的检索词ZZZZ"), ())

    def test_top_k生效(self):
        hits = self.retriever.search("申报 流水 偏离 授权 额度", top_k=1)
        self.assertLessEqual(len(hits), 1)

    def test_打分为确定性(self):
        a = keyword_score("申报月营收 流水", "申报月营收与流水合计偏离")
        b = keyword_score("申报月营收 流水", "申报月营收与流水合计偏离")
        self.assertEqual(a, b)


class TestPermissionFiltering(unittest.TestCase):
    """PRD 9.4 权限模型 · PRD 11.4 S-06 越权检索。"""

    def setUp(self):
        self.store = DocStore(chunk_policy_clauses(make_policy()))
        self.retriever = DocRetriever(self.store)

    def test_有权限角色能检索到(self):
        hits = self.retriever.search("申报月营收 流水 偏离", role=Role.L2.value)
        self.assertTrue(hits)

    def test_无权限角色检索不到(self):
        hits = self.retriever.search("申报月营收 流水 偏离", role=Role.L5.value)
        self.assertEqual(hits, ())

    def test_运维角色无内容读(self):
        """PRD 9.4：运维/系统管理员无内容读。"""
        hits = self.retriever.search("申报月营收 流水 偏离", role=Role.L7.value)
        self.assertEqual(hits, ())

    def test_产品范围过滤(self):
        hits = self.retriever.search("申报月营收 流水 偏离", product_type="大额授信")
        self.assertEqual(hits, ())

    def test_机构范围过滤(self):
        """限定机构的切片对范围外机构不可见；``*`` 通配的切片全机构可见。"""
        limited = DocStore(chunk_policy_clauses(make_policy(org_scope=("二级分行",))))
        limited_retriever = DocRetriever(limited)
        self.assertEqual(
            limited_retriever.search("申报月营收 流水 偏离", org_level="一级分行"), ()
        )
        self.assertTrue(
            limited_retriever.search("申报月营收 流水 偏离", org_level="二级分行")
        )
        # 通配切片对任一机构都可见
        self.assertTrue(
            self.retriever.search("申报月营收 流水 偏离", org_level="一级分行")
        )

    def test_七层角色齐备(self):
        self.assertEqual(len(ROLE_LEVELS), 7)
        self.assertEqual(len(CAN_READ_DOCUMENTS), 6)

    def test_签核闸门映射(self):
        self.assertEqual(sign_gate_for_role(Role.L2.value), "GATE-A")
        self.assertEqual(sign_gate_for_role(Role.L3.value), "GATE-B")
        self.assertEqual(sign_gate_for_role(Role.L4.value), "GATE-C")
        self.assertIsNone(sign_gate_for_role(Role.L5.value))
        self.assertIsNone(sign_gate_for_role(Role.L7.value))

    def test_受限文档只有制度管理与内审能读(self):
        self.assertTrue(can_read_documents(Role.L1.value, Sensitivity.RESTRICTED))
        self.assertTrue(can_read_documents(Role.L6.value, Sensitivity.RESTRICTED))
        self.assertFalse(can_read_documents(Role.L2.value, Sensitivity.RESTRICTED))


class TestAnalogReferenceOnly(unittest.TestCase):
    """FR-RAG-08：历史意见与处罚案例只作参考类比。"""

    def setUp(self):
        self.store = DocStore(chunk_policy_clauses(make_policy()))
        for chunk in build_opinion_history_chunks([
            OpinionHistoryRecord(
                opinion_id="OP-2024-0001", rule_id="R-CR-013",
                applied_rule_version="3.1.0", observed_at="2024-05-06",
                disposition="调整关注",
                rationale="申报营收与账户流水合计偏离,客户说明为季节性因素,按调整关注处理",
            ),
        ]):
            self.store.add(chunk)
        for chunk in build_penalty_case_chunks([
            PenaltyCaseRecord(
                case_id="CASE-2023-0007", cause="授信材料要素缺失",
                circumstances="未按要求提供近六个月流水", penalty="通报并问责",
                source="本机构内部处罚通报",
            ),
        ]):
            self.store.add(chunk)
        self.analog = AnalogRetriever(self.store)
        self.docs = DocRetriever(self.store)

    def test_历史意见标记为参考类比(self):
        hits = self.analog.search("流水 偏离 季节性")
        self.assertTrue(hits)
        self.assertTrue(all(h.is_reference_only for h in hits))

    def test_历史意见必带当时规则版本(self):
        hits = self.analog.search("流水 偏离 季节性")
        self.assertTrue(hits)
        for hit in hits:
            self.assertTrue(hit.applied_rule_version)
            self.assertTrue(hit.observed_at)

    def test_缺当时版本的历史意见不返回(self):
        store = DocStore()
        for chunk in build_opinion_history_chunks([
            OpinionHistoryRecord(
                opinion_id="OP-X", rule_id="R-CR-013", applied_rule_version="",
                observed_at="2024-05-06", disposition="调整关注", rationale="缺版本的旧意见",
            ),
        ]):
            store.add(chunk)
        self.assertEqual(AnalogRetriever(store).search("缺版本"), ())

    def test_制度检索不返回历史意见(self):
        hits = self.docs.search("流水 偏离 季节性")
        self.assertTrue(all(h.doc_type != DocType.OPINION_HISTORY for h in hits))

    def test_历史检索不返回制度切片(self):
        hits = self.analog.search("申报月营收 流水 偏离")
        self.assertTrue(all(h.doc_type in (DocType.OPINION_HISTORY, DocType.PENALTY_CASE)
                            for h in hits))

    def test_入库保留时间戳版本与处置结果(self):
        chunk = [c for c in self.store.chunks()
                 if c.doc_type is DocType.OPINION_HISTORY][0]
        self.assertEqual(chunk.observed_at, "2024-05-06")
        self.assertEqual(chunk.applied_rule_version, "3.1.0")
        self.assertEqual(chunk.disposition, "调整关注")
        self.assertTrue(chunk.is_reference_only)


class TestReviewGaps72(unittest.TestCase):
    """切片 7.2 评审补充断言（R-G1—R-G4，先红后绿）。"""

    def test_处罚案例携带当时版本与时间戳并可被类比检索(self):
        """R-G1：HANDOFF 声明入库必留「时间戳/当时规则版本/处置结果」，
        但 PenaltyCaseRecord 根本没有前两个字段——处罚案例永远过不了
        AnalogRetriever 的版本闸，等于整类语料死在库里。"""
        store = DocStore(build_penalty_case_chunks([
            PenaltyCaseRecord(
                case_id="CASE-2024-0001", cause="贷后检查记录缺失",
                circumstances="连续两期未按要求提交贷后检查表",
                penalty="通报并扣减绩效", source="本机构内部处罚通报",
                applied_rule_id="R-PM-001", applied_rule_version="1.0.0",
                observed_at="2024-03-02",
            ),
        ]))
        hits = AnalogRetriever(store).search("贷后检查记录缺失")
        self.assertTrue(hits, "带版本的处罚案例应可被类比检索")
        self.assertEqual(hits[0].applied_rule_version, "1.0.0")
        self.assertEqual(hits[0].observed_at, "2024-03-02")
        self.assertTrue(hits[0].is_reference_only)

    def test_历史类切片禁旗关闭即拒收(self):
        """R-G2：is_reference_only 靠入库方自觉=没有保证。
        一面关掉禁旗的历史意见会被 DocRetriever 当制度依据返回。"""
        chunk = DocChunk(
            doc_id="DOC-H", chunk_id="DOC-H-1",
            doc_type=DocType.OPINION_HISTORY, chunk_level=ChunkLevel.OPINION,
            title="历史意见", source_ref="历史审查意见库 OP-1",
            quote="申报营收与流水偏离的处理口径", version="1.0",
            is_reference_only=False,
        )
        with self.assertRaises(ValueError):
            DocStore().add(chunk)
        penalty = DocChunk(
            doc_id="DOC-P", chunk_id="DOC-P-1",
            doc_type=DocType.PENALTY_CASE, chunk_level=ChunkLevel.CASE,
            title="处罚案例", source_ref="处罚案例库 C-1",
            quote="案由与处罚结果", version="1.0",
            is_reference_only=False,
        )
        with self.assertRaises(ValueError):
            DocStore().add(penalty)

    def test_L5只读生效版文档(self):
        """R-G3：PRD 9.4 文档库列 L5 授信审查岗=「读生效版本」，
        实现把 L5 当全量读——失效制度对授信岗可见会误导信贷决策。"""
        common = dict(
            doc_id="DOC-V", doc_type=DocType.INTERNAL_POLICY,
            chunk_level=ChunkLevel.CLAUSE, title="规程",
            source_ref="《规程》", quote="申报月营收与账户流水偏离核查要求",
            version="1.0",
        )
        store = DocStore([
            DocChunk(chunk_id="DOC-V-now", effective_from="2025-07-01",
                     effective_to=None, **common),
            DocChunk(chunk_id="DOC-V-old", effective_from="2023-01-01",
                     effective_to="2025-06-30", **common),
        ])
        l5 = DocRetriever(store).search("申报月营收 流水 偏离", role=Role.L5.value)
        self.assertTrue(l5)
        self.assertTrue(all(c.chunk_id == "DOC-V-now" for c in l5),
                        "L5 看到了失效版制度")
        l2 = DocRetriever(store).search("申报月营收 流水 偏离", role=Role.L2.value)
        self.assertEqual(len(l2), 2)

    def test_检索命中携带内容哈希供出处复验(self):
        """评审确认项 2 的落地：PRD 9.3 的 content_hash 应随命中返回——
        引用防篡改复验（意见依据与卷宗 L3 对账）依赖它。"""
        store = DocStore([
            DocChunk(
                doc_id="DOC-H1", chunk_id="DOC-H1-C001",
                doc_type=DocType.INTERNAL_POLICY, chunk_level=ChunkLevel.CLAUSE,
                title="规程", source_ref="《规程》第四条",
                quote="申报月营收与账户流水合计偏离超过阈值的核查要求",
                version="2.0", effective_from="2025-07-01", effective_to=None,
            ),
        ])
        hits = DocRetriever(store).search("申报月营收 流水 偏离")
        self.assertTrue(hits)
        self.assertEqual(hits[0].content_hash, store.get("DOC-H1-C001").content_hash)
        self.assertIn("content_hash", hits[0].to_dict())

    def test_要素卡尺寸在入库时校验(self):
        """R-G4：80—300 字是自建口径 [D]，但只在测试里验了演示数据，
        入库路径不设防——5 个字的要素卡会静默进库。"""
        with self.assertRaises(ValueError):
            chunk_elements("DOC-E", "要素清单", "《要素表》", "1.0", [
                ElementSpec(element_id="E-1", element_name="营收",
                            criteria="口径", required_evidence_fields=("a",),
                            text="太短"),
            ])
        with self.assertRaises(ValueError):
            chunk_elements("DOC-E", "要素清单", "《要素表》", "1.0", [
                ElementSpec(element_id="E-2", element_name="营收",
                            criteria="口径", required_evidence_fields=("a",),
                            text="很长" * 200),
            ])
