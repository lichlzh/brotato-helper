#!/usr/bin/env python3
"""Shop Advisor 决策引擎 —— Python 参考实现。

与 mod/scripts/advisor.gd 逐函数同构：改这里必须同步改 GDScript，
并用 tools/evaluate.py + tests/cases.json 回归。本机（无 Godot）唯一可验证核心逻辑之处。

效用单位：1 效用 ≈ 1 金币 ≈ 1 点有效生命/等效伤害。
标定锚点（Danger 1 视角，其余用 danger_scale 缩放）：
  - 1 点护甲 ≈ 10 + 0.5×波次   （波6 时 金属板(2甲,40金) ≈ 买）
  - 1 点最大生命 ≈ 1.2×HP 系数
  - 1% 全伤害 ≈ 2 效用×属性占比；1 收获 ≈ 1.5×衰减
"""
import json
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def default_priors():
    return load_json(os.path.join(ROOT, "mod", "data", "priors.json"))


def num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# ---------- 局面锚点 ----------

def hp_ref(player):
    return max(50.0, num(player["stats"].get("stat_max_hp", 100)))


def danger_scale(player, priors):
    table = priors.get("danger_scale", {"base": 1.0, "per_level": 0.04})
    d = num(player.get("danger", 0))
    f = table["base"] + table["per_level"] * d
    return max(0.6, min(2.0, f))


def wave_scale(player, base=1.0, per_wave=0.0, lo=0.0, hi=1e9):
    f = base + per_wave * num(player.get("wave", 1))
    return max(lo, min(hi, f))


def hp_factor(player):
    """血条越长，每点护甲/闪避越值钱（相对 100 血）。"""
    return max(0.6, min(1.5, hp_ref(player) / 100.0))


# ---------- 属性效用 ----------

def utility_from_stat_gain(key, value, player, priors):
    """属性效用的总入口：先按曲线算，再乘 priors.stat_weight_adj 里的相对权重修正。

    修正系数来自 tools/calibrate.py 对「物品内部好处 vs 代价」兑换比的回归（详见 README
    「属性权重是怎么标定的」）。默认 1.0 = 不修正。
    """
    u = _utility_stat_gain(key, value, player, priors)
    if not priors:
        return u
    adj = priors.get("stat_weight_adj")
    if not adj:
        return u
    f = num(adj.get(key, 1.0))
    return u * f if f > 0.0 else u


