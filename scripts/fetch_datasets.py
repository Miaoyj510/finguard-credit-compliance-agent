#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""外部数据集抓取与固化（切片 6.5）。

**这份脚本把外部压缩包当不可信数据处理**，四条硬规矩：

1. **只下载、只校验、只解压**——**绝不执行归档内的任何文件**。
   归档里带 ``generator/*.py`` 是正常的（上游为了让人复现数据），
   但那只是给人读的源码，本脚本不会 import、不会 exec、不会运行。
2. **SHA256 钉死**——Kaggle 的公开接口不暴露 commit revision，因此用
   ``sha256(归档) + last_updated`` 双钉。归档字节数**只作辅助**：
   实测压缩包大小与接口报的展开体积对不上（107 MB → 32 MB），
   size 挡不住内容替换，只有哈希能。
3. **解压防目录穿越**——逐个成员检查路径，挡住 ``../`` 与绝对路径。
4. **原始件不进 Git**——落在 ``data/external/_raw/``，由 ``.gitignore`` 挡掉。

用法
----
    python -I scripts/fetch_datasets.py --verify-only   # 只校验本地已有文件
    python -I scripts/fetch_datasets.py                  # 校验 + 缺什么补什么
    python -I scripts/fetch_datasets.py --id credit-50k  # 只处理指定数据集
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import zipfile
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "data", "external", "manifests", "external_datasets.json")
RAW_DIR = os.path.join(ROOT, "data", "external", "_raw")

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

#: 归档内**允许**出现的可执行后缀——只用于**拒绝**它们落地成可执行文件。
#: 本脚本不会运行它们，但也不把它们解压出来，避免误执行的风险。
FORBIDDEN_SUFFIXES = (".py", ".pyc", ".pyd", ".exe", ".dll", ".bat", ".cmd", ".sh", ".ps1")


class FetchError(Exception):
    """抓取或校验失败。"""


def sha256_of(path: str, chunk: int = 1 << 20) -> str:
    """分块算 SHA256（大文件不进内存）。"""
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def load_manifest(path: str = MANIFEST) -> Dict[str, Any]:
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def safe_members(zf: zipfile.ZipFile) -> List[zipfile.ZipInfo]:
    """过滤掉有目录穿越风险的成员。"""
    safe: List[zipfile.ZipInfo] = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            raise FetchError("归档成员路径可疑，已拒绝：%s" % info.filename)
        safe.append(info)
    return safe


def _common_prefix(members: List[zipfile.ZipInfo]) -> Optional[str]:
    """归档若整体套了一层同名顶层目录（Kaggle 打包的常见形态），剥掉它。

    剥掉之后 ``_raw/<id>/`` 直接就是数据集根，路径才稳定。
    """
    tops = set()
    for info in members:
        name = info.filename.replace("\\", "/").lstrip("/")
        if "/" in name:
            tops.add(name.split("/", 1)[0])
        elif name:
            tops.add(name)
    if len(tops) != 1:
        return None
    only = tops.pop()
    if any(m.filename.replace("\\", "/").lstrip("/") == only for m in members):
        return None          # 顶层就是一个文件，不是目录
    return only + "/"


def extract_archive(archive: str, dest: str) -> Dict[str, Any]:
    """安全解压。**不执行归档内任何文件。**"""
    if not os.path.isdir(dest):
        os.makedirs(dest)
    extracted = 0
    skipped_executable = 0
    with zipfile.ZipFile(archive) as zf:
        members = safe_members(zf)
        prefix = _common_prefix(members)
        for info in members:
            rel = info.filename.replace("\\", "/").lstrip("/")
            if prefix and rel.startswith(prefix):
                rel = rel[len(prefix):]
            if not rel:
                continue
            target = os.path.normpath(os.path.join(dest, rel))
            if not target.startswith(os.path.normpath(dest) + os.sep) and target != os.path.normpath(dest):
                raise FetchError("解压目标越界，已拒绝：%s" % info.filename)
            if info.is_dir():
                if not os.path.isdir(target):
                    os.makedirs(target)
                continue

            # 可执行后缀的文件**不解压**：上游的生成器源码只供人工阅读，
            # 解压出来就有被误执行的风险。
            if rel.lower().endswith(FORBIDDEN_SUFFIXES):
                skipped_executable += 1
                continue
            parent = os.path.dirname(target)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent)
            with zf.open(info) as src, io.open(target, "wb") as out:
                out.write(src.read())
            extracted += 1
    return {"extracted": extracted, "skipped_executable": skipped_executable}


def download(url: str, dest: str, timeout: int = 600) -> str:
    """下载到临时文件再改名——避免半截文件被当成完整包校验。"""
    import urllib.request

    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "FinGuard/6.5"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, io.open(tmp, "wb") as out:
        while True:
            block = resp.read(1 << 20)
            if not block:
                break
            out.write(block)
    os.replace(tmp, dest)
    return dest


def verify_one(entry: Dict[str, Any], raw_dir: str = RAW_DIR) -> Dict[str, Any]:
    """校验一个数据集的本地文件。**哈希不符即失败**，不自动接受。"""
    archive = os.path.join(raw_dir, "%s.zip" % entry["id"])
    if not os.path.isfile(archive):
        return {"id": entry["id"], "state": "missing", "path": archive}
    actual = sha256_of(archive)
    expected = entry["sha256"].lower()
    if actual != expected:
        return {
            "id": entry["id"],
            "state": "hash_mismatch",
            "path": archive,
            "expected": expected,
            "actual": actual,
        }
    size = os.path.getsize(archive)
    if size != entry["bytes"]:
        return {
            "id": entry["id"],
            "state": "size_mismatch",
            "path": archive,
            "expected": entry["bytes"],
            "actual": size,
        }
    return {"id": entry["id"], "state": "ok", "path": archive, "bytes": size}


def process(entry: Dict[str, Any], raw_dir: str = RAW_DIR, do_download: bool = True) -> Dict[str, Any]:
    """校验 + 必要时下载 + 解压。"""
    state = verify_one(entry, raw_dir)
    if state["state"] == "ok" and not do_download:
        return state
    if state["state"] == "hash_mismatch":
        raise FetchError(
            "数据集 %s 的 SHA256 不符：期望 %s，实得 %s。\n"
            "  拒绝自动接受——请人工确认来源后更新 manifest，或删除本地文件重下。"
            % (entry["id"], state["expected"], state["actual"])
        )
    if state["state"] == "missing" and do_download:
        if not os.path.isdir(raw_dir):
            os.makedirs(raw_dir)
        archive = os.path.join(raw_dir, "%s.zip" % entry["id"])
        download(entry["url"], archive)
        state = verify_one(entry, raw_dir)
        if state["state"] != "ok":
            raise FetchError(
                "数据集 %s 下载后校验未通过（%s）：期望 %s，实得 %s"
                % (entry["id"], state["state"], state.get("expected"), state.get("actual"))
            )
    if state["state"] == "ok":
        dest = os.path.join(raw_dir, entry["id"])
        info = extract_archive(state["path"], dest)
        state["extract"] = info
        state["state"] = "ok"
    return state


def main() -> int:
    manifest = load_manifest()
    ap = argparse.ArgumentParser(description="抓取并固化外部数据集（只下载 / 只校验 / 只解压）")
    ap.add_argument("--verify-only", action="store_true", help="只校验本地已有文件，不下载")
    ap.add_argument("--id", default=None, help="只处理指定数据集 id")
    args = ap.parse_args()

    entries = manifest["datasets"]
    if args.id:
        entries = [e for e in entries if e["id"] == args.id]
        if not entries:
            print("未知数据集 id：%s" % args.id)
            return 2

    failures = 0
    for entry in entries:
        try:
            state = process(entry, do_download=not args.verify_only)
        except FetchError as exc:
            failures += 1
            print("%sFAIL%s %-14s %s" % (RED, RESET, entry["id"], exc))
            continue
        if state["state"] == "ok":
            extra = ""
            if "extract" in state:
                extra = "（解压 %d 个文件，拒收 %d 个可执行文件）" % (
                    state["extract"]["extracted"], state["extract"]["skipped_executable"])
            print("%s OK %s %-14s %s%s" % (GREEN, RESET, entry["id"], state["path"], extra))
        else:
            failures += 1
            print("%sFAIL%s %-14s %s" % (RED, RESET, entry["id"], state["state"]))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())