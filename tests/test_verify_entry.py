"""验证入口自检（scripts/verify.py）：未实现的切片不得输出假绿。

设计约定第 3 条要求「未启用的切片不计入通过率」。旧代码对 --slice 2
会跑一个空套件并打印「0 / 0 全部通过」退出码 0——典型的 hollow green。

注意：这里只测「不会运行套件」的参数路径，避免与 rules 套件自身递归嵌套。

「未实现的切片」不写死具体编号：早期版本把 --slice 2 / --suite dsl 写死，
切片 2 一实现，这两条断言就失效了（世界状态一变，测试就红）。
现在改为从 SUITES 注册表里**动态找出**当前仍未实现的套件；万一全部都实现了，
测试跳过而不是失败。
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts" / "verify.py"

sys.path.insert(0, str(ROOT / "scripts"))
import verify as verify_mod   # noqa: E402  —— 直接读套件注册表，避免写死编号


def first_unimplemented_suite():
    """当前仍未实现的套件名；全部实现则返回 None。"""
    for name, cfg in verify_mod.SUITES.items():
        if not cfg["modules"]:
            return name
    return None


def run_verify(*args):
    return subprocess.run(
        [sys.executable, str(VERIFY), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT),
    )


class TestVerifyEntry(unittest.TestCase):
    def test_未实现切片不输出假绿(self):
        name = first_unimplemented_suite()
        if name is None:
            self.skipTest("所有切片均已实现，无「未实现」路径可测")
        slice_no = verify_mod.SUITES[name]["slice"]
        p = run_verify("--slice", str(slice_no))
        self.assertEqual(p.returncode, 2)
        self.assertNotIn("全部通过", p.stdout)

    def test_未实现套件不输出假绿(self):
        name = first_unimplemented_suite()
        if name is None:
            self.skipTest("所有套件均已实现，无「未实现」路径可测")
        p = run_verify("--suite", name)
        self.assertEqual(p.returncode, 2)
        self.assertNotIn("全部通过", p.stdout)

    def test_未知切片被拒(self):
        p = run_verify("--slice", "99")
        self.assertEqual(p.returncode, 2)

    def test_list可运行(self):
        p = run_verify("--list")
        self.assertEqual(p.returncode, 0)
        self.assertIn("rules", p.stdout)


if __name__ == "__main__":
    unittest.main()
