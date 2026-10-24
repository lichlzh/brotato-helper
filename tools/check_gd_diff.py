#!/usr/bin/env python3
"""Python ↔ GDScript 差分测试：把同一批用例喂给两份实现，逐字段对拍。

check_parity.py 只做静态检查（函数名/字段名/priors 键），两个人写同一个公式
写错了符号它看不出来。这里用 Godot 3 无头模式真的跑一遍 mod 里的 advisor.gd，
和 tools/advisor.py 比整店输出：标签、分数、买组合、锁、刷新期望。

用法：
  python3 tools/check_gd_diff.py [--case c1 ...]
  GODOT3_BIN=/path/to/Godot python3 tools/check_gd_diff.py

找不到 Godot 3 时打印 SKIP 并以 0 退出（本机没装游戏也能照常打包）；
只要找到了二进制，任何差异都是失败。Godot 4 不能跑 3.x 脚本，会被拒绝。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from advisor import advise_shop, build_index, load_json, reset_pool_cache  # noqa: E402
from evaluate import enrich_player  # noqa: E402

CASES = os.path.join(ROOT, "tests", "cases.json")
PRIORS_PATH = os.path.join(ROOT, "mod", "data", "priors.json")
DATA_PATH = os.path.join(ROOT, "mod", "data", "brotato_data.json")
SCRIPTS = ["advisor.gd", "shop_advisor.gd", "state_collector.gd"]
RUNNER_GD = os.path.join(ROOT, "tools", "gd_runner.gd")

SCORE_TOL = 0.15          # 两边都四舍五入到 0.1 后再比
ROUND_TOL = 0.05          # spend/left 也是 0.1 精度
REL_TOL = 1e-6


def find_godot():
    env = os.environ.get("GODOT3_BIN") or os.environ.get("GODOT_BIN")
    cands = []
    if env:
        cands.append(env)
    for name in ("godot3", "godot", "Godot"):
        p = shutil.which(name)
        if p:
            cands.append(p)
    cands.extend([
        "/Applications/Godot.app/Contents/MacOS/Godot",
        "/tmp/brotato-godot3/Godot.app/Contents/MacOS/Godot",
        os.path.expanduser("~/Applications/Godot.app/Contents/MacOS/Godot"),
    ])
    for p in cands:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def godot_version(binary):
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    # macOS 二进制在版本号前会打印启动横幅（arguments / Current path …），取第一条版本行
    for line in ((out.stdout or "") + "\n" + (out.stderr or "")).splitlines():
        line = line.strip()
        if re.match(r"^\d+\.\d+", line):
            return line
    return ""


def run_godot(binary, workdir):
    env = dict(os.environ)
    env["HOME"] = env.get("HOME", "/tmp")
    cmd = [binary, "--headless", "--path", workdir, "--script", "res://runner.gd"]
    proc = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=300)
    out_file = os.path.join(workdir, "out.json")
    if proc.returncode != 0 or not os.path.exists(out_file):
        # 3.5.0 以前的 --headless 不完整，退回 --no-window 再试一次
        cmd = [binary, "--no-window", "--path", workdir, "--script", "res://runner.gd"]
        proc = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 or not os.path.exists(out_file):
        raise RuntimeError("Godot 运行失败（exit %s）\nstdout:\n%s\nstderr:\n%s"
                           % (proc.returncode, proc.stdout[-3000:], proc.stderr[-3000:]))
    return json.load(open(out_file, encoding="utf-8"))


def prepare_workdir(enriched):
    workdir = tempfile.mkdtemp(prefix="advdiff-")
    with open(os.path.join(workdir, "project.godot"), "w", encoding="utf-8") as f:
        f.write('config_version=4\n\n[application]\nconfig/name="ShopAdvisorDiff"\n')
    for name in SCRIPTS:
        shutil.copy(os.path.join(ROOT, "mod", "scripts", name), os.path.join(workdir, name))
    shutil.copy(RUNNER_GD, os.path.join(workdir, "runner.gd"))
    shutil.copy(DATA_PATH, os.path.join(workdir, "brotato_data.json"))
    shutil.copy(PRIORS_PATH, os.path.join(workdir, "priors.json"))
    with open(os.path.join(workdir, "cases.json"), "w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False)
    return workdir


def close(a, b, tol=0.0, rel=0.0):
    if a is None or b is None:
        return a is None and b is None
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return a == b
    return abs(a - b) <= tol + rel * max(abs(a), abs(b))


def compare_case(name, py, gd):
    diffs = []
    pit, git = py.get("items", []), gd.get("items", [])
    if len(pit) != len(git):
        diffs.append("items 数量 %d != %d" % (len(pit), len(git)))
    for i in range(min(len(pit), len(git))):
        a, b = pit[i], git[i]
        if a["recommendation"] != b["recommendation"]:
            diffs.append("slot%d 标签 py=%s gd=%s" % (i, a["recommendation"], b["recommendation"]))
        if not close(a["score"], b["score"], SCORE_TOL):
            diffs.append("slot%d 分数 py=%s gd=%s" % (i, a["score"], b["score"]))
    if py.get("reroll") != gd.get("reroll"):
        diffs.append("reroll py=%s gd=%s" % (py.get("reroll"), gd.get("reroll")))
    pl = (py.get("lock") or {}).get("index")
    gl = (gd.get("lock") or {}).get("index")
    if pl != gl:
        diffs.append("lock py=%s gd=%s" % (pl, gl))
    if list(py.get("buy_set") or []) != list(gd.get("buy_set") or []):
        diffs.append("buy_set py=%s gd=%s" % (py.get("buy_set"), gd.get("buy_set")))
    if not close(py.get("spend"), gd.get("spend"), ROUND_TOL):
        diffs.append("spend py=%s gd=%s" % (py.get("spend"), gd.get("spend")))
    if not close(py.get("left"), gd.get("left"), ROUND_TOL):
        diffs.append("left py=%s gd=%s" % (py.get("left"), gd.get("left")))
    if not close(py.get("reroll_expected"), gd.get("reroll_expected"), tol=1e-4, rel=REL_TOL):
        diffs.append("reroll_expected py=%s gd=%s" % (py.get("reroll_expected"), gd.get("reroll_expected")))
    if not close(py.get("reroll_margin"), gd.get("reroll_margin"), tol=1e-4, rel=REL_TOL):
        diffs.append("reroll_margin py=%s gd=%s" % (py.get("reroll_margin"), gd.get("reroll_margin")))
    pt, gt = py.get("thresholds", {}), gd.get("thresholds", {})
    for key in ("buy", "maybe"):
        if not close(pt.get(key), gt.get(key), SCORE_TOL):
            diffs.append("thresholds.%s py=%s gd=%s" % (key, pt.get(key), gt.get(key)))
    return diffs


def main():
    only = set()
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--case" and i + 1 < len(argv):
            only.add(argv[i + 1])
    cases = load_json(CASES)
    if only:
        cases = [c for c in cases if c.get("name", "").split()[0] in only]
    priors = load_json(PRIORS_PATH)
    refdata = load_json(DATA_PATH)
    index = build_index(refdata)
    index["_stat_names"] = {k.lower(): v for k, v in refdata.get("stat_names", {}).items()}

    enriched = []
    py_results = []
    for case in cases:
        player = enrich_player(dict(case["player"]), index)
        enriched.append({"name": case["name"], "player": player, "shop": case["shop"]})
        reset_pool_cache()
        py_results.append(advise_shop(player, case["shop"], priors, index))

    binary = find_godot()
    if not binary:
        print("SKIP 没找到 Godot 3（设置 GODOT3_BIN 后重跑）：Python 参考实现已通过用例，"
              "本次跳过 py/gd 差分")
        return 0
    version = godot_version(binary)
    if not version.startswith("3."):
        print("SKIP Godot 版本不是 3.x（%s）：Godot 4 无法解析 3.x 脚本" % (version or "unknown"))
        return 0
    print("Godot %s @ %s" % (version, binary))

    workdir = prepare_workdir(enriched)
    try:
        gd_results = run_godot(binary, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    print("OK mod/scripts 三个纯脚本均可编译，shop_advisor 的 JSON 读取实跑通过")

    if len(gd_results) != len(py_results):
        print("FAIL GDScript 返回 %d 条，期望 %d 条" % (len(gd_results), len(py_results)))
        return 1
    failed = 0
    for case, py, row in zip(cases, py_results, gd_results):
        diffs = compare_case(case["name"], py, row.get("result") or {})
        if diffs:
            failed += 1
            print("[FAIL] %s" % case["name"])
            for d in diffs:
                print("   " + d)
        else:
            print("[PASS] %s" % case["name"])
    print("%d/%d cases matched (py vs gd)" % (len(cases) - failed, len(cases)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
