extends Reference
# 从游戏运行时（RunData / ItemService / ShopItem）收集引擎需要的纯数据。
# 全部访问都是防御式的：任何字段取不到就退回默认值，游戏版本变动只会让
# 建议变粗糙，不会崩游戏。
#
# 产出结构与 tools/advisor.py 的测试输入完全一致：
#   player: {wave, gold, danger, stats{stat_x: float}, weapon_ids[], total_dps, melee_share}
#   shop:   {tier, reroll_cost, offers[{id, name, kind, price, tier, cursed, effects[], base{}}]}


# 记录「哪个字段是靠哪条途径读到的」，debug_dump 打开时随诊断文件落盘：
# 本机没有游戏，这些途径全是按常见写法猜的，需要一份真实数据来核对
var probes := {}

const STAT_KEYS := [
	"stat_max_hp", "stat_armor", "stat_dodge", "stat_speed", "stat_lifesteal",
	"stat_hp_regeneration", "stat_harvesting", "stat_luck", "stat_engineering",
	"stat_melee_damage", "stat_ranged_damage", "stat_elemental_damage",
	"stat_percent_damage", "stat_damage", "stat_attack_speed", "stat_crit_chance",
	"stat_range", "stat_xp_gain", "stat_curse", "stat_all",
]


func getk(obj, key, d = null):
	if obj == null:
		return d
	if typeof(obj) == TYPE_DICTIONARY:
		return obj[key] if obj.has(key) else d
	if typeof(obj) == TYPE_OBJECT:
		var v = obj.get(key)   # Object.get 对不存在的属性返回 null，不报错
		return d if v == null else v
	return d


func collect_player(run_data, index, player_index := 0, adv = null) -> Dictionary:
	var stats := {}
	for key in STAT_KEYS:
		stats[key] = 0.0
		var v = read_stat(run_data, key)
		if v != null:
			stats[key] = v

	var gold := 0.0
	if typeof(run_data) == TYPE_OBJECT and run_data.has_method("get_player_gold"):
		gold = float(run_data.call("get_player_gold", player_index))
		probes["gold"] = "get_player_gold"
	else:
		for gk in ["gold", "money"]:
			var gv = read_stat(run_data, gk)
			if gv != null:
				gold = float(gv)
				probes["gold"] = "property:" + gk
				break
	var weapon_ids := []
	var raw_weapons = getk(run_data, "weapons", [])
	if typeof(raw_weapons) == TYPE_ARRAY:
		for w in raw_weapons:
			var rid := weapon_ref_id(w, index)
			if rid != "":
				weapon_ids.append(rid)
		if raw_weapons.size() > 0:
			probes["weapons"] = "RunData.weapons x%d -> %s" % [raw_weapons.size(), str(weapon_ids)]

	var player := {
		"wave": float(getk(run_data, "current_wave", 1)),
		"gold": gold,
		"danger": float(getk(run_data, "danger_level", 0)),
		"stats": stats,
		"weapon_ids": weapon_ids,
		"total_dps": 0.0,
		"melee_share": 0.5,
	}
	derive_dps(player, index, adv)
	return player


func read_stat(run_data, key: String):
	# 依次尝试游戏里几种常见的属性读取途径，并把第一条命中的记进 probes
	if run_data == null:
		return null
	if typeof(run_data) == TYPE_OBJECT:
		for m in ["get_stat", "get_stat_value", "get_effect_value"]:
			if run_data.has_method(m):
				probes["stat_read"] = m
				return run_data.call(m, key)
		var direct = run_data.get(key)
		if direct != null:
			probes["stat_read"] = "property"
			return direct
	var fx = getk(run_data, "effects", null)
	if fx != null:
		if typeof(fx) == TYPE_DICTIONARY:
			if fx.has(key):
				return getk(fx[key], "value", 0.0)
		elif typeof(fx) == TYPE_OBJECT and fx.has_method("get_stat"):
			return fx.call("get_stat", key)
	var prd = getk(run_data, "player_run_data", null)
	if prd != null:
		var pst = getk(prd, "stats", null)
		if pst != null:
			if typeof(pst) == TYPE_DICTIONARY and pst.has(key):
				return getk(pst[key], "value", 0.0)
			if typeof(pst) == TYPE_OBJECT and pst.has_method("get_stat"):
				return pst.call("get_stat", key)
	return null


