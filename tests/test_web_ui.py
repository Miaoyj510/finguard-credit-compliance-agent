"""切片 7.5 · 界面原型断言（设计系统 + 五个页面）。

界面质量**不能只靠肉眼**——这组断言把它变成可回归的检查项，
对应 UI/UX Pro Max 的交付前检查清单。
"""
from __future__ import annotations

import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
DS = os.path.join(ROOT, "design-system", "finguard")

PAGES = ("index.html", "login.html", "workbench.html", "adjudication.html",
         "alerts.html", "replay.html")

#: 走应用外壳（侧栏 + 共享脚本）的业务页面；登录页是单栏独立页面
SHELL_PAGES = ("index.html", "workbench.html", "adjudication.html",
               "alerts.html", "replay.html")


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def page(name):
    return read(os.path.join(WEB, name))


#: 常见 emoji 区段（Emoji_Presentation / Extended_Pictographic）
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)


class TestDesignSystem(unittest.TestCase):
    def test_主文件与四个页面覆盖齐备(self):
        self.assertTrue(os.path.isfile(os.path.join(DS, "MASTER.md")))
        for name in ("workbench", "adjudication", "alerts", "replay"):
            self.assertTrue(os.path.isfile(os.path.join(DS, "pages", name + ".md")))

    def test_主文件含配色字体与检查清单(self):
        m = read(os.path.join(DS, "MASTER.md"))
        for token in ("--color-primary", "IBM Plex Sans", "prefers-reduced-motion",
                      "交付前检查清单", "决策记录"):
            self.assertIn(token, m)

    def test_决策记录说明改了自动生成结果的哪些地方(self):
        m = read(os.path.join(DS, "MASTER.md"))
        i = m.find("决策记录")
        self.assertGreater(i, 0)
        section = m[i:]
        for token in ("Pattern", "深色", "Fira", "Tailwind"):
            self.assertIn(token, section)


class TestPagesExist(unittest.TestCase):
    def test_五个页面都存在且非空(self):
        for name in PAGES:
            path = os.path.join(WEB, name)
            self.assertTrue(os.path.isfile(path), name)
            self.assertGreater(os.path.getsize(path), 1500, name)

    def test_资源文件存在(self):
        for asset in ("assets/app.css", "assets/app.js"):
            self.assertTrue(os.path.isfile(os.path.join(WEB, asset)), asset)

    def test_每页有语言视口与标题(self):
        for name in PAGES:
            html = page(name)
            self.assertIn('lang="zh-CN"', html, name)
            self.assertIn('name="viewport"', html, name)
            self.assertIn("<title>", html, name)

    def test_每页引用共享样式(self):
        for name in PAGES:
            self.assertIn("assets/app.css", page(name), name)

    def test_业务页面引用共享脚本(self):
        """登录页是单栏独立页面，脚本内联；业务页面走共享 app.js。"""
        for name in SHELL_PAGES:
            self.assertIn("assets/app.js", page(name), name)

    def test_内部链接都指向存在的文件(self):
        for name in PAGES:
            html = page(name)
            for href in re.findall(r'href="([^"#][^"]*)"', html):
                if href.startswith("http"):
                    continue
                target = href.split("?")[0]
                self.assertTrue(
                    os.path.isfile(os.path.join(WEB, target)),
                    "%s 的链接 %s 指向不存在的文件" % (name, href),
                )


class TestAccessibility(unittest.TestCase):
    def test_没有用emoji当图标(self):
        for name in PAGES:
            hits = EMOJI_RE.findall(page(name))
            self.assertEqual(hits, [], "%s 出现 emoji %s" % (name, hits))

    def test_装饰性svg标记为aria_hidden(self):
        for name in PAGES:
            html = page(name)
            for tag in re.findall(r"<svg\b[^>]*>", html):
                self.assertTrue(
                    'aria-hidden="true"' in tag or "aria-label" in tag,
                    "%s 的 svg 缺少 aria-hidden 或 aria-label" % name,
                )

    def test_按钮都声明了type(self):
        for name in PAGES:
            for tag in re.findall(r"<button\b[^>]*>", page(name)):
                self.assertIn("type=", tag, "%s 的按钮缺 type" % name)

    def test_每页有唯一h1(self):
        import re
        for name in PAGES:
            n = len(re.findall(r"<h1[\s>]", page(name)))
            self.assertEqual(n, 1, name)

    def test_表格结构完整(self):
        for name in PAGES:
            html = page(name)
            if "<table>" in html:
                self.assertIn("<thead>", html, name)
                self.assertIn("<tbody>", html, name)

    def test_覆盖度条有无障碍描述(self):
        html = page("workbench.html")
        self.assertIn('role="img"', html)
        self.assertIn("aria-label", html)

    def test_筛选状态用aria_live播报(self):
        html = page("alerts.html")
        self.assertIn('role="status"', html)
        self.assertIn('aria-atomic="true"', html)

    def test_闸门节点不可绕过有aria标记(self):
        html = page("replay.html")
        self.assertIn('aria-disabled="true"', html)
        self.assertIn("不可绕过", html)


