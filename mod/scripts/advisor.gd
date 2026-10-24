extends Reference
# Shop Advisor 决策引擎（GDScript 实现）。
# 与 tools/advisor.py 逐函数同构：改任何一边都必须同步另一边，
# 并用 `python3 tools/evaluate.py`（读 tests/cases.json）回归核心逻辑。
#
# 约定：所有输入是普通 Dictionary/Array（由 state_collector.gd 从游戏中抽取），
# 输出也是普通 Dictionary，方便纯逻辑测试。1 效用 ≈ 1 金币 ≈ 1 点有效生命。
# 无状态：由 shop_advisor.gd new() 一次后当纯函数库使用。


func num(x, d = 0.0) -> float:
	if typeof(x) == TYPE_STRING:
		return float(x) if x.is_valid_float() else float(d)
	if typeof(x) == TYPE_INT or typeof(x) == TYPE_REAL:
		return float(x)
	return float(d)


func getk(obj, key, d = null):
	if obj == null:
		return d
	if typeof(obj) == TYPE_DICTIONARY:
		return obj[key] if obj.has(key) else d
	if typeof(obj) == TYPE_OBJECT:
		var v = obj.get(key)   # Object.get 对不存在的属性返回 null，不会报错
		if v == null:
			return d
		return v
	return d


# ---------- 局面锚点 ----------

func hp_ref(player) -> float:
	return max(50.0, num(getk(getk(player, "stats", {}), "stat_max_hp", 100)))


func danger_scale(player, priors) -> float:
	var table = priors.get("danger_scale", {"base": 1.0, "per_level": 0.04})
	var d := num(getk(player, "danger", 0))
	var f := num(table["base"]) + num(table["per_level"]) * d
	return clamp(f, 0.6, 2.0)


func wave_scale(player, base := 1.0, per_wave := 0.0, lo := 0.0, hi := 1.0e9) -> float:
	var f := base + per_wave * num(getk(player, "wave", 1))
	return max(lo, min(hi, f))


func hp_factor(player) -> float:
	return clamp(hp_ref(player) / 100.0, 0.6, 1.5)


# ---------- 属性效用 ----------

func utility_from_stat_gain(key, value, player, priors) -> float:
	# 属性效用总入口：先按曲线算，再乘 priors.stat_weight_adj 里的相对权重修正。
	# 修正系数来自 tools/calibrate.py 对「物品内部好处 vs 代价」兑换比的回归（见 README）。
	var u := _utility_stat_gain(key, value, player, priors)
	if priors == null or typeof(priors) != TYPE_DICTIONARY:
		return u
	var adj = priors.get("stat_weight_adj")
	if adj == null or typeof(adj) != TYPE_DICTIONARY:
		return u
	var f := num(getk(adj, str(key), 1.0))
	return u * f if f > 0.0 else u