def _utility_stat_gain(key, value, player, priors):
    wave = num(player.get("wave", 1))
    s = player["stats"]
    share = player.get("melee_share", 0.5)
    ds = danger_scale(player, priors)
    hf = hp_factor(player)

    if key == "stat_max_hp":
        return value * 1.2 * hf * ds * wave_scale(player, 1.0, 0.02, 1.0, 1.4)
    if key == "stat_armor":
        per = num(priors.get("armor_per_point_base", 8.0)) + num(priors.get("armor_per_point_per_wave", 0.4)) * wave
        cur = max(0.0, num(s.get("stat_armor", 0)))
        kept = num(priors.get("armor_diminish_base", 10.0)) / (num(priors.get("armor_diminish_base", 10.0)) + cur)
        if value > 0:
            return value * per * hf * ds * kept
        return value * per * hf * ds * max(num(priors.get("armor_neg_kept_min", 0.6)), kept) \
            * num(priors.get("negative_stat_factor", 1.2))
    if key == "stat_dodge":
        per = num(priors.get("dodge_per_point_base", 3.0)) + num(priors.get("dodge_per_point_per_wave", 0.15)) * wave
        cur = max(0.0, num(s.get("stat_dodge", 0)))
        kept = 25.0 / (25.0 + cur)
        if value > 0:
            return value * per * hf * ds * kept
        return value * per * hf * ds * kept * num(priors.get("negative_stat_factor", 1.5))
    if key == "stat_speed":
        di = 80.0 + 12.0 * wave * hf
        cur = num(s.get("stat_speed", 0))
        per = 0.01 / (1.0 + cur / 100.0)
        f = wave_scale(player, 1.0, -0.01, 0.6, 1.0)
        return value * per * di * 0.30 * f * ds
    if key == "stat_lifesteal":
        di = 80.0 + 12.0 * wave * hf
        cur = num(s.get("stat_lifesteal", 0))
        return value * 0.01 * di * 0.4 * ds / (1.0 + cur / 15.0)
    if key == "stat_hp_regeneration":
        f = wave_scale(player, 1.4, -0.04, 0.8, 1.4)
        return value * f * ds
    if key == "stat_harvesting":
        f = wave_scale(player, 1.4, -0.05, 0.3, 1.4)
        cur = max(0.0, num(s.get("stat_harvesting", 0)))
        return value * num(priors.get("harvest_per_point", 1.2)) * f * (1.0 + cur / 100.0)
    if key == "stat_luck":
        f = wave_scale(player, 1.0, -0.02, 0.6, 1.0)
        return value * num(priors.get("luck_per_point", 1.0)) * f
    if key == "stat_engineering":
        return value * (0.8 if wave <= 12 else 0.5)
    if key == "stat_xp_gain":
        return value * 0.4 * max(0.2, 1.1 - 0.05 * wave)
    if key in ("stat_melee_damage", "stat_ranged_damage", "stat_elemental_damage",
               "stat_percent_damage", "stat_damage", "stat_attack_speed", "stat_crit_chance"):
        weights = {"stat_melee_damage": share, "stat_ranged_damage": 1.0 - share,
                   "stat_elemental_damage": 0.15, "stat_percent_damage": 1.0,
                   "stat_damage": 1.0, "stat_attack_speed": 0.45,
                   "stat_crit_chance": 0.75}
        dim_cur = {"stat_percent_damage": num(s.get("stat_percent_damage", 0)) + num(s.get("stat_damage", 0)),
                   "stat_damage": num(s.get("stat_percent_damage", 0)) + num(s.get("stat_damage", 0)),
                   "stat_attack_speed": num(s.get("stat_attack_speed", 0)),
                   "stat_crit_chance": num(s.get("stat_crit_chance", 0))}.get(key, 0.0)
        dim = 100.0 / (100.0 + max(0.0, dim_cur))
        neg = num(priors.get("negative_stat_factor", 1.2)) if value < 0 else 1.0
        return value * num(priors.get("damage_pct_per_point", 2.0)) * weights[key] * dim * ds * neg
    if key == "stat_range":
        return value * (0.4 if share >= 0.5 else 0.2)
    if key == "stat_curse":
        return -abs(value) * 1.5
    if key == "stat_all":
        return value * 8.0
    return 0.0


# ---------- 武器 DPS ----------

def weapon_dps(contrib, player, priors=None):
    base = num(contrib.get("damage", 10))
    cd = max(1.0, num(contrib.get("cooldown", 13)))
    scaling = contrib.get("scaling", {}) or {}
    stats = player["stats"]
    mult = 1.0
    for skey, sshare in scaling.items():
        if skey == "stat_melee_damage":
            mult += num(stats.get("stat_melee_damage", 0)) * num(sshare) * 0.01
        elif skey == "stat_ranged_damage":
            mult += num(stats.get("stat_ranged_damage", 0)) * num(sshare) * 0.01
        elif skey == "stat_attack_speed":
            mult += num(stats.get("stat_attack_speed", 0)) * num(sshare) * 0.005
        elif skey == "stat_lifesteal":
            mult += num(stats.get("stat_lifesteal", 0)) * num(sshare) * 0.002
        elif skey == "stat_max_hp":
            mult += num(stats.get("stat_max_hp", 100)) * num(sshare) * 0.001
    mult = max(0.2, mult)
    global_pct = 1.0 + (num(stats.get("stat_percent_damage", 0))
                        + num(stats.get("stat_damage", 0))) * 0.01
    melee_pct = 1.0 + num(stats.get("stat_melee_damage", 0)) * 0.01
    ranged_pct = 1.0 + num(stats.get("stat_ranged_damage", 0)) * 0.01
    wtype = contrib.get("type", "melee")
    dmg = base * mult * global_pct * (melee_pct if wtype == "melee" else ranged_pct)
    as_mult = max(0.3, 1.0 + num(stats.get("stat_attack_speed", 0)) * 0.005)
    shots = as_mult * 600.0 / cd
    crit = min(1.0, num(contrib.get("crit_chance", 0))
               + num(stats.get("stat_crit_chance", 0)) * 0.01)
    crit_dmg = 1.0 + max(0.5, num(contrib.get("crit_damage", 1.5)) - 1.0)
    return shots * dmg * ((1.0 - crit) + crit * crit_dmg)


