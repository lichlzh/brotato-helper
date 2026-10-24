#!/usr/bin/env python3
"""跑 tests/cases.json 回归用例：从装备武器推导 total_dps/melee_share，再调用引擎。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from advisor import (advise_shop, build_index, comb, default_priors, expected_top1,
                     load_json, num, set_first_count, set_gain, weapon_dps)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIORS = default_priors()


def self_test_math():
    """E[max] 解析式对拍手算值：公式被改坏时这里先炸，不要让用例默默擦边。"""
    from math import comb as py_comb     # 标准库当独立 oracle，别信我心算的组合数
    assert abs(comb(3, 2) - 3.0) < 1e-9
    assert abs(comb(300, 4) - py_comb(300, 4)) < 1.0
    for n in (5, 40, 251):
        for k in (1, 2, 4):
            assert abs(comb(n, k) - py_comb(n, k)) < 1e-6, (n, k)
    # N=3 抽 2：{1,2}{1,3}{2,3} 的 max 为 2,3,3 → 8/3
    assert abs(expected_top1([1.0, 2.0, 3.0], 2)["value"] - 8.0 / 3.0) < 1e-9
    # 抽 1 件就是均值
    assert abs(expected_top1([1.0, 2.0, 3.0, 4.0], 1)["value"] - 2.5) < 1e-9
    # 池子不够抽 k 件 → 取最好那件
    assert abs(expected_top1([5.0, 7.0], 4)["value"] - 7.0) < 1e-9
    assert expected_top1([], 4)["ok"] is False
    # 穷举对拍：直接枚举所有抽样组合取 max 的均值
    from itertools import combinations
    for scores in ([1.0, 2.0, 3.0], [4.0, -2.0, 7.0, 1.0, 5.0], [12.0, 3.0, 9.0, 9.0, 1.0, 6.0]):
        n = len(scores)
        for k in range(1, n + 1):
            draws = list(combinations(scores, k))
            brute = sum(max(d) for d in draws) / float(len(draws))
            got = expected_top1(scores, k)["value"]
            assert abs(got - brute) < 1e-9, (scores, k, got, brute)
    return True


def enrich_player(player, index):
    total = 0.0
    melee = 0.0
    for wid in player.get("weapon_ids", []):
        ref = index.get(wid)
        if not ref:
            continue
        dps = weapon_dps(dict(ref["base"], type=ref.get("weapon_type", "melee")), player, PRIORS)
        total += dps
        if ref.get("weapon_type") == "melee":
            melee += dps
    player["total_dps"] = total if total > 0 else 50.0
    player["melee_share"] = (melee / total) if total > 0 else 0.5
    return player


def margin_of(result, idx):
    """这件商品离「换标签」还差多少分。buy/lock 由买入门槛决定，maybe/skip 由可考虑线决定。
    正数=稳，负数=已越过线；用例把这个数记下来，调 priors 调过头时会立刻报警，
    而不是等标签在阈值附近来回翻还「擦边通过」。"""
    thr = result.get("thresholds", {})
    it = result["items"][idx]
    if it["recommendation"] in ("buy", "lock"):
        return it["score"] - num(thr.get("buy", 0.0))
    return it["score"] - num(thr.get("maybe", 0.0))


def check_invariants(result, case_name):
    """不写进用例也不该被破坏的性质：违反即失败，避免「买组合」逻辑被改坏后
    只有个别用例擦边才发现。返回错误信息列表。"""
    errs = []
    items = result["items"]
    buy_set = set(result.get("buy_set", []))
    if result.get("spend", 0.0) > result.get("budget", 0.0) + 1e-6:
        errs.append("超预算: spend={} > budget={}".format(result["spend"], result["budget"]))
    for i, it in enumerate(items):
        if it["recommendation"] == "buy" and it["price"] > 0 and i not in buy_set:
            errs.append("slot{} 标 buy 但不在 buy_set".format(i))
        if it["recommendation"] == "lock" and i in buy_set:
            errs.append("slot{} 既建议买又建议锁".format(i))
        if it["recommendation"] == "maybe" and i not in buy_set:
            errs.append("slot{} 标 maybe 但不在 buy_set".format(i))
        if i in buy_set and it["price"] > result.get("budget", 0.0):
            errs.append("slot{} 单价 {} 超过总预算".format(i, it["price"]))
    lock = result.get("lock")
    if lock is not None and lock["index"] in buy_set:
        errs.append("lock 指向推荐购买的那件")
    return errs


def self_test_sets(index):
    """套装账目直接对拍参考表。用例里没法测这件事：整店分数同时受 dps 锚点、
    负重、流派占比影响，把「套装加了几分」淹没在噪声里；这里只喂武器 id，逐条核对增量。
    用的是参考表里的真实职业：拳=UNARMED(+3 闪避/档)、小刀=PRECISE(+3 暴击/档)、
    利爪=UNARMED+PRECISE、默认 set_first_count=2。"""
    first = set_first_count(PRIORS)
    assert first == 2, "参考实现假定 2 件才起效，priors 被改了就要一起改这里"

    def gain(ids, add):
        return set_gain(ids, add, index, first)

    # 1) 第 1 把同职业不该有分（门槛是 2 件）
    assert "stat_dodge" not in gain(["weapon_knife_1"], ["weapon_fist_1"]), \
        "1 把徒手就给了徒手套加分，set_first_count 没生效"
    # 2) 1→2 件：吃到第 1 档 = +3 闪避；同件的精准职业只有 1 把，不给
    g = gain(["weapon_fist_1"], ["weapon_claw_1"])
    assert abs(g.get("stat_dodge", 0.0) - 3.0) < 1e-9, g
    assert "stat_crit_chance" not in g, "精准只有 1 把，不该跨门槛"
    # 3) 两个职业同时跨门槛
    g = gain(["weapon_fist_1", "weapon_knife_1"], ["weapon_claw_1"])
    assert abs(g.get("stat_dodge", 0.0) - 3.0) < 1e-9 and \
        abs(g.get("stat_crit_chance", 0.0) - 3.0) < 1e-9, g
    # 4) 档位是累计值：连拿两把 = 第 3 档（+6），不是 1+1 档相加（+3+3 也对，
    #    但拿 4→5 时才会露馅）——这里直接核对 1→3 的结果
    g = gain(["weapon_fist_1", "weapon_knife_1"], ["weapon_claw_1", "weapon_claw_1"])
    assert abs(g.get("stat_dodge", 0.0) - 6.0) < 1e-9, g
    # 5) 封顶：最后一档之后不再涨（set_first_count=2 时 6 件到顶），
    #    否则后期会把「第 7 把武器」估得越来越值
    g = gain(["weapon_fist_1"] * 6, ["weapon_claw_1"])
    assert g == {}, "超过最后一档还在给徒手套加分，档位没封顶"
    # 6) 套装不会和玩家已有属性重复计数：baseline 只作差，不进 stats
    assert "stat_dodge" not in gain([], ["weapon_claw_1"]), "没有武器却给了套装分"


def run(path=None):
    self_test_math()
    path = path or os.path.join(ROOT, "tests", "cases.json")
    refdata = load_json(os.path.join(ROOT, "mod", "data", "brotato_data.json"))
    index = build_index(refdata)
    index["_stat_names"] = {k.lower(): v for k, v in refdata.get("stat_names", {}).items()}
    self_test_sets(index)
    cases = load_json(path)
    ok = 0
    for case in cases:
        player = enrich_player(dict(case["player"]), index)
        result = advise_shop(player, case["shop"], PRIORS, index)
        checks = []
        tol = num(case.get("margin_tol", 2.0))
        for exp in case.get("expect", {}).get("items", []):
            got = result["items"][exp["index"]]["recommendation"]
            checks.append((exp["index"], exp["recommendation"], got, got == exp["recommendation"]))
            if "margin" in exp:
                gm = margin_of(result, exp["index"])
                checks.append(("margin[%d]" % exp["index"], exp["margin"], round(gm, 1),
                               abs(gm - exp["margin"]) <= tol))
                if abs(exp["margin"]) < 1.5:
                    print("   NOTE {}: slot{} 阈值距离只有 {:.1f}，标签在刀口上".format(
                        case["name"], exp["index"], exp["margin"]))
        exp_reroll = case.get("expect", {}).get("reroll")
        if exp_reroll is not None:
            checks.append(("reroll", exp_reroll, result["reroll"], exp_reroll == result["reroll"]))
        if "reroll_margin" in case.get("expect", {}):
            exp_rm = case["expect"]["reroll_margin"]
            got_rm = result.get("reroll_margin")
            ok_rm = got_rm is not None and abs(got_rm - exp_rm) <= tol
            checks.append(("reroll_margin", exp_rm, None if got_rm is None else round(got_rm, 1), ok_rm))
            if exp_rm is not None and abs(exp_rm) < 1.5:
                print("   NOTE {}: 刷新判定距离阈值只有 {:.1f}".format(case["name"], exp_rm))
        exp_lock = case.get("expect", {}).get("lock_index")
        if exp_lock is not None:
            got_lock = result["lock"]["index"] if result["lock"] else None
            checks.append(("lock", exp_lock, got_lock, exp_lock == got_lock))
        exp_set = case.get("expect", {}).get("buy_set")
        if exp_set is not None:
            got_set = result.get("buy_set", [])
            checks.append(("buy_set", exp_set, got_set, got_set == exp_set))
        # 刷新期望分是连续值：用区间断言，既钉住新逻辑又不禁止无害的微调
        for key, label, cmp_fn in (("reroll_expected_max", "E[重掷] <=", lambda g, e: g <= e),
                                   ("reroll_expected_min", "E[重掷] >=", lambda g, e: g >= e)):
            bound = case.get("expect", {}).get(key)
            if bound is not None:
                got_val = result.get("reroll_expected")
                ok_cmp = got_val is not None and cmp_fn(got_val, bound)
                checks.append((label + str(bound), "ok", got_val, ok_cmp))
        inv = check_invariants(result, case["name"])
        for e in inv:
            print("   INVARIANT {} : {}".format(case["name"], e))
        passed = all(c[3] for c in checks) and not inv
        ok += 1 if passed else 0
        print("[{}] {}".format("PASS" if passed else "FAIL", case["name"]))
        for c in checks:
            print("   {} : expect {} got {}".format(c[0], c[1], c[2]))
        if not passed or "-v" in sys.argv:
            for i, it in enumerate(result["items"]):
                print("     slot{} {}: u={:.1f} p={} -> {}  {}".format(
                    i, it["name"], it["utility"], it["price"], it["recommendation"],
                    " / ".join(it["reasons"])))
            exp_val = result.get("reroll_expected")
            print("     reroll={} lock={} buy_set={} 花{} 剩{} E[重掷最好件]={}".format(
                result["reroll"], result["lock"], result.get("buy_set"),
                result.get("spend"), result.get("left"),
                "—" if exp_val is None else "{:.1f}".format(exp_val)))
    print("{}/{} cases passed".format(ok, len(cases)))
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "-v" else None))