func _utility_stat_gain(key, value, player, priors) -> float:
	var wave := num(getk(player, "wave", 1))
	var s = getk(player, "stats", {})
	var share := num(getk(player, "melee_share", 0.5))
	var ds := danger_scale(player, priors)
	var hf := hp_factor(player)
	var cur := 0.0
	var per := 0.0
	var kept := 0.0

	if key == "stat_max_hp":
		return value * 1.2 * hf * ds * wave_scale(player, 1.0, 0.02, 1.0, 1.4)
	if key == "stat_armor":
		per = num(priors.get("armor_per_point_base", 8.0)) + num(priors.get("armor_per_point_per_wave", 0.4)) * wave
		cur = max(0.0, num(getk(s, "stat_armor", 0)))
		kept = num(priors.get("armor_diminish_base", 10.0)) / (num(priors.get("armor_diminish_base", 10.0)) + cur)
		if value > 0:
			return value * per * hf * ds * kept
		return value * per * hf * ds * max(num(priors.get("armor_neg_kept_min", 0.6)), kept) * num(priors.get("negative_stat_factor", 1.2))
	if key == "stat_dodge":
		per = num(priors.get("dodge_per_point_base", 3.0)) + num(priors.get("dodge_per_point_per_wave", 0.15)) * wave
		cur = max(0.0, num(getk(s, "stat_dodge", 0)))
		kept = 25.0 / (25.0 + cur)
		if value > 0:
			return value * per * hf * ds * kept
		return value * per * hf * ds * kept * num(priors.get("negative_stat_factor", 1.2))
	if key == "stat_speed":
		var di := 80.0 + 12.0 * wave * hf
		cur = num(getk(s, "stat_speed", 0))
		per = 0.01 / (1.0 + cur / 100.0)
		return value * per * di * 0.30 * wave_scale(player, 1.0, -0.01, 0.6, 1.0) * ds
	if key == "stat_lifesteal":
		var di2 := 80.0 + 12.0 * wave * hf
		cur = num(getk(s, "stat_lifesteal", 0))
		return value * 0.01 * di2 * 0.4 * ds / (1.0 + cur / 15.0)
	if key == "stat_hp_regeneration":
		return value * wave_scale(player, 1.4, -0.04, 0.8, 1.4) * ds
	if key == "stat_harvesting":
		cur = max(0.0, num(getk(s, "stat_harvesting", 0)))
		return value * num(priors.get("harvest_per_point", 1.2)) * wave_scale(player, 1.4, -0.05, 0.3, 1.4) * (1.0 + cur / 100.0)
	if key == "stat_luck":
		return value * num(priors.get("luck_per_point", 1.0)) * wave_scale(player, 1.0, -0.02, 0.6, 1.0)
	if key == "stat_engineering":
		return value * (0.8 if wave <= 12 else 0.5)
	if key == "stat_xp_gain":
		return value * 0.4 * max(0.2, 1.1 - 0.05 * wave)
	if key in ["stat_melee_damage", "stat_ranged_damage", "stat_elemental_damage",
			"stat_percent_damage", "stat_damage", "stat_attack_speed", "stat_crit_chance"]:
		var weights = {"stat_melee_damage": share, "stat_ranged_damage": 1.0 - share,
				"stat_elemental_damage": 0.15, "stat_percent_damage": 1.0,
				"stat_damage": 1.0, "stat_attack_speed": 0.45,
				"stat_crit_chance": 0.75}
		var pd := num(getk(s, "stat_percent_damage", 0)) + num(getk(s, "stat_damage", 0))
		var dim_cur := 0.0
		if key == "stat_percent_damage" or key == "stat_damage":
			dim_cur = pd
		elif key == "stat_attack_speed":
			dim_cur = num(getk(s, "stat_attack_speed", 0))
		elif key == "stat_crit_chance":
			dim_cur = num(getk(s, "stat_crit_chance", 0))
		var dim := 100.0 / (100.0 + max(0.0, dim_cur))
		var neg := num(priors.get("negative_stat_factor", 1.2)) if value < 0 else 1.0
		return value * num(priors.get("damage_pct_per_point", 2.0)) * num(weights.get(key, 1.0)) * dim * ds * neg
	if key == "stat_range":
		return value * (0.4 if share >= 0.5 else 0.2)
	if key == "stat_curse":
		return -abs(float(value)) * 1.5
	if key == "stat_all":
		return value * 8.0
	return 0.0


# ---------- 武器 DPS ----------

func weapon_dps(contrib, player) -> float:
	var base := num(getk(contrib, "damage", 10))
	var cd := max(1.0, num(getk(contrib, "cooldown", 13)))
	var scaling = getk(contrib, "scaling", {})
	var stats = getk(player, "stats", {})
	var mult := 1.0
	if typeof(scaling) == TYPE_DICTIONARY:
		for skey in scaling.keys():
			var sshare := num(scaling[skey])
			if skey == "stat_melee_damage":
				mult += num(getk(stats, "stat_melee_damage", 0)) * sshare * 0.01
			elif skey == "stat_ranged_damage":
				mult += num(getk(stats, "stat_ranged_damage", 0)) * sshare * 0.01
			elif skey == "stat_attack_speed":
				mult += num(getk(stats, "stat_attack_speed", 0)) * sshare * 0.005
			elif skey == "stat_lifesteal":
				mult += num(getk(stats, "stat_lifesteal", 0)) * sshare * 0.002
			elif skey == "stat_max_hp":
				mult += num(getk(stats, "stat_max_hp", 100)) * sshare * 0.001
	mult = max(0.2, mult)
	var global_pct := 1.0 + (num(getk(stats, "stat_percent_damage", 0)) + num(getk(stats, "stat_damage", 0))) * 0.01
	var melee_pct := 1.0 + num(getk(stats, "stat_melee_damage", 0)) * 0.01
	var ranged_pct := 1.0 + num(getk(stats, "stat_ranged_damage", 0)) * 0.01
	var wtype = getk(contrib, "type", "melee")
	var dmg := base * mult * global_pct * (melee_pct if wtype == "melee" else ranged_pct)
	var as_mult := max(0.3, 1.0 + num(getk(stats, "stat_attack_speed", 0)) * 0.005)
	var shots := as_mult * 600.0 / cd
	var crit := min(1.0, num(getk(contrib, "crit_chance", 0)) + num(getk(stats, "stat_crit_chance", 0)) * 0.01)
	var crit_dmg := 1.0 + max(0.5, num(getk(contrib, "crit_damage", 1.5)) - 1.0)
	return shots * dmg * ((1.0 - crit) + crit * crit_dmg)