func weapon_ref_id(weapon, index) -> String:
	# 游戏武器 my_id 不带等级后缀（形如 weapon_fist），参考表里是 weapon_fist_1..5。
	# 按装备等级挑变体；挑不到就退回同级可用变体。
	if weapon == null or index == null:
		return ""
	var my_id = getk(weapon, "my_id", "")
	if my_id == null or String(my_id) == "":
		return ""
	var base := String(my_id)
	var level = getk(weapon, "level", getk(weapon, "current_level", null))
	if level != null and index.has("%s_%d" % [base, int(level) + 1]):
		return "%s_%d" % [base, int(level) + 1]
	for l in range(1, 6):
		if index.has("%s_%d" % [base, l]):
			return "%s_%d" % [base, l]
	if index.has(base):
		return base
	return ""


func collect_offer(shop_item, index) -> Dictionary:
	# shop_item 是 ShopItem 节点（含 item_data 与显示价 value），也容忍直接传 ItemParentData
	if shop_item == null:
		return {}
	var item_data = getk(shop_item, "item_data", null)
	if item_data == null:
		item_data = shop_item
	var my_id = getk(item_data, "my_id", "")
	var effects = read_effects(item_data)
	# 显示价优先取 ShopItem.value（含折扣），退回 item_data.price/value
	var price = getk(shop_item, "value", null)
	if price == null:
		price = getk(item_data, "price", getk(item_data, "value", 0))
	probes["price"] = "ShopItem.value" if getk(shop_item, "value", null) != null else "item_data.price"
	var tier = int(getk(item_data, "tier", 0))
	var kind := "item"
	var ref = null
	var ref_name := ""
	var ref_id := str(my_id)
	if index != null:
		ref = index.get(ref_id)
		if ref == null and ref_id.find("weapon_") == 0:
			# 游戏里武器的 my_id 不带等级后缀（weapon_chopper），参考表里是 weapon_chopper_1..5。
			# 不认到的话中文名与职业套装都查不到，所以退回按等级 1 的条目。
			ref_id = weapon_base_ref(ref_id, index)
			if ref_id != "":
				ref = index.get(ref_id)
	if ref != null:
		kind = getk(ref, "kind", "item")
		ref_name = str(getk(ref, "name_zh", getk(ref, "name", "")))
	elif String(my_id).find("weapon_") == 0:
		kind = "weapon"
	var shown_name := ref_name
	if shown_name == "":
		shown_name = str(getk(item_data, "name", getk(item_data, "name_key", my_id)))
	var offer := {
		"id": ref_id if ref != null else str(my_id),
		"name": shown_name,
		"kind": kind,
		"price": float(price),
		"tier": tier,
		"cursed": false,
		"effects": effects,
	}
	if kind == "weapon":
		offer["base"] = read_weapon_base(item_data, ref)
	return offer


func weapon_base_ref(base_id: String, index) -> String:
	# 商店里武器的等级未知，取找到的第一个变体：职业套装各等级一致（已在参考表核对），
	# 数值仍以运行时数据为准（read_weapon_base），所以挑 _1 不影响打分
	for l in range(1, 6):
		var cand := "%s_%d" % [base_id, l]
		if index.has(cand):
			probes["offer_ref"] = "%s -> %s" % [base_id, cand]
			return cand
	return ""