def build_index(refdata):
    by_id = {}
    for group in ("weapons", "items", "characters"):
        for e in refdata.get(group, []):
            by_id[e["id"]] = e
    by_id["_sets"] = refdata.get("sets", {})
    return by_id


# ---------- 武器职业套装 ----------
# 参考表里每把武器带 sets（职业名，如 WEAPON_CLASS_BLADE），每职业 5 档效果，
# 第 i 档 = 持有 i+1 把该职业武器时的**累计**加成。
#
# 引擎只用「买了这件之后累计值的增量」：游戏把套装加成算进 RunData 属性没有、
# 算进哪一项本机无法确认，只取增量就不会和已含的属性重复计数。
# set_first_count（凑几把才开始生效）本机也确认不了：wiki 抓取失败、数据文件里没有
# 门槛文案，游戏里只看到「1 把就算」还是「2 把才算」两种可能。默认 2（保守，
# 不会怂恿把第一把同类武器当成套装件买），进游戏看一眼套装提示即可确认。

def set_first_count(priors):
    return int(num(priors.get("set_first_count", 2)))


def set_totals(ids, index, first):
    """当前这批武器已经吃到的套装累计加成 {stat_key: value}"""
    table = (index or {}).get("_sets", {})
    counts = {}
    for wid in ids or []:
        ref = (index or {}).get(wid)
        if not ref:
            continue
        for cls in ref.get("sets", []):
            counts[cls] = counts.get(cls, 0) + 1
    out = {}
    for cls, c in counts.items():
        entry = table.get(cls)
        if not entry:
            continue
        tiers = entry.get("tiers", [])
        if not tiers:
            continue
        level = c - first
        if level < 0:
            continue
        if level >= len(tiers):
            level = len(tiers) - 1
        for e in tiers[level]:
            key = e.get("key", "")
            if key.startswith("stat_"):
                out[key] = out.get(key, 0.0) + num(e.get("value", 0))
    return out


def set_gain(ids, add_ids, index, first):
    """再拿 add_ids 这几件后，套装加成多了多少（只看增量）"""
    before = set_totals(ids, index, first)
    after = set_totals(list(ids or []) + list(add_ids or []), index, first)
    out = {}
    for k, v in after.items():
        delta = v - before.get(k, 0.0)
        if delta:
            out[k] = delta
    return out


def set_hint(ids, add_id, index, first):
    """凑到了哪一套，给 UI 写理由用；没跨过门槛就返回空串"""
    if not add_id:
        return ""
    table = (index or {}).get("_sets", {})
    ref = (index or {}).get(add_id)
    if not ref:
        return ""
    counts = {}
    for wid in ids or []:
        r = (index or {}).get(wid)
        if not r:
            continue
        for cls in r.get("sets", []):
            counts[cls] = counts.get(cls, 0) + 1
    best = None
    for cls in ref.get("sets", []):
        n = counts.get(cls, 0) + 1
        if n < first:
            continue
        entry = table.get(cls) or {}
        tiers = entry.get("tiers", [])
        if not tiers or n - first >= len(tiers):
            continue
        if best is None or n > best[1]:
            best = (entry.get("name_zh") or cls, n)
    if best is None:
        return ""
    return "凑%s套（%d 件）" % (best[0], best[1])