# ---------- 物品效用 ----------

# ---------- 武器职业套装 ----------
# 参考表里每把武器带 sets（职业名，如 WEAPON_CLASS_BLADE），每职业 5 档效果，
# 第 i 档 = 持有 i+1 把该职业武器时的**累计**加成。引擎只用增量，避免与
# RunData 已含属性重复计数（套装算不算进 RunData 属性本机确认不了）。
# set_first_count（凑几把才生效）同样确认不了，默认 2 保守处理，见 priors.json 说明。


func set_first_count(priors) -> int:
	return int(num(priors.get("set_first_count", 2)))


func set_totals(ids, index, first: int) -> Dictionary:
	# 当前这批武器已经吃到的套装累计加成 {stat_x: value}
	var out := {}
	if index == null or ids == null:
		return out
	var table = index.get("_sets", {})
	if typeof(table) != TYPE_DICTIONARY:
		return out
	var counts := {}
	for wid in ids:
		var ref = index.get(str(wid))
		if ref == null:
			continue
		for cls in getk(ref, "sets", []):
			var ck := str(cls)
			counts[ck] = int(counts.get(ck, 0)) + 1
	for cls in counts.keys():
		var entry = table.get(cls)
		if entry == null or typeof(entry) != TYPE_DICTIONARY:
			continue
		var tiers = getk(entry, "tiers", [])
		if typeof(tiers) != TYPE_ARRAY or tiers.size() == 0:
			continue
		var level = int(counts[cls]) - first
		if level < 0:
			continue
		if level >= tiers.size():
			level = tiers.size() - 1
		for e in tiers[level]:
			var key := str(getk(e, "key", ""))
			if key.find("stat_") == 0:
				out[key] = float(out.get(key, 0.0)) + num(getk(e, "value", 0))
	return out


func set_gain(ids, add_ids, index, first: int) -> Dictionary:
	# 再拿 add_ids 这几件后，套装加成多了多少（只看增量）
	var before = set_totals(ids, index, first)
	var merged := []
	if ids != null:
		for x in ids:
			merged.append(x)
	if add_ids != null:
		for y in add_ids:
			merged.append(y)
	var after = set_totals(merged, index, first)
	var out := {}
	for k in after.keys():
		var delta = float(after[k]) - float(before.get(k, 0.0))
		if abs(delta) > 1e-9:
			out[k] = delta
	return out


func set_hint(ids, add_id, index, first: int) -> String:
	# 凑到了哪一套，只给 UI 写理由；没跨过门槛返回空串
	if index == null or add_id == null or String(add_id) == "":
		return ""
	var ref = index.get(str(add_id))
	if ref == null:
		return ""
	var table = index.get("_sets", {})
	if typeof(table) != TYPE_DICTIONARY:
		return ""
	var counts := {}
	if ids != null:
		for wid in ids:
			var r = index.get(str(wid))
			if r == null:
				continue
			for cls in getk(r, "sets", []):
				var ck := str(cls)
				counts[ck] = int(counts.get(ck, 0)) + 1
	var best_name := ""
	var best_count := 0
	for cls in getk(ref, "sets", []):
		var rc := str(cls)
		var n = int(counts.get(rc, 0)) + 1
		if n < first:
			continue
		var entry = table.get(rc)
		if entry == null or typeof(entry) != TYPE_DICTIONARY:
			continue
		var tiers = getk(entry, "tiers", [])
		if typeof(tiers) != TYPE_ARRAY or n - first >= tiers.size():
			continue
		if n > best_count:
			best_count = n
			best_name = str(getk(entry, "name_zh", rc))
	if best_count == 0:
		return ""
	return "凑%s套（%d 件）" % [best_name, best_count]