func read_weapon_base(item_data, ref):
	# 优先用运行时数据（含升级后的数值），拿不到再用参考表
	var stats = getk(item_data, "stats", getk(item_data, "current_stats", null))
	if stats != null:
		return {
			"damage": float(getk(stats, "damage", getk(item_data, "damage", 10))),
			"cooldown": float(getk(stats, "cooldown", getk(item_data, "cooldown", 13))),
			"crit_chance": float(getk(stats, "crit_chance", getk(item_data, "crit_chance", 0))),
			"crit_damage": float(getk(stats, "crit_damage", getk(item_data, "crit_damage", 1.5))),
			"scaling": read_scaling(item_data),
		}
	if ref != null:
		return getk(ref, "base", {})
	return {}


func read_scaling(item_data) -> Dictionary:
	var out := {}
	var raw = getk(item_data, "scaling_stats", getk(item_data, "stats_scaling", null))
	if raw == null:
		return out
	if typeof(raw) == TYPE_ARRAY:
		for pair in raw:
			if typeof(pair) == TYPE_ARRAY and pair.size() >= 2:
				out[str(pair[0])] = float(pair[1])
	elif typeof(raw) == TYPE_DICTIONARY:
		for kk in raw.keys():
			out[str(kk)] = float(raw[kk])
	return out


func read_effects(item_data) -> Array:
	var out := []
	var fx = getk(item_data, "effects", null)
	if fx == null:
		probes["effects"] = "item_data.effects 不存在"
		return out
	var list = []
	if typeof(fx) == TYPE_DICTIONARY:
		list = fx.values()
		probes["effects"] = "Dictionary(%d)" % list.size()
	elif typeof(fx) == TYPE_ARRAY:
		list = fx
		probes["effects"] = "Array(%d)" % list.size()
	for e in list:
		var key = ""
		var val = 0.0
		if typeof(e) == TYPE_DICTIONARY:
			key = str(getk(e, "key", getk(e, "my_id", "")))
			val = float(getk(e, "value", 0))
		else:
			key = str(getk(e, "my_id", getk(e, "key", "")))
			val = float(getk(e, "value", 0))
		if key == "":
			continue
		if out.size() == 0:
			probes["effect_key"] = key
		out.append({"key": key, "value": val})
	return out


func derive_dps(player: Dictionary, index, adv) -> void:
	if adv == null:
		return
	var total := 0.0
	var melee := 0.0
	for wid in player["weapon_ids"]:
		var ref = null
		if index != null:
			ref = index.get(str(wid))
		if ref == null or getk(ref, "kind", "") != "weapon":
			continue
		var base = getk(ref, "base", {})
		var merged = base.duplicate(true)
		merged["type"] = getk(ref, "weapon_type", "melee")
		var dps := float(adv.weapon_dps(merged, player))
		total += dps
		if merged["type"] == "melee":
			melee += dps
	if total > 0:
		player["total_dps"] = total
		player["melee_share"] = melee / total
		probes["total_dps"] = "%.0f" % total
	else:
		player["total_dps"] = 50.0
		player["melee_share"] = 0.5
		probes["total_dps"] = "fallback(没有可算的武器)"


# ---------------------------------------------------------------------------
# 字段清单（只在 debug_dump 打开时跑，不参与任何建议计算）
#
# 天赋/怪物/ban 这类信息上游参考库里根本没有，游戏里字段名本机也无从验证。
# 与其继续猜，不如把「游戏对象到底自声明了哪些属性/方法」原样落盘一次：
# 拿到这份清单，#8 要接的字段就是查表而不是猜。
# 全部走 Godot 3 的 Script.get_script_*_list()，取不到就标 unsupported，不报错。

const INV_NAME_LIMIT := 260			# 单个对象最多记多少个名字，防诊断文件失控


func _script_of(obj):
	if obj == null or typeof(obj) != TYPE_OBJECT:
		return null
	var scr = obj.get_script()
	if scr == null or typeof(scr) != TYPE_OBJECT:
		return null
	return scr