# ---------- 档位池期望 ----------

_POOL_CACHE = {}


def reset_pool_cache():
    _POOL_CACHE.clear()


def _ensure_pools(index):
    """按 "tier|kind" 建物品池倒排，挂在 index 上（与 advisor.gd 的 index["_pools"] 同步）。"""
    if index is None:
        return None
    pools = index.get("_pools")
    if pools is None:
        pools = {}
        for key, e in index.items():
            if key.startswith("_") or not isinstance(e, dict):
                continue
            kind = e.get("kind")
            if kind not in ("weapon", "item"):
                continue
            pools.setdefault("{}|{}".format(int(num(e.get("tier", 0))), kind), []).append(e["id"])
        index["_pools"] = pools
    return pools


def player_fingerprint(player):
    """影响打分的玩家字段压成一个字符串，用作池子期望的缓存键。"""
    s = player["stats"]
    vals = [num(player.get("wave", 1)), num(player.get("danger", 0)),
            num(player.get("melee_share", 0.5)) * 100.0, num(player.get("total_dps", 50.0)),
            num(s.get("stat_max_hp", 100)), num(s.get("stat_armor", 0)),
            num(s.get("stat_dodge", 0)), num(s.get("stat_speed", 0)),
            num(s.get("stat_lifesteal", 0)), num(s.get("stat_harvesting", 0)),
            num(s.get("stat_percent_damage", 0)) + num(s.get("stat_damage", 0)),
            num(s.get("stat_attack_speed", 0)), num(s.get("stat_crit_chance", 0)),
            len(player.get("weapon_ids", []))]
    return "|".join(str(int(v)) for v in vals)


def comb(n, k):
    if k < 0 or n < k:
        return 0.0
    r = 1.0
    for i in range(1, int(k) + 1):
        r = r * (n - i + 1) / i
    return r


def expected_top1(scores, k):
    """从 N 件里抽 k 件（不重复），最好那件的期望分。
    排序后 E[max] = Σ_j s_j · C(j-1, k-1) / C(N, k)，j = k..N（解析解，无采样噪声）。
    返回 {"ok": bool, "value": float}，与 advisor.gd 同形。"""
    n = len(scores)
    if n == 0:
        return {"ok": False, "value": 0.0}
    if n <= k:
        return {"ok": True, "value": float(max(scores))}
    ordered = sorted(scores)
    total = comb(n, k)
    acc = 0.0
    for j in range(k, n + 1):
        acc += ordered[j - 1] * comb(j - 1, k - 1) / total
    return {"ok": True, "value": acc}


def _exp_for(kind, tier, fp, k, player, priors, index, pools, dps_anchor, floor=0.0, banned=None):
    ids = pools.get("{}|{}".format(tier, kind), [])
    if not ids:
        return {"ok": False, "value": 0.0}
    banned_key = ",".join(sorted(banned)) if banned else ""
    ck = "{}|{}|{}|{}|{}|{}".format(tier, kind, fp, int(k), int(round(floor)), banned_key)
    if ck in _POOL_CACHE:
        return {"ok": True, "value": _POOL_CACHE[ck]}
    scores = []
    for i in ids:
        if banned and i in banned:
            continue
        u, dps_pct, _reasons, pen = score_item({"id": i, "kind": kind}, player, priors, index)
        scores.append(u + dps_pct * dps_anchor - pen)
    if not scores:
        return {"ok": False, "value": 0.0}
    if floor > 0.0:
        # 已锁的栏位不会被重掷，等价于每件新货的下限提高 floor
        scores = [max(s, floor) for s in scores]
    top = expected_top1(scores, k)
    if top["ok"]:
        # 每次买完东西玩家指纹会变，长会话下不清会一直涨
        if len(_POOL_CACHE) > 512:
            _POOL_CACHE.clear()
        _POOL_CACHE[ck] = top["value"]
    return top