func score_item(offer, player, priors, index) -> Array:
	# returns [utility, dps_pct, reasons(Array of String), loadout_penalty]
	var ref = null
	if index != null:
		ref = index.get(str(getk(offer, "id", "")))
	var reasons := []
	var stat_u := 0.0
	var special_terms := []
	var bonus := num(priors.get("special_bonus_per_effect", 4.0))

	var effects = getk(offer, "effects")
	if effects == null and ref != null:
		effects = getk(ref, "effects", [])
	if effects == null:
		effects = []

	for e in effects:
		var key = getk(e, "key", "")
		var val := num(getk(e, "value", 0))
		if typeof(key) != TYPE_STRING:
			continue
		if key.find("stat_") == 0:
			stat_u += utility_from_stat_gain(key, val, player, priors)
			continue
		var flat = priors.get("special_flat_keys", {})
		var special_keys = priors.get("special_keys", {})
		if typeof(flat) == TYPE_DICTIONARY and flat.has(key):
			special_terms.append(num(flat[key]))
		elif typeof(special_keys) == TYPE_DICTIONARY and special_keys.has(key):
			special_terms.append(val * num(special_keys[key]))
		elif key == "":
			# 参考表里这类效果没有键名（炮塔/地雷/召唤物），只能给一个统一的小额分
			special_terms.append(bonus)
		else:
			# 未登记的新键：按数值符号给分，负值不再被当成正向收益
			var cap_u := num(priors.get("special_unknown_cap", 3.0))
			var per := num(priors.get("special_unknown_per_point", 0.2))
			special_terms.append(max(-cap_u, min(cap_u, val * per)))
	if effects.size() == 0:
		special_terms = [bonus]

	var kind = getk(offer, "kind")
	if kind == null or kind == "":
		kind = "weapon" if (ref != null and getk(ref, "kind", "") == "weapon") else "item"

	# 套装增量：只有武器会带职业套装
	var first := set_first_count(priors)
	var sgain := {}
	if kind == "weapon":
		sgain = set_gain(getk(player, "weapon_ids", []), [getk(offer, "id", "")], index, first)
		var weight := num(priors.get("set_bonus_weight", 1.0))
		for k in sgain.keys():
			stat_u += utility_from_stat_gain(str(k), float(sgain[k]), player, priors) * weight

	var pos := 0.0
	var neg := 0.0
	for v in special_terms:
		if v > 0:
			pos += v
		else:
			neg += v
	var cap := num(priors.get("special_bonus_cap", 16.0))
	var u := stat_u + min(pos, cap) + neg

	if bool(getk(offer, "cursed", false)) or (ref != null and bool(getk(ref, "cursed", false))):
		u -= num(priors.get("cursed_penalty", 25.0))

	var dps_pct := 0.0
	if kind == "weapon":
		var base = getk(offer, "base")
		if (base == null or (typeof(base) == TYPE_DICTIONARY and base.empty())) and ref != null:
			base = getk(ref, "base")
		if base != null:
			var wtype = getk(base, "type")
			if (wtype == null or wtype == "") and ref != null:
				wtype = getk(ref, "weapon_type", "melee")
			if wtype == null or wtype == "":
				wtype = "melee"
			var merged = {}
			if typeof(base) == TYPE_DICTIONARY:
				merged = base.duplicate(true)
			merged["type"] = wtype
			var dps_gain := weapon_dps(merged, player)
			dps_pct = dps_gain / max(1.0, num(getk(player, "total_dps", 50.0)))
			var share := num(getk(player, "melee_share", 0.5))
			var build_type := "melee" if share >= 0.5 else "ranged"
			if wtype != build_type:
				dps_pct *= num(priors.get("off_type_factor", 0.35))
				reasons.append("非本流派武器")

	var stat_lines := []
	for e in effects:
		var key2 = getk(e, "key", "")
		if typeof(key2) == TYPE_STRING and key2.find("stat_") == 0:
			var gain := utility_from_stat_gain(key2, num(getk(e, "value", 0)), player, priors)
			if abs(gain) >= 6:
				var names = {}
				if index != null and index.has("_stat_names"):
					names = index["_stat_names"]
				stat_lines.append("%s%+d" % [str(names.get(key2, key2)), int(num(getk(e, "value", 0)))])
	if stat_lines.size() > 0:
		reasons.insert(0, PoolStringArray(stat_lines.slice(0, 2)).join("、"))
	var hint := set_hint(getk(player, "weapon_ids", []), getk(offer, "id", ""), index, first)
	if hint != "":
		reasons.append(hint)

	var pen := 0.0
	if kind == "weapon":
		var loadout = getk(player, "weapon_ids", [])
		var limit := num(priors.get("loadout_penalty_start", 5))
		if loadout.size() >= limit:
			pen = (loadout.size() - limit + 1) * num(priors.get("loadout_penalty_per_slot", 12.0))
			reasons.append("负重已满 -%d" % int(pen))
	return [u, dps_pct, reasons, pen]


# ---------- 商店决策 ----------

# 档位池期望的缓存：advisor 由 shop_advisor.gd new() 一次，活得比一局长
var _pool_cache := {}