func _brief(obj) -> String:
	if obj == null or not is_instance_valid(obj):
		return "(null)"
	var scr = _script_of(obj)
	var tag := "(native)"
	if scr != null:
		tag = String(scr.resource_path)
	return "%s @ %s" % [String(obj.get_class()), tag]


func _names(list, key: String, limit: int) -> Array:
	var out := []
	if typeof(list) != TYPE_ARRAY:
		return out
	for item in list:
		if out.size() >= limit:
			break
		if typeof(item) == TYPE_DICTIONARY:
			if item.has(key):
				out.append(str(item[key]))
	return out


func describe(obj, limit: int = INV_NAME_LIMIT) -> Dictionary:
	var info := {}
	# 只处理 Object：候选属性可能命中的是数字/字符串（如 character_index），不能对它调 get_class()
	if obj == null or typeof(obj) != TYPE_OBJECT:
		return {"missing": true, "raw": str(obj)}
	info["class"] = String(obj.get_class())
	var scr = _script_of(obj)
	if scr == null:
		info["script"] = "(native：没有 GDScript 脚本，字段清单拿不到)"
		return info
	info["script"] = String(scr.resource_path)
	for spec in [["props", "get_script_property_list"], ["methods", "get_script_method_list"],
			["signals", "get_script_signal_list"]]:
		var method := String(spec[1])
		if scr.has_method(method):
			info[String(spec[0])] = _names(scr.call(method), "name", limit)
		else:
			info[String(spec[0])] = "(引擎不支持 " + method + ")"
	# 引擎自带的完整属性列表（含继承来的）：usage 直方图留着，真实数据能告诉我们
	# 游戏里脚本变量与内建属性各自落在哪个 usage 值上
	var pl = obj.get_property_list()
	var hist := {}
	for entry in pl:
		if typeof(entry) == TYPE_DICTIONARY:
			var u := str(entry.get("usage", "?"))
			hist[u] = int(hist.get(u, 0)) + 1
	info["usage_histogram"] = hist
	info["all_props"] = _names(pl, "name", limit)
	return info


func scan_inventory(root_node, run_data, shop_item) -> Dictionary:
	var out := {"root_children": {}, "objects": {}, "hits": {}}
	if root_node == null or not is_instance_valid(root_node):
		out["error"] = "root_node 取不到，环境清单为空"
		return out
	for child in root_node.get_children():
		if is_instance_valid(child):
			out["root_children"][String(child.name)] = _brief(child)

	out["objects"]["RunData"] = describe(run_data)
	var svc = _find(root_node, "ItemService")
	if svc != null:
		out["objects"]["ItemService"] = describe(svc)

	# 角色节点：候选属性名全试一遍，命中哪个记进 hits（这一步是探测，不是依赖）
	for prop in ["character", "current_character", "active_character", "player_run_data",
			"players", "run_characters", "characters"]:
		var cand = getk(run_data, prop, null)
		if cand == null:
			continue
		if typeof(cand) == TYPE_ARRAY:
			if cand.size() > 0:
				out["hits"]["character_via"] = "%s[0]" % prop
				out["objects"]["character"] = describe(cand[0])
		elif typeof(cand) == TYPE_OBJECT:
			out["hits"]["character_via"] = prop
			out["objects"]["character"] = describe(cand)
		else:
			out["hits"]["character_via"] = "%s=%s" % [prop, str(cand)]
		if out["objects"].has("character"):
			break

	if shop_item != null and is_instance_valid(shop_item):
		out["objects"]["ShopItem"] = describe(shop_item)
		var data = getk(shop_item, "item_data", null)
		if data != null:
			out["objects"]["item_data"] = describe(data)
		else:
			out["hits"]["item_data"] = "ShopItem.item_data 取不到"
	return out


func _find(root_node, node_name: String):
	var path := "/root/" + node_name
	if root_node.has_node(path):
		return root_node.get_node(path)
	return null


