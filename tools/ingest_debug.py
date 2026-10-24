#!/usr/bin/env python3
"""把游戏里 debug_dump 落盘的 shopadvisor_debug_w*.json 转成回归用例。

用法：
    python3 tools/ingest_debug.py game_dumps/*.json
    python3 tools/ingest_debug.py --selftest

产出：
  tests/cases_from_game.json        候选用例（不直接并进 cases.json：里面的期望标签是
                                    当前引擎算出来的，属于「先中靶再调靶」，要人核对过才值得当断言用）
  tests/inventory_from_game.json    合并后的游戏字段清单，并按关键字挑出 #8（天赋/怪物/ban）可能的入口
终端还会列出 probes 里出现「没找到 / 兜底」的字段——那些是本机没法验证、只能靠真实数据确认的读取途径。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from advisor import advise_shop, build_index, set_totals  # noqa: E402
from evaluate import PRIORS, enrich_player, margin_of  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "cases_from_game.json")
INV_OUT = os.path.join(ROOT, "tests", "inventory_from_game.json")
SUSPICIOUS = ("没找到", "兜底")
# #8（怪物/天赋/ban）要找的字段关键字：真实字段名只能从游戏里带回来
INTEREST = ("talent", "monster", "wave", "ban", "crystal", "essence", "reroll",
            "lock", "character", "difficult", "shop", "item")


def load_index():
    with open(os.path.join(ROOT, "mod", "data", "brotato_data.json"), encoding="utf-8") as f:
        refdata = json.load(f)
    index = build_index(refdata)
    index["_stat_names"] = {k.lower(): v for k, v in refdata.get("stat_names", {}).items()}
    return index


def case_from_dump(dump, index):
    player = dict(dump["player"])
    shop = json.loads(json.dumps(dump["shop"]))  # 深拷贝，别让引擎改到原数据
    if "total_dps" not in player or not player.get("total_dps"):
        enrich_player(player, index)
    result = advise_shop(player, shop, PRIORS, index)
    expect = {
        "items": [{"index": i,
                   "recommendation": it["recommendation"],
                   "margin": round(margin_of(result, i), 1)}
                  for i, it in enumerate(result["items"])],
        "reroll": result["reroll"],
    }
    if result.get("lock") is not None:
        expect["lock"] = result["lock"]["index"]
    if result.get("buy_set"):
        expect["buy_set"] = list(result["buy_set"])
    wave = int(player.get("wave", 0))
    char = player.get("character_id", "?")
    name = "g%d 波%d %s 金%d（自动导出，标签未经人工确认）" % (
        wave, wave, char, int(player.get("gold", 0)))
    return {"name": name, "player": player, "shop": shop, "expect": expect,
            "note": "由 tools/ingest_debug.py 从游戏诊断文件生成。expect 是当前引擎的输出，"
                    "核对合理后再抄进 tests/cases.json。"}, result


def suspicious_probes(dump):
    out = []
    for key, val in (dump.get("probes") or {}).items():
        if any(s in str(val) for s in SUSPICIOUS):
            out.append("%s: %s" % (key, val))
    return out


def selftest():
    """本机拿不到真实诊断文件，所以用已有用例伪造一份，确认「转出来的用例
    和源头用例断言一致」——否则这工具会把人带偏到一批莫名其妙的期望上。"""
    index = load_index()
    with open(os.path.join(ROOT, "tests", "cases.json"), encoding="utf-8") as f:
        cases = json.load(f)
    bad = 0
    for case in cases[:6]:
        dump = {"game_version": "selftest", "index_size": len(index), "engine": "fake",
                "probes": {"stat_read": "get_effect_value"},
                "player": dict(case["player"]), "shop": json.loads(json.dumps(case["shop"]))}
        case_gen, result = case_from_dump(dump, index)
        for exp in case["expect"].get("items", []):
            got = next((g for g in case_gen["expect"]["items"] if g["index"] == exp["index"]), None)
            same = got and got["recommendation"] == exp["recommendation"] \
                and abs(got["margin"] - exp.get("margin", got["margin"])) < 0.2
            if not same:
                bad += 1
                print("  不一致 %s slot%d: 期望 %s/%s，导出 %s" % (
                    case["name"], exp["index"], exp["recommendation"], exp.get("margin"), got))
        if result["reroll"] != case["expect"].get("reroll", result["reroll"]):
            bad += 1
            print("  不一致 %s reroll: 期望 %s，导出 %s" % (case["name"], case["expect"].get("reroll"), result["reroll"]))
    print(("FAIL selftest: %d 处不一致" % bad) if bad else "OK ingest 自检：%d 个局面一致" % min(len(cases), 6))
    return 1 if bad else 0


def set_check(player, index):
    """打印这份真实局面里的职业构成与两种门槛下的套装累计值。
    set_first_count 是本机唯一定不下来的套装参数：拿 dump 的面板属性一比，
    用户在游戏中看一眼套装提示就能确认（详见 README 第 4 节）。"""
    ids = player.get("weapon_ids") or []
    if not ids:
        return
    table = index.get("_sets") or {}
    names = index.get("_stat_names") or {}
    counts = {}
    for wid in ids:
        for cls in (index.get(wid) or {}).get("sets", []):
            counts[cls] = counts.get(cls, 0) + 1
    t1 = set_totals(ids, index, 1)
    t2 = set_totals(ids, index, 2)

    def fmt(total):
        return "+".join("%g %s" % (v, names.get(k, k)) for k, v in sorted(total.items())) or "无"

    print("  职业构成：%s ｜ 套装累计：1件起效 %s ／ 2件起效 %s" % (
        "、".join("%s×%d" % (table.get(cls, {}).get("name_zh", cls), c)
                  for cls, c in sorted(counts.items(), key=lambda kv: -kv[1])),
        fmt(t1), fmt(t2)))


def inventory_report(invs):
    """合并多份诊断文件里的字段清单，按关键字挑出 #8 可能用得上的字段名。
    这一步是「把猜变成查表」：游戏里到底有没有天赋/怪物/ban 的入口，看一眼就知道。"""
    if not invs:
        print("\n（诊断文件里没有 inventory 字段——那份文件是关着 debug 生成的，"
              "或 mod 版本还没有字段清单）")
        return
    merged = {}
    for inv in invs:
        for name, info in (inv.get("objects") or {}).items():
            merged.setdefault(name, info)
    with open(INV_OUT, "w", encoding="utf-8") as f:
        json.dump({"root_children": (invs[0].get("root_children") or {}),
                   "objects": merged, "hits": (invs[0].get("hits") or {})},
                  f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("\n字段清单已合并到 %s" % os.path.relpath(INV_OUT, ROOT))
    kids = sorted((invs[0].get("root_children") or {}).keys())
    print("/root 下的单例与场景：%s" % ("、".join(kids) if kids else "(空)"))
    for name, info in sorted(merged.items()):
        if not isinstance(info, dict) or info.get("missing"):
            continue
        hits = []
        label = {"props": "属性", "methods": "方法", "signals": "信号"}
        for bucket in ("props", "methods", "signals"):
            vals = info.get(bucket)
            if not isinstance(vals, list):
                continue
            for v in vals:
                low = str(v).lower()
                if any(k in low for k in INTEREST):
                    hits.append("%s %s" % (label[bucket], v))
        script = info.get("script", "?")
        print("  %-12s %s" % (name, script))
        if hits:
            print("      可能是 #8 的入口：%s" % "、".join(sorted(set(hits))[:24]))


def main():
    if "--selftest" in sys.argv:
        return selftest()
    paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 1
    index = load_index()
    cases, flagged, invs = [], [], []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            dump = json.load(f)
        if "player" not in dump or "shop" not in dump:
            print("跳过（不像诊断文件）:", p)
            continue
        if dump.get("inventory"):
            invs.append(dump["inventory"])
        case, result = case_from_dump(dump, index)
        cases.append(case)
        bad = suspicious_probes(dump)
        env = "game=%s idx=%s godot=%s" % (dump.get("game_version", "?"),
                                           dump.get("index_size", "?"), dump.get("engine", "?"))
        marks = " ".join(bad) if bad else "读取途径全部命中"
        recs = "/".join(it["recommendation"][:2] for it in result["items"])
        print("%-46s %s  %s  [%s] reroll=%s" % (os.path.basename(p), env, marks, recs, result["reroll"]))
        if bad:
            flagged.append((p, bad))
        set_check(dump["player"], index)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("写入 %s（%d 个局面）" % (os.path.relpath(OUT, ROOT), len(cases)))
    inventory_report(invs)
    if flagged:
        print("\n需要改代码确认的字段：")
        for p, bad in flagged:
            for b in bad:
                print("  %s: %s" % (os.path.basename(p), b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