func ensure_pools(index):
	# 按 "tier|kind" 建物品池倒排，挂在 index["_pools"]（与 tools/advisor.py 同步）
	if index == null:
		return null
	if index.has("_pools"):
		return index["_pools"]
	var pools := {}
	for key in index.keys():
		if String(key).begins_with("_"):
			continue
		var e = index[key]
		if typeof(e) != TYPE_DICTIONARY:
			continue
		var kind = str(getk(e, "kind", ""))
		if kind != "weapon" and kind != "item":
			continue
		var pk = "%d|%s" % [int(num(getk(e, "tier", 0))), kind]
		if not pools.has(pk):
			pools[pk] = []
		pools[pk].append(str(getk(e, "id", "")))
	index["_pools"] = pools
	return pools


func player_fingerprint(player) -> String:
	# 影响打分的玩家字段压成缓存键
	var s = getk(player, "stats", {})
	var wlist = getk(player, "weapon_ids", [])
	var vals = [num(getk(player, "wave", 1)), num(getk(player, "danger", 0)),
			num(getk(player, "melee_share", 0.5)) * 100.0, num(getk(player, "total_dps", 50.0)),
			num(getk(s, "stat_max_hp", 100)), num(getk(s, "stat_armor", 0)),
			num(getk(s, "stat_dodge", 0)), num(getk(s, "stat_speed", 0)),
			num(getk(s, "stat_lifesteal", 0)), num(getk(s, "stat_harvesting", 0)),
			num(getk(s, "stat_percent_damage", 0)) + num(getk(s, "stat_damage", 0)),
			num(getk(s, "stat_attack_speed", 0)), num(getk(s, "stat_crit_chance", 0)),
			float(wlist.size())]
	var strs := PoolStringArray()
	for v in vals:
		strs.append(str(int(float(v))))
	return strs.join("|")


func comb(n: int, k: int) -> float:
	if k < 0 or n < k:
		return 0.0
	var r := 1.0
	for i in range(1, k + 1):
		r = r * (n - i + 1) / i
	return r


func expected_top1(scores: Array, k: int) -> Dictionary:
	# 从 N 件里抽 k 件（不重复），最好那件的期望分：
	# 排序后 E[max] = Σ_j s_j · C(j-1,k-1)/C(N,k)，j = k..N（解析解，无采样噪声）
	var n = scores.size()
	if n == 0:
		return {"ok": false, "value": 0.0}
	if n <= k:
		return {"ok": true, "value": float(scores.max())}
	var ordered = scores.duplicate()
	ordered.sort()
	var total := comb(n, k)
	var acc := 0.0
	for j in range(k, n + 1):
		acc += float(ordered[j - 1]) * comb(j - 1, k - 1) / total
	return {"ok": true, "value": acc}


func expected_reroll_score(player, shop, priors, index, tier) -> Dictionary:
	# 重掷后的期望最高分。新货来自「档位池」，构成只由 reroll_weapon_share 决定，
	# 不能用当前这家店摆了几把武器来推。k = 会被重掷的栏位数（总栏位减去已锁）；
	# 已锁栏位保留，作为期望值的下限。
	var pools = ensure_pools(index)
	if pools == null or pools.size() == 0:
		return {"ok": false, "value": 0.0}
	var offers = getk(shop, "offers", [])
	var n_slots := 4
	if typeof(offers) == TYPE_ARRAY and offers.size() > 0:
		n_slots = offers.size()
	var locked_idx := {}
	var locked = getk(shop, "locked", [])
	if typeof(locked) == TYPE_ARRAY:
		for x in locked:
			var li := int(num(x, -1))
			if li >= 0 and li < n_slots:
				locked_idx[li] = true
	var k := n_slots - locked_idx.size()
	if k <= 0:
		return {"ok": true, "value": 0.0}
	var dps_anchor := num(priors.get("weapon_dps_anchor", 100.0))
	var floor_score := 0.0
	for li in locked_idx.keys():
		if typeof(offers) != TYPE_ARRAY or li >= offers.size():
			continue
		var o = offers[li]
		if o == null:
			continue
		var lr = score_item(o, player, priors, index)
		floor_score = max(floor_score, float(lr[0]) + float(lr[1]) * dps_anchor - float(lr[3]))
	var w_share := num(priors.get("reroll_weapon_share", 0.5))
	w_share = max(0.0, min(1.0, w_share))
	var banned := {}
	var bl = getk(player, "banned_ids", [])
	if typeof(bl) == TYPE_ARRAY:
		for b in bl:
			banned[str(b)] = true
	var fp := player_fingerprint(player)
	var t = int(num(tier))

	var parts := []
	for kind in ["weapon", "item"]:
		var res = _exp_for(kind, t, fp, k, player, priors, index, pools, dps_anchor, floor_score, banned)
		if res["ok"]:
			parts.append([w_share if kind == "weapon" else 1.0 - w_share, res["value"]])
	if parts.size() == 0:
		return {"ok": false, "value": 0.0}
	var total_w := 0.0
	for p in parts:
		total_w += p[0]
	if total_w <= 0.0:
		return {"ok": true, "value": parts[0][1]}
	var acc := 0.0
	for p in parts:
		acc += p[0] * p[1]
	return {"ok": true, "value": acc / total_w}


