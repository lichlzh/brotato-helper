#!/usr/bin/env python3
"""校验 tools/advisor.py 与 mod/scripts/advisor.gd 的同构性。

两份实现无法在同一进程跑（本机无 Godot），所以这里做静态一致性检查：
1. 两边处理的 stat 键集合必须一致；
2. 两边读取的 priors 配置键集合必须一致；
3. priors.json 必须覆盖两边引用的全部配置键。
任何漂移都算失败——这是防止「改了 Python 忘了 GDScript」的护栏。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "tools", "advisor.py")
GD = os.path.join(ROOT, "mod", "scripts", "advisor.gd")
PRIORS = os.path.join(ROOT, "mod", "data", "priors.json")

STAT_RE = re.compile(r'stat_[a-z_]+')


def py_stats(src):
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    body = '"""' not in body and body or re.sub(r'""".*?"""', '', body, flags=re.S)
    return set(m.group(0) for m in STAT_RE.finditer(body))


def gd_stats(src):
    # 注释行里的 stat_ 只用于文档，不参与逻辑，排除掉
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    return set(m.group(0) for m in STAT_RE.finditer(body))


def py_priors_keys(src):
    keys = set(re.findall(r'priors\.get\("([a-z_0-9]+)"', src))
    keys |= set(re.findall(r'priors\["([a-z_0-9]+)"\]', src))
    return keys


_NESTED = {"base", "per_wave", "min", "per_level"}


def gd_priors_keys(src):
    keys = set(re.findall(r'priors\.get\("([a-z_0-9]+)"', src))
    keys |= set(re.findall(r'priors\["([a-z_0-9]+)"\]', src))
    return keys


def _py_funcs(src):
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    return set(re.findall(r'^def\s+([A-Za-z_][A-Za-z_0-9]*)', body, re.M))


def _gd_funcs(src):
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    return set(re.findall(r'^func\s+([A-Za-z_][A-Za-z_0-9]*)', body, re.M))


# 只有单边存在的函数：Python 侧的 CLI/数据装载、GDScript 侧的取数工具，以及
# 命名前缀差异。新增函数如果不在这里，就会被当成「改了一边忘了另一边」拦下来。
_PY_ONLY = {"main", "load_json", "default_priors", "build_index", "reset_pool_cache"}
_GD_ONLY = {"getk", "_r1", "_r6", "_indices_of", "_cmp_score_desc"}
# Python 里带下划线前缀、GDScript 里公开的同名函数
_RENAMED = {"_ensure_pools": "ensure_pools", "_snapshot_after": "snapshot_after"}


def func_parity(pysrc, gdsrc):
    ps = _py_funcs(pysrc)
    gs = _gd_funcs(gdsrc)
    for a, b in _RENAMED.items():
        if a in ps:
            ps.discard(a)
            ps.add(b)
    missing_in_gd = sorted(ps - gs - _PY_ONLY)
    missing_in_py = sorted(gs - ps - _GD_ONLY)
    if missing_in_gd or missing_in_py:
        return "函数字典漂移 gd缺={} py缺={}".format(missing_in_gd, missing_in_py)
    return None


def _block_keys(src, marker):
    """从 marker 处的 { 开始做花括号配对，返回该字典字面量里的顶层键。"""
    at = src.find(marker)
    if at < 0:
        return None
    i = src.index("{", at)
    depth = 0
    end = i
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    return set(re.findall(r'"([a-z_]+)":', src[i:end]))


def output_shape_parity(pysrc, gdsrc):
    """返回结构必须两边一致：改了一边的输出字段，UI 侧就会静默读到默认值。"""
    failures = []
    for label, marker in (("advise_shop 返回值", '{"items": items'),
                          ("每栏位结果", '{"name": s["name"]')):
        pk = _block_keys(pysrc, marker)
        gk = _block_keys(gdsrc, marker)
        if pk is None or gk is None:
            failures.append("{}：找不到标记 {}，请检查返回结构是否被改写".format(label, marker))
            continue
        if pk != gk:
            failures.append("{} 字段漂移 py-gd={} gd-py={}".format(label, sorted(pk - gk), sorted(gk - pk)))
    return failures


def main():
    pysrc = open(PY, encoding="utf-8").read()
    gdsrc = open(GD, encoding="utf-8").read()
    priors = json.load(open(PRIORS, encoding="utf-8"))

    failures = []
    ps, gs = py_stats(pysrc), gd_stats(gdsrc)
    if ps != gs:
        failures.append("stat 键漂移 py-gd={} gd-py={}".format(sorted(ps - gs), sorted(gs - ps)))

    failures.extend(output_shape_parity(pysrc, gdsrc))

    drift = func_parity(pysrc, gdsrc)
    if drift:
        failures.append(drift)

    pk, gk = py_priors_keys(pysrc), gd_priors_keys(gdsrc)
    if pk != gk:
        failures.append("priors 键漂移 py-gd={} gd-py={}".format(sorted(pk - gk), sorted(gk - pk)))

    missing = sorted((pk | gk) - set(priors.keys()) - _NESTED)
    if missing:
        failures.append("priors.json 缺少键: {}".format(missing))

    # special_keys 表必须存在且非空
    if not isinstance(priors.get("special_keys"), dict) or not priors["special_keys"]:
        failures.append("priors.json 的 special_keys 缺失或为空")

    if failures:
        for f in failures:
            print("FAIL", f)
        return 1
    print("OK py/gd 同构：stats={} priors_keys={}".format(len(ps), len(pk)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