class TestStateNotColorOnly(unittest.TestCase):
    def test_等级用文字表达(self):
        html = page("alerts.html")
        for token in ("高", "中", "低"):
            self.assertIn(token, html)
        self.assertIn("badge-danger", html)
        self.assertIn("badge-warning", html)

    def test_闸门用图标加文字(self):
        """闸门 = SVG 图标 + 文字，不用文字符号当图标。"""
        for name in ("workbench.html", "replay.html"):
            html = page(name)
            self.assertIn("GATE-A", html)
            self.assertIn("人工闸门", html)
            self.assertNotIn("⛔", html)

    def test_未判定用标签加原因码(self):
        html = page("workbench.html")
        self.assertIn("须人工判定", html)
        self.assertIn("NO_VERSION_MATCH", html)


class TestLoginPage(unittest.TestCase):
    """登录页：认证要求在 PRD 7.6.7⑩ 与 9.4，界面必须有入口。"""

    def setUp(self):
        self.html = page("login.html")

    def test_登录页存在且有表单(self):
        self.assertIn("<form", self.html)
        self.assertIn('id="login-form"', self.html)

    def test_输入框有可见标签并正确关联(self):
        for field, fid in (("账号（实名工号）", "account"), ("密码", "password")):
            self.assertIn('for="%s"' % fid, self.html, "%s 缺 label 关联" % field)
            self.assertIn('id="%s"' % fid, self.html)

    def test_不是只用placeholder当标签(self):
        """placeholder 不能替代 label（skill 规则 8）。

        账号框可以有 placeholder（但 label 必须可见）；
        密码框**不放** placeholder——切到明文后会变成"密码显示在框里"。
        """
        self.assertIn('for="account"', self.html)
        account_tag = self.html[self.html.index('id="account"'):][:260]
        self.assertIn("placeholder", account_tag)
        pw_tag = self.html[self.html.index('id="password"'):][:160]
        self.assertNotIn("placeholder", pw_tag)

    def test_有自动填充与密码类型(self):
        self.assertIn('autocomplete="username"', self.html)
        self.assertIn('autocomplete="current-password"', self.html)
        self.assertIn('type="password"', self.html)

    def test_密码可见切换有可访问名(self):
        self.assertIn('id="pw-toggle"', self.html)
        self.assertIn('aria-label="显示密码"', self.html)

    def test_声明演示环境与合成账号(self):
        """本地演示形态：必须说清账号是合成的，避免被当成真实客户数据。"""
        self.assertIn("本机演示环境", self.html)
        self.assertIn("合成数据", self.html)

    def test_演示账号与岗位映射键一致(self):
        """登录页填的账号与侧栏岗位映射的键必须一致，否则显示"未登录"。"""
        import re
        js = read(os.path.join(WEB, "assets", "app.js"))
        keys = set(re.findall(r'"(u-\d+)":\s*\{', js))
        accounts = set(re.findall(r'data-account="([^"]+)"', self.html))
        self.assertTrue(keys, "app.js 未定义岗位映射")
        self.assertEqual(keys, accounts, "账号与岗位映射不一致")

    def test_四个演示岗位对应四处闸门(self):
        for token in ("合规审查岗", "风险研判岗", "贷后管理岗", "内审稽核岗",
                      "GATE-A", "GATE-B", "GATE-C"):
            self.assertIn(token, self.html)

    def test_绑定设备指纹的说明在(self):
        """FR-GATE-03：签核动作绑定设备指纹，登录即绑定。"""
        self.assertIn("设备指纹", self.html)

    def test_错误提示就近而非只在顶部(self):
        self.assertIn('class="err" id="login-err"', self.html)

    def test_输入框样式覆盖全部类型(self):
        """漏掉某个 type，表单宽度就会不一致（password 曾漏过）。"""
        css = read(os.path.join(WEB, "assets", "app.css"))
        block = css[css.index('input[type="radio"]'):]
        block = block[:block.index("}")]
        for tp in ("radio", "text", "password"):
            self.assertIn('input[type="%s"]' % tp, block, "输入框样式漏了 %s" % tp)

    def test_登录页是单栏居中(self):
        """登录页不是看板——单栏居中，表单宽度受限。"""
        self.assertIn(".login-shell", self.html)
        self.assertIn("place-items: center", self.html)
        self.assertIn("max-width: 420px", self.html)