func _exp_for(kind, tier, fp, k, player, priors, index, pools, dps_anchor, floor_score = 0.0, banned = null) -> Dictionary:
	var ids = pools.get("%d|%s" % [tier, kind], [])
	if ids.size() == 0:
		return {"ok": false, "value": 0.0}
	var banned_key := ""
	if typeof(banned) == TYPE_DICTIONARY:
		var bl := []
		for bk in banned.keys():
			bl.append(str(bk))
		bl.sort()
		banned_key = PoolStringArray(bl).join(",")
	var ck = "%d|%s|%s|%d|%d|%s" % [tier, kind, fp, int(k), int(round(floor_score)), banned_key]
	if _pool_cache.has(ck):
		return {"ok": true, "value": _pool_cache[ck]}
	var scores := []
	for id in ids:
		if typeof(banned) == TYPE_DICTIONARY and banned.has(str(id)):
			continue
		var res = score_item({"id": id, "kind": kind}, player, priors, index)
		scores.append(res[0] + res[1] * dps_anchor - res[3])
	if scores.size() == 0:
		return {"ok": false, "value": 0.0}
	if floor_score > 0.0:
		# 已锁的栏位不会被重掷，等价于每件新货的下限提高 floor_score
		for si in range(scores.size()):
			scores[si] = max(float(scores[si]), floor_score)
	var top = expected_top1(scores, k)
	if top["ok"]:
		# 每次买完东西玩家指纹会变，长会话下不清会一直涨
		if _pool_cache.size() > 512:
			_pool_cache.clear()
		_pool_cache[ck] = top["value"]
	return top


func tier_anchor(tier, priors) -> float:
	var anchors = priors.get("tier_anchors", {"0": 10.0, "1": 16.0, "2": 28.0, "3": 45.0, "4": 65.0})
	return num(anchors.get(str(int(tier)), 25.0))


# ---------- 整店组合选择 ----------

func _r6(v: float) -> float:
	# 与 Python 的 round(x, 6) 对齐：穷举比较时先量化，避免浮点尾差导致选组抖动
	return round(v * 1.0e6) / 1.0e6


func _r1(v: float) -> float:
	return round(v * 10.0) / 10.0


func snapshot_after(player, offer, index = null, priors = null):
	# 把一件东西并进玩家快照，供组合里下一件重估边际价值
	# （两件护甲叠一起时，第二件的每点护甲要按新的护甲总量打折）
	if typeof(player) != TYPE_DICTIONARY or typeof(offer) != TYPE_DICTIONARY:
		return player
	var p2 = player.duplicate()
	var s = getk(player, "stats", {})
	var stats = s.duplicate() if typeof(s) == TYPE_DICTIONARY else {}
	p2["stats"] = stats
	var effects = getk(offer, "effects", [])
	if typeof(effects) != TYPE_ARRAY:
		effects = []
	for e in effects:
		var key = str(getk(e, "key", ""))
		if not key.begins_with("stat_"):
			continue
		var val := num(getk(e, "value", 0))
		if key == "stat_all":
			for sk in stats.keys():
				stats[sk] = num(stats[sk]) + val
		else:
			stats[key] = num(stats.get(key, 0)) + val
	if str(getk(offer, "kind", "")) == "weapon":
		var wlist = getk(player, "weapon_ids", [])
		var nxt = wlist.duplicate() if typeof(wlist) == TYPE_ARRAY else []
		var new_id := str(getk(offer, "id", ""))
		if new_id == "":
			new_id = "cart_weapon"
		# 套装也要并进快照：连拿两把同职业时，第二件才看得见跨档增量
		var p = priors if priors != null else {}
		var gain = set_gain(nxt, [new_id], index, set_first_count(p))
		for gk in gain.keys():
			stats[str(gk)] = num(stats.get(str(gk), 0)) + float(gain[gk])
		nxt.append(new_id)
		p2["weapon_ids"] = nxt
		# 新武器的 DPS 必须计入分母：否则第二把武器仍按买前的 total_dps 折算，
		# 两把同水平武器会被各算一次「+100%」。
		var ref = index.get(new_id) if typeof(index) == TYPE_DICTIONARY else null
		var base = getk(offer, "base", null)
		if base == null and ref != null:
			base = getk(ref, "base", null)
		if typeof(base) == TYPE_DICTIONARY and base.size() > 0:
			var wtype = str(base.get("type", ""))
			if wtype == "":
				wtype = str(getk(ref, "weapon_type", "melee")) if ref != null else "melee"
			var merged = base.duplicate(true)
			merged["type"] = wtype
			var gain_dps := weapon_dps(merged, player)
			var old_total := max(1.0, num(getk(player, "total_dps", 50.0)))
			var old_melee := num(getk(player, "melee_share", 0.5)) * old_total
			var new_total := old_total + gain_dps
			p2["total_dps"] = new_total
			p2["melee_share"] = (old_melee + (gain_dps if wtype == "melee" else 0.0)) / new_total
	return p2