def expected_reroll_score(player, shop, priors, index, tier):
    """重掷后的期望最高分。

    新货来自「档位池」，构成只由先验 reroll_weapon_share 决定——不能用当前这家店
    摆了几把武器来推，否则同一局面会因为展示不同得出不同的刷新建议。
    k 是会被重掷的栏位数（总栏位减去已锁），已锁栏位保留，作为期望值的下限。
    池子拿不到返回 ok=False，调用方退回 tier_anchors 兜底。"""
    pools = _ensure_pools(index)
    if not pools:
        return {"ok": False, "value": 0.0}
    offers = shop.get("offers") or []
    n_slots = len(offers) if offers else 4
    locked_idx = set()
    for x in (shop.get("locked") or []):
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if 0 <= i < n_slots:
            locked_idx.add(i)
    k = n_slots - len(locked_idx)
    if k <= 0:
        return {"ok": True, "value": 0.0}
    dps_anchor = num(priors.get("weapon_dps_anchor", 100.0))
    floor = 0.0
    for i in locked_idx:
        o = offers[i] if i < len(offers) else None
        if not o:
            continue
        u, dps_pct, _reasons, pen = score_item(o, player, priors, index)
        floor = max(floor, u + dps_pct * dps_anchor - pen)
    w_share = max(0.0, min(1.0, num(priors.get("reroll_weapon_share", 0.5))))
    banned = set(str(x) for x in (player.get("banned_ids") or []))
    fp = player_fingerprint(player)
    t = int(num(tier))

    parts = []
    for kind in ("weapon", "item"):
        res = _exp_for(kind, t, fp, k, player, priors, index, pools, dps_anchor, floor, banned)
        if res["ok"]:
            parts.append((w_share if kind == "weapon" else 1.0 - w_share, res["value"]))
    if not parts:
        return {"ok": False, "value": 0.0}
    total_w = sum(p for p, _ in parts)
    if total_w <= 0.0:
        return {"ok": True, "value": parts[0][1]}
    return {"ok": True, "value": sum(p * v for p, v in parts) / total_w}



# ---------- 物品效用 ----------

