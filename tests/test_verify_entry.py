"""验证入口自检（scripts/verify.py）：未实现的切片不得输出假绿。

设计约定第 3 条要求「未启用的切片不计入通过率」。旧代码对 --slice 2
会跑一个空套件并打印「0 / 0 全部通过」退出码 0——典型的 hollow green。

注意：这里只测「不会运行套件」的参数路径，避免与 rules 套件自身递归嵌套。
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts" / "verify.py"


def run_verify(*args):
    return subprocess.run(
        [sys.executable, str(VERIFY), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT),
    )


class TestVerifyEntry(unittest.TestCase):
    def test_未实现切片不输出假绿(self):
        p = run_verify("--slice", "2")
        self.assertEqual(p.returncode, 2)
        self.assertNotIn("全部通过", p.stdout)

    def test_未实现套件不输出假绿(self):
        p = run_verify("--suite", "dsl")
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