func _cmp_score_desc(a, b) -> bool:
	# 分数降序；同分保持原序（对齐 Python sorted 的稳定性）
	var sa := num(a["score"])
	var sb := num(b["score"])
	if sa == sb:
		return int(a["index"]) < int(b["index"])
	return sa > sb


func _indices_of(cart) -> Array:
	var out := []
	for c in cart:
		out.append(int(c["index"]))
	out.sort()
	return out


func cart_value(cart, player, priors, index, dps_anchor) -> float:
	# 组合价值 = 按独立分从高到低逐件累加边际效用（逐件重算，不直接相加）
	var cur = player
	var total := 0.0
	var ordered = cart.duplicate()
	ordered.sort_custom(self, "_cmp_score_desc")
	for entry in ordered:
		var res = score_item(entry["offer"], cur, priors, index)
		total += num(res[0]) + num(res[1]) * dps_anchor - num(res[3])
		cur = snapshot_after(cur, entry["offer"], index, priors)
	return total


func choose_buy_set(cart_items, budget, player, priors, index, dps_anchor) -> Dictionary:
	# 预算内挑组合价值最高的（穷举 2^n）；同价值选花钱少的，留现金给刷新/下波
	# n 上限 8：商店正常只有 4 栏，8 件已经要 256×8 次打分，GDScript 里会卡
	var n = cart_items.size()
	if n > 8:
		n = 8
	var best_indices := []
	var best_spend := 0.0
	var best_val := 0.0
	var best_size := -1
	var has_best := false
	for mask in range(1 << n):
		var cart := []
		var spend := 0.0
		for b in range(n):
			if mask & (1 << b):
				cart.append(cart_items[b])
				spend += num(cart_items[b]["price"])
		if spend > budget + 1.0e-9:
			continue
		var val := 0.0
		if cart.size() > 0:
			val = _r6(cart_value(cart, player, priors, index, dps_anchor))
		var spend_k := -_r6(spend)
		var take := false
		if not has_best:
			take = true
		elif val > _r6(best_val):
			take = true
		elif val == _r6(best_val):
			if spend_k > -_r6(best_spend):
				take = true
			elif spend_k == -_r6(best_spend) and cart.size() > best_size:
				take = true
		if take:
			has_best = true
			best_indices = _indices_of(cart)
			best_spend = spend
			best_val = val
			best_size = cart.size()
	return {"indices": best_indices, "spend": best_spend, "value": best_val}