def score_item(offer, player, priors, index=None):
    """返回 (utility, reasons)。"""
    ref = index.get(offer.get("id", "")) if index else None
    reasons = []
    stat_u = 0.0
    special_terms = []
    bonus = num(priors.get("special_bonus_per_effect", 4.0))

    effects = offer.get("effects")
    if effects is None and ref is not None:
        effects = ref.get("effects", [])
    effects = effects or []
    kind = offer.get("kind") or ("weapon" if ref and ref["kind"] == "weapon" else "item")

    for e in effects:
        key = e.get("key", "")
        val = num(e.get("value", 0))
        if key.startswith("stat_"):
            stat_u += utility_from_stat_gain(key, val, player, priors)
            continue
        flat = priors.get("special_flat_keys", {})
        weights = priors.get("special_keys", {})
        if key in flat:
            special_terms.append(num(flat[key]))
        elif key in weights:
            special_terms.append(val * num(weights[key]))
        elif key == "":
            # 参考表里这类效果没有键名（炮塔/地雷/召唤物），只能给一个统一的小额分
            special_terms.append(bonus)
        else:
            # 未登记的新键：按数值符号给分，负值不再被当成正向收益
            cap_u = num(priors.get("special_unknown_cap", 3.0))
            per = num(priors.get("special_unknown_per_point", 0.2))
            special_terms.append(max(-cap_u, min(cap_u, val * per)))
    if not effects:
        special_terms = [bonus]

    # 套装增量：只有武器会带来职业套装
    first = set_first_count(priors)
    sgain = set_gain(player.get("weapon_ids", []), [offer.get("id", "")], index, first) \
        if kind == "weapon" else {}
    if sgain:
        weight = num(priors.get("set_bonus_weight", 1.0))
        for key, val in sgain.items():
            stat_u += utility_from_stat_gain(key, val, player, priors) * weight

    pos = sum(v for v in special_terms if v > 0)
    neg = sum(v for v in special_terms if v <= 0)
    cap = num(priors.get("special_bonus_cap", 16.0))
    u = stat_u + min(pos, cap) + neg
    special_neg = neg

    if offer.get("cursed") or (ref and ref.get("cursed")):
        u -= num(priors.get("cursed_penalty", 25.0))

    dps_pct = 0.0
    if kind == "weapon":
        base = offer.get("base") or (ref.get("base") if ref else None) or {}
        if base:
            wtype = base.get("type")
            if not wtype:
                wtype = ref.get("weapon_type", "melee") if ref else "melee"
            dps_gain = weapon_dps(dict(base, type=wtype), player, priors)
            dps_pct = dps_gain / max(1.0, num(player.get("total_dps", 50.0)))
            share = player.get("melee_share", 0.5)
            build_type = "melee" if share >= 0.5 else "ranged"
            if wtype != build_type:
                dps_pct *= num(priors.get("off_type_factor", 0.35))
                reasons.append("非本流派武器")
        pass  # 负重惩罚在 advise_shop 的最终分数中处理

    stat_lines = []
    for e in effects:
        key = e.get("key", "")
        if key.startswith("stat_"):
            gain = utility_from_stat_gain(key, num(e.get("value", 0)), player, priors)
            if abs(gain) >= 6:
                names = (index or {}).get("_stat_names", {})
                stat_lines.append("{}{:+.0f}".format(names.get(key, key), num(e.get("value", 0))))
    if stat_lines:
        reasons = ["、".join(stat_lines[:3])] + reasons
    hint = set_hint(player.get("weapon_ids", []), offer.get("id", ""), index, first)
    if hint:
        reasons.append(hint)
    loadout = len(player.get("weapon_ids", []))
    limit = num(priors.get("loadout_penalty_start", 5))
    if kind == "weapon" and loadout >= limit:
        pen = (loadout - limit + 1) * num(priors.get("loadout_penalty_per_slot", 12.0))
        reasons.append("负重已满 -{:.0f}".format(pen))
    else:
        pen = 0.0
    return u, dps_pct, reasons, pen


# ---------- 商店决策 ----------

def tier_anchor(tier, priors):
    anchors = priors.get("tier_anchors", {"0": 15.0, "1": 25.0, "2": 40.0, "3": 60.0, "4": 85.0})
    return num(anchors.get(str(int(tier)), 25.0))


def _snapshot_after(player, offer, index=None, priors=None):
    """把一件的东西并进玩家快照，供组合内下一件重新估边际价值。
    （两件护甲叠在一起时，第二件的每点护甲要按新的护甲总量打折）"""
    p2 = dict(player)
    p2["stats"] = dict(player["stats"])
    for e in (offer.get("effects") or []):
        key = e.get("key", "")
        if not key.startswith("stat_"):
            continue
        val = num(e.get("value", 0))
        if key == "stat_all":
            for k in list(p2["stats"].keys()):
                p2["stats"][k] = num(p2["stats"][k]) + val
        else:
            p2["stats"][key] = num(p2["stats"].get(key, 0)) + val
    if (offer.get("kind") or "") == "weapon":
        ids = list(player.get("weapon_ids", []))
        new_id = offer.get("id") or "cart_weapon"
        # 套装也要并进快照：连拿两把同职业时，第二件才看得见跨档增量
        for key, val in set_gain(ids, [new_id], index, set_first_count(priors or {})).items():
            p2["stats"][key] = num(p2["stats"].get(key, 0)) + val
        p2["weapon_ids"] = ids + [new_id]
        # 新武器的 DPS 必须计入分母：否则第二把武器仍按买前的 total_dps 折算，
        # 两把同水平武器会被各算一次「+100%」。
        ref = (index or {}).get(new_id)
        base = offer.get("base") or (ref.get("base") if ref else None) or {}
        if base:
            wtype = base.get("type") or (ref.get("weapon_type", "melee") if ref else "melee")
            gain_dps = weapon_dps(dict(base, type=wtype), player, priors)
            old_total = max(1.0, num(player.get("total_dps", 50.0)))
            old_melee = num(player.get("melee_share", 0.5)) * old_total
            new_total = old_total + gain_dps
            p2["total_dps"] = new_total
            p2["melee_share"] = (old_melee + (gain_dps if wtype == "melee" else 0.0)) / new_total
    return p2