class TestCssRules(unittest.TestCase):
    def setUp(self):
        self.css = read(os.path.join(WEB, "assets", "app.css"))

    def test_有焦点可见样式(self):
        self.assertIn(":focus-visible", self.css)
        self.assertNotIn("outline: none", self.css)
        self.assertNotIn("outline:none", self.css)

    def test_尊重减少动效偏好(self):
        self.assertIn("prefers-reduced-motion", self.css)

    def test_有打印样式(self):
        self.assertIn("@media print", self.css)

    def test_响应式断点齐备(self):
        for width in ("375px", "768px", "1024px", "1440px"):
            self.assertIn(width, self.css, "缺断点 %s" % width)

    def test_字号阶符合12到32(self):
        for token in ("--fs-xs: 12px", "--fs-sm: 14px", "--fs-base: 16px",
                      "--fs-lg: 18px", "--fs-xl: 24px", "--fs-2xl: 32px"):
            self.assertIn(token, self.css)

    def test_正文不使用低对比度的muted前景色(self):
        """muted-foreground 只作辅助标签，不得作为正文色。"""
        body_rule = self.css[self.css.index("body {"):self.css.index("body {") + 600]
        self.assertIn("color: var(--color-foreground)", body_rule)


class TestMonospaceFallback(unittest.TestCase):
    """等宽字体没有汉字——mono 栈缺中文回落就会出现"豆腐块"。

    这个坑是**渲染出来才看得到**的：静态断言查不出字形缺失，
    但答辩现场一旦缺字，整屏就都是方框。所以钉成断言。
    """

    def test_等宽字体栈含中文回落(self):
        css = read(os.path.join(WEB, "assets", "app.css"))
        mono = css[css.index("--font-mono:"):css.index("--font-mono:") + 220]
        for fallback in ("Microsoft YaHei", "PingFang SC"):
            self.assertIn(fallback, mono, "等宽字体栈缺中文回落 %s" % fallback)

    def test_等宽单元格不含中文(self):
        """中文必须排在 .mono 之外，否则回落到等宽字体就变方框。"""
        import re
        pat = re.compile(r'class="mono">([^<]*[\u4e00-\u9fff][^<]*)<')
        for name in PAGES:
            hits = pat.findall(page(name))
            self.assertEqual(hits, [], "%s 的等宽单元格含中文：%s" % (name, hits))


class TestOfflineAndPrint(unittest.TestCase):
    def test_没有外部脚本依赖(self):
        for name in PAGES:
            for src in re.findall(r'<script[^>]*src="([^"]+)"', page(name)):
                self.assertFalse(src.startswith("http"), "%s 引入了外部脚本" % name)

    def test_字体有中文回落(self):
        css = read(os.path.join(WEB, "assets", "app.css"))
        for fallback in ("Microsoft YaHei", "PingFang SC", "Noto Sans SC"):
            self.assertIn(fallback, css)

    def test_等宽字体用于编号与金额(self):
        css = read(os.path.join(WEB, "assets", "app.css"))
        self.assertIn("--font-mono", css)
        for name in SHELL_PAGES:
            self.assertIn('class="mono"', page(name), name)


class TestPrdAlignment(unittest.TestCase):
    """界面内容必须与 PRD 10.2 的线框图对得上，不许自造。"""

    def test_工作台含PRD关键字段(self):
        html = page("workbench.html")
        for token in ("FG-20261114-000123", "R-CR-013 v3.2.0", "820,000",
                      "615,000", "25.4%", "DOC-SYS-2025-0031", "CHK-014"):
            self.assertIn(token, html, "工作台缺 PRD 关键字段 %s" % token)

    def test_覆盖度三类计数与PRD一致(self):
        html = page("workbench.html")
        for token in ("已判定", "不适用", "未判定", ">41<", ">9<", ">2<"):
            self.assertIn(token, html)

    def test_裁决面板三选项齐全(self):
        html = page("adjudication.html")
        for token in ("取值 A", "取值 B", "重新抽取", "必填"):
            self.assertIn(token, html)

    def test_预警池含提前量与四档处置(self):
        html = page("alerts.html")
        for token in ("AL-20261112-0031", "R-POST-007 v2.1.0", "62 天",
                      "关闭", "跟进", "上调关注", "触发现场检查"):
            self.assertIn(token, html)

    def test_回放含六层与三个闸门(self):
        html = page("replay.html")
        for token in ("L1", "L2", "L3", "L4", "L5", "L6",
                      "GATE-A", "GATE-B", "六层齐备性检查"):
            self.assertIn(token, html)