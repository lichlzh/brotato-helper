extends Node
# 数据枢纽：加载参考数据与先验，构建 id 索引，供 shop/shop_item 扩展调用。
# 加载失败时整体退化为「不显示建议」，绝不影响游戏。

const MOD_DIR := "res://mods-unpacked/BrotatoHelper-ShopAdvisor/"
const DEBUG_FILE_PREFIX := "user://shopadvisor_debug_w"
const DEBUG_FILE_SUFFIX := ".json"

# mod_main.gd 会用 loader 实际解压出的目录覆盖它（正常情况两者一致）
var mod_dir := MOD_DIR
var priors: Dictionary = {}
var index: Dictionary = {}
var advisor = null
var collector = null
var enabled := false
var debug := false
# 采集途径命中记录（debug 打开时写进诊断文件，用来确认游戏字段名有没有猜对）
var probes: Dictionary = {}
# 最近一次整店结果（shop_items_container 扩展写入，角标/汇总行读取）。结构：
# {items: [每栏位结果...], reroll: String, lock: Dictionary or null,
#  buy_set: [推荐一起买的栏位下标], spend/left: float}
var last_result = {}
# 游戏对象字段清单（debug_dump 打开时由 UI 侧扫一次，只落盘、不参与建议）
var inventory = {}


func has_inventory() -> bool:
	return not inventory.empty()


func set_inventory(data) -> void:
	if typeof(data) == TYPE_DICTIONARY and inventory.empty():
		inventory = data


func _ready() -> void:
	var adv_scr = load(mod_dir + "scripts/advisor.gd")
	var col_scr = load(mod_dir + "scripts/state_collector.gd")
	if adv_scr == null or col_scr == null:
		printerr("ShopAdvisor: 引擎脚本加载失败，建议功能关闭（目录：" + mod_dir + "）")
		return
	advisor = adv_scr.new()
	collector = col_scr.new()
	priors = _read_json(mod_dir + "data/priors.json")
	if priors.empty():
		printerr("ShopAdvisor: priors.json 加载失败，建议功能关闭")
		return
	debug = bool(priors.get("debug_dump", false))
	var refdata = _read_json(mod_dir + "data/brotato_data.json")
	if refdata != null:
		_build_index(refdata)
	enabled = true
	print("ShopAdvisor 已启用：参考条目 %d，调试落盘 %s" % [index.size(), str(debug)])


func _build_index(refdata: Dictionary) -> void:
	for group in ["weapons", "items", "characters"]:
		var arr = refdata.get(group, [])
		for e in arr:
			index[str(e["id"])] = e
	var names := {}
	for k in refdata.get("stat_names", {}).keys():
		names[String(k).to_lower()] = refdata["stat_names"][k]
	index["_stat_names"] = names
	# 武器职业套装表：引擎靠它算「再拿一把同职业能多吃多少套装加成」
	index["_sets"] = refdata.get("sets", {})


func _read_json(path: String) -> Dictionary:
	var out := {}
	var f = File.new()
	if f.file_exists(path) == false:
		printerr("ShopAdvisor: 找不到 ", path)
		return out
	var err = f.open(path, File.READ)
	if err != OK:
		printerr("ShopAdvisor: 无法打开 ", path, " err=", str(err))
		f.close()
		return out
	var txt = f.get_as_text()
	f.close()
	var res = JSON.parse(txt)
	if res.error != OK:
		printerr("ShopAdvisor: JSON 解析失败 ", path, " ", res.get("error_string"))
		return out
	if typeof(res.result) == TYPE_DICTIONARY:
		out = res.result
	return out


# 入口：给定 run_data/玩家序号与商店栏位数据，返回 advise_shop 的结果字典
func advise(run_data, player_index: int, offers_data: Array, reroll_cost: float, shop_tier: int,
		locked_indices: Array = []) -> Dictionary:
	if not enabled:
		return {}
	var player: Dictionary = collector.collect_player(run_data, index, player_index, advisor)
	var offers := []
	for node in offers_data:
		if is_instance_valid(node):
			offers.append(collector.collect_offer(node, index))
		else:
			offers.append(null)
	var shop := {"tier": shop_tier, "reroll_cost": reroll_cost, "offers": offers,
		"locked": locked_indices}
	var result = advisor.advise_shop(player, shop, priors, index)
	if debug:
		_write_debug(player, shop, result)
	return result


# UI 侧把它探到的环境信息记一笔（档位/刷价/金钱的读取途径在各版本不一样）
func note_probe(key, value) -> void:
	probes[str(key)] = str(value)


func _merged_probes() -> Dictionary:
	var merged := {}
	for ck in collector.probes.keys():
		merged[str(ck)] = str(collector.probes[ck])
	for pk in probes.keys():
		merged[str(pk)] = str(probes[pk])
	return merged


func _write_debug(player, shop, result) -> void:
	# 每个波次一个文件（同波内重复重算会覆盖）：一次逛几局就能攒下多份真实局面，
	# 开发机上照着核对游戏字段名/采集途径猜得对不对，不用靠猜改代码
	var wave := 0
	if typeof(player) == TYPE_DICTIONARY:
		wave = int(player.get("wave", 0))
	if wave < 0:
		wave = 0
	var path := DEBUG_FILE_PREFIX + str(wave) + DEBUG_FILE_SUFFIX
	# Godot 3 的 get_setting 只收一个参数，没有默认值重载
	var game_version := "unknown"
	if ProjectSettings.has_setting("application/config/version"):
		game_version = str(ProjectSettings.get_setting("application/config/version"))
	var payload := {
		"engine": str(Engine.get_version_info().get("string", "?")),
		"game_version": game_version,
		"mod_dir": mod_dir,
		"index_size": index.size(),
		"probes": _merged_probes(),
		"inventory": inventory,
		"player": player,
		"shop": shop,
		"result": result,
	}
	var f = File.new()
	var err = f.open(path, File.WRITE)
	if err != OK:
		printerr("ShopAdvisor: 调试文件写不了 ", path, " err=", str(err))
		debug = false			# 写不了就关掉，免得每次重算都刷错误日志
		return
	f.store_string(JSON.print(payload, "  "))
	f.close()