def cart_value(cart, player, priors, index, dps_anchor):
    """组合价值 = 按独立分从高到低逐件累加边际效用。
    逐件重算而不是直接相加：同买两件护甲时第二件实际更不值。"""
    cur = player
    total = 0.0
    for entry in sorted(cart, key=lambda e: -e["score"]):
        u, dps_pct, _reasons, pen = score_item(entry["offer"], cur, priors, index)
        total += u + dps_pct * dps_anchor - pen
        cur = _snapshot_after(cur, entry["offer"], index, priors)
    return total


def choose_buy_set(cart_items, budget, player, priors, index, dps_anchor):
    """预算内挑组合价值最高的（穷举 2^n）。
    cart_items: [{"index","score","price","offer"}]，只放过了门槛的件。
    同价值时选花钱少的：留现金给刷新/下波。
    n 上限 8：商店正常只有 4 栏，8 件已经要 256×8 次打分，GDScript 里会卡。"""
    n = min(len(cart_items), 8)
    best_key = None
    best_cart = []
    best_spend = 0.0
    for mask in range(1 << n):
        cart = [cart_items[b] for b in range(n) if mask & (1 << b)]
        spend = sum(c["price"] for c in cart)
        if spend > budget + 1e-9:
            continue
        val = cart_value(cart, player, priors, index, dps_anchor) if cart else 0.0
        key = (round(val, 6), -round(spend, 6), len(cart))
        if best_key is None or key > best_key:
            best_key = key
            best_cart = sorted(c["index"] for c in cart)
            best_spend = spend
    return {"indices": best_cart, "spend": best_spend,
            "value": best_key[0] if best_key else 0.0}