func advise_shop(player, shop, priors, index) -> Dictionary:
	# 每件先独立打分，再在金币预算内做一次组合选择——避免出现
	# 「三件都写买、但钱只够买两件」这种自相矛盾的建议
	var anchor := tier_anchor(getk(shop, "tier", 0), priors)
	var k = priors.get("buy_wave_scale", {"base": 1.2, "per_wave": -0.02, "min": 0.8})
	var k_val := max(num(k["min"]), num(k["base"]) + num(k["per_wave"]) * num(getk(player, "wave", 1)))
	var buy_thr := max(anchor, anchor * k_val)
	var maybe_thr := anchor * num(priors.get("maybe_factor", 0.7))
	var gold := num(getk(player, "gold", 0))
	var dps_anchor := num(priors.get("weapon_dps_anchor", 100.0))

	var scored := []
	var offers = getk(shop, "offers", [])
	for i in range(offers.size()):
		var offer = offers[i]
		if offer == null:
			scored.append({"index": i, "name": "#%d" % (i + 1), "score": 0.0, "utility": 0.0,
					"dps_pct": 0.0, "price": 0.0, "reasons": [], "null": true})
			continue
		var res = score_item(offer, player, priors, index)
		var price := max(0.0, num(getk(offer, "price", 0)))
		var score = num(res[0]) + num(res[1]) * dps_anchor - num(res[3])
		var o2 = offer.duplicate() if typeof(offer) == TYPE_DICTIONARY else {}
		var kind = str(getk(offer, "kind", ""))
		o2["kind"] = kind if kind != "" else "item"
		scored.append({"index": i, "name": getk(offer, "name", "#%d" % (i + 1)), "score": score,
				"utility": res[0], "dps_pct": res[1], "price": price, "reasons": res[2],
				"offer": o2, "plain": typeof(offer) == TYPE_DICTIONARY})

	# 组合选择：只有过了「可考虑」线且单件买得起的件参与抢预算
	var cands := []
	for s in scored:
		if bool(getk(s, "null", false)) or not bool(getk(s, "plain", false)):
			continue
		if num(s["price"]) > 0.0 and num(s["score"]) >= maybe_thr and num(s["price"]) <= gold:
			cands.append(s)
	var chosen = choose_buy_set(cands, gold, player, priors, index, dps_anchor)
	var picked := {}
	for ci in chosen["indices"]:
		picked[int(ci)] = true

	var items := []
	for s in scored:
		var reasons = s["reasons"].duplicate()
		var rec := "skip"
		if bool(getk(s, "null", false)):
			rec = "skip"
		elif num(s["price"]) == 0.0:
			rec = "buy"                                  # 白拿的一律拿
		elif picked.has(int(s["index"])):
			rec = "buy" if num(s["score"]) >= buy_thr else "maybe"
		elif num(s["score"]) >= buy_thr:
			rec = "lock"
			if num(s["price"]) > gold:
				reasons.append("买不起，差 %d 金" % int(ceil(num(s["price"]) - gold)))
			else:
				reasons.append("预算让给更值的组合")
		items.append({"name": s["name"], "score": _r1(num(s["score"])), "utility": _r1(num(s["utility"])),
				"dps_pct": _r1(num(s["dps_pct"])), "price": _r1(num(s["price"])),
				"recommendation": rec, "reasons": reasons})

	# 锁定免费：值得留下但本次不买的，建议锁定（已经推荐购买的不必再锁，
	# 免费件虽然不在 buy_set 里、但标签就是 buy，同样不能同时当锁）
	var lock = null
	var best_lock = null
	var rec_by_index := {}
	for i in range(scored.size()):
		rec_by_index[int(scored[i]["index"])] = str(items[i]["recommendation"])
	for s in scored:
		if bool(getk(s, "null", false)) or str(rec_by_index.get(int(s["index"]), "skip")) == "buy":
			continue
		if num(s["score"]) < max(buy_thr, maybe_thr):
			continue
		if best_lock == null or num(s["score"]) > num(best_lock["score"]):
			best_lock = s
	if best_lock != null:
		lock = {"index": int(best_lock["index"]), "name": str(best_lock["name"]),
				"score": _r1(num(best_lock["score"]))}

	# 刷新：用「买完推荐组合后剩下的钱」判断刷不刷得起，
	# 否则会出现「买这三件 + 去刷新」这种矛盾建议
	var reroll_cost := num(getk(shop, "reroll_cost", 0))
	var avail := gold - num(chosen["spend"])
	var best_score := 0.0
	var seen_score := false
	for s in scored:
		if not seen_score or num(s["score"]) > best_score:
			best_score = num(s["score"])
			seen_score = true
	var has_affordable_buy := false
	for x in items:
		if str(x["recommendation"]) == "buy":
			has_affordable_buy = true
	var reroll := "no"
	var exp_best = null			# 只在真正要权衡刷新时才算，调试字段
	var reroll_margin = null
	if reroll_cost == 0:
		reroll = "yes" if not has_affordable_buy else "no"
	elif avail >= reroll_cost and not has_affordable_buy:
		exp_best = anchor * num(priors.get("reroll_expected_factor", 1.5))
		var est = expected_reroll_score(player, shop, priors, index, getk(shop, "tier", 0))
		if est["ok"]:
			exp_best = est["value"]
		var headroom = max(0.0, float(exp_best) - max(best_score, 0.0))
		reroll_margin = headroom - reroll_cost * num(priors.get("reroll_gain_per_cost", 0.8))
		reroll = "yes" if reroll_margin >= 0.0 else "no"

	return {"items": items, "reroll": reroll, "lock": lock, "reroll_expected": exp_best,
			"reroll_margin": reroll_margin, "buy_set": chosen["indices"],
			"spend": _r1(num(chosen["spend"])), "budget": gold, "left": _r1(avail),
			"thresholds": {"buy": _r1(buy_thr), "maybe": _r1(maybe_thr)}}