def advise_shop(player, shop, priors, index=None):
    """档位锚点法：物品分数与「本档位标准件」比较，避免绝对金钱标定。
    每件先独立打分，再在金币预算内做一次组合选择——避免出现「三件都写买、
    但钱只够买两件」这种自相矛盾的建议。"""
    anchor = tier_anchor(shop.get("tier", 0), priors)
    k = priors["buy_wave_scale"]
    k_val = max(k["min"], k["base"] + k["per_wave"] * num(player.get("wave", 1)))
    buy_thr = max(anchor, anchor * k_val)
    maybe_thr = anchor * num(priors.get("maybe_factor", 0.6))
    gold = num(player.get("gold", 0))
    dps_anchor = num(priors.get("weapon_dps_anchor", 100.0))

    scored = []
    for i, offer in enumerate(shop.get("offers", [])):
        if offer is None:
            scored.append({"index": i, "name": "#%d" % (i + 1), "score": 0.0, "utility": 0.0,
                           "dps_pct": 0.0, "price": 0.0, "reasons": [], "null": True})
            continue
        u, dps_pct, reasons, pen = score_item(offer, player, priors, index)
        price = max(0.0, num(offer.get("price", 0)))
        score = u + dps_pct * dps_anchor - pen
        scored.append({"index": i, "name": offer.get("name", "#%d" % (i + 1)), "score": score,
                       "utility": u, "dps_pct": dps_pct, "price": price, "reasons": reasons,
                       "offer": dict(offer, kind=offer.get("kind") or "item")})

    # 组合选择：只有过「可考虑」线且买得起单件的件参与抢预算
    cands = [s for s in scored if not s.get("null") and s["price"] > 0
             and s["score"] >= maybe_thr and s["price"] <= gold]
    chosen = choose_buy_set(cands, gold, player, priors, index, dps_anchor)
    picked = set(chosen["indices"])

    items = []
    for s in scored:
        reasons = list(s["reasons"])
        if s.get("null"):
            rec = "skip"
        elif s["price"] == 0:
            rec = "buy"                      # 白拿的一律拿
        elif s["index"] in picked:
            rec = "buy" if s["score"] >= buy_thr else "maybe"
        elif s["score"] >= buy_thr:
            rec = "lock"
            if s["price"] > gold:
                reasons.append("买不起，差 {:.0f} 金".format(s["price"] - gold))
            else:
                reasons.append("预算让给更值的组合")
        else:
            rec = "skip"
        items.append({"name": s["name"], "score": round(s["score"], 1),
                      "utility": round(s["utility"], 1), "dps_pct": round(s["dps_pct"], 1),
                      "price": round(s["price"], 1), "recommendation": rec, "reasons": reasons})

    # 锁定免费：值得留下但本次不买的，建议锁定（已经推荐购买的不必再锁，
    # 免费件虽然不在 buy_set 里、但标签就是 buy，同样不能同时当锁）
    lock = None
    best_lock = None
    rec_by_index = {s["index"]: items[i]["recommendation"] for i, s in enumerate(scored)}
    for s in scored:
        if s.get("null") or rec_by_index.get(s["index"]) == "buy":
            continue
        if s["score"] < max(buy_thr, maybe_thr):
            continue
        if best_lock is None or s["score"] > best_lock["score"]:
            best_lock = s
    if best_lock is not None:
        lock = {"index": best_lock["index"], "name": best_lock["name"],
                "score": round(best_lock["score"], 1)}

    # 刷新：有买得起的 BUY 就不刷；否则看期望提升是否值回票价
    # 用「买完推荐组合后剩下的钱」判断刷不刷得起，否则会出现「买这三件 + 去刷新」的矛盾建议
    reroll_cost = num(shop.get("reroll_cost", 0))
    avail = gold - chosen["spend"]
    best_score = max([s["score"] for s in scored], default=0.0)
    has_affordable_buy = any(x["recommendation"] == "buy" for x in items)
    exp_best = None
    reroll_margin = None
    if reroll_cost == 0:
        reroll = "yes" if not has_affordable_buy else "no"
    elif avail >= reroll_cost and not has_affordable_buy:
        exp_best = anchor * num(priors.get("reroll_expected_factor", 1.5))
        est = expected_reroll_score(player, shop, priors, index, shop.get("tier", 0))
        if est["ok"]:
            exp_best = est["value"]
        headroom = max(0.0, exp_best - max(best_score, 0.0))
        gate = reroll_cost * num(priors.get("reroll_gain_per_cost", 0.8))
        reroll_margin = headroom - gate
        reroll = "yes" if reroll_margin >= 0 else "no"
    else:
        reroll = "no"

    return {"items": items, "reroll": reroll, "lock": lock, "reroll_expected": exp_best,
            "reroll_margin": reroll_margin, "buy_set": chosen["indices"],
            "spend": round(chosen["spend"], 1), "budget": gold, "left": round(avail, 1),
            "thresholds": {"buy": round(buy_thr, 1), "maybe": round(maybe_thr, 1)}}


# ---------- CLI ----------

def main():
    import sys
    priors = default_priors()
    refdata = load_json(os.path.join(ROOT, "mod", "data", "brotato_data.json"))
    index = build_index(refdata)
    index["_stat_names"] = {k.lower(): v for k, v in refdata.get("stat_names", {}).items()}
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "tests", "cases.json")
    cases = load_json(path)
    ok = 0
    for case in cases:
        result = advise_shop(case["player"], case["shop"], priors, index)
        passed = True
        for exp in case.get("expect", {}).get("items", []):
            if result["items"][exp["index"]]["recommendation"] != exp["recommendation"]:
                passed = False
        print("[{}] {}".format("PASS" if passed else "FAIL", case["name"]))
        ok += 1 if passed else 0
    print("{}/{} passed".format(ok, len(cases)))
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
