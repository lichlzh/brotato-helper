extends SceneTree
# 差分测试运行器：在 Godot 3 里加载真实的 advisor.gd，跑 cases.json（已在 Python 侧
# 补好 total_dps/melee_share），把整店结果写回 out.json，供 tools/check_gd_diff.py 对拍。
# 只依赖 res:// 下的文件，由 harness 复制到临时工程。

func _read_json(path: String):
	var f = File.new()
	if not f.file_exists(path):
		printerr("差分运行器：找不到 ", path)
		return null
	if f.open(path, File.READ) != OK:
		printerr("差分运行器：打不开 ", path)
		return null
	var txt = f.get_as_text()
	f.close()
	var res = JSON.parse(txt)
	if res.error != OK:
		printerr("差分运行器：JSON 解析失败 ", path)
		return null
	return res.result


func _write_json(path: String, data) -> bool:
	var f = File.new()
	if f.open(path, File.WRITE) != OK:
		printerr("差分运行器：写不了 ", path)
		return false
	f.store_string(JSON.print(data))
	f.close()
	return true


func _init():
	var ok := _run()
	quit(0 if ok else 1)


func _smoke_scripts() -> bool:
	# 另外两个纯脚本不参与整店计算，但必须能被 3.x 编译；顺便实跑一次 JSON 读取，
	# 因为 Godot 3 的 JSON 是单例（JSON.parse），写成 JSON.new() 只会在运行时炸。
	var hub_scr = load("res://shop_advisor.gd")
	var col_scr = load("res://state_collector.gd")
	if hub_scr == null or col_scr == null:
		printerr("差分运行器：shop_advisor.gd / state_collector.gd 编译失败")
		return false
	var hub = hub_scr.new()
	if hub == null:
		return false
	hub.mod_dir = "res://"
	var priors = hub.call("_read_json", "res://priors.json")
	if typeof(priors) != TYPE_DICTIONARY or priors.size() == 0:
		printerr("差分运行器：shop_advisor._read_json 读不出 priors.json")
		return false
	var col = col_scr.new()
	if col == null:
		return false
	return true


func _run() -> bool:
	if not _smoke_scripts():
		return false
	var adv_scr = load("res://advisor.gd")
	if adv_scr == null:
		printerr("差分运行器：advisor.gd 加载失败")
		return false
	var adv = adv_scr.new()
	var refdata = _read_json("res://brotato_data.json")
	var priors = _read_json("res://priors.json")
	var cases = _read_json("res://cases.json")
	if refdata == null or priors == null or cases == null:
		return false
	if typeof(cases) != TYPE_ARRAY:
		printerr("差分运行器：cases.json 必须是数组")
		return false

	var index := {}
	for group in ["weapons", "items", "characters"]:
		for e in refdata.get(group, []):
			index[str(e["id"])] = e
	var names := {}
	for k in refdata.get("stat_names", {}).keys():
		names[String(k).to_lower()] = refdata["stat_names"][k]
	index["_stat_names"] = names
	index["_sets"] = refdata.get("sets", {})

	var out := []
	for case in cases:
		var player = case.get("player", {})
		var shop = case.get("shop", {})
		var res = adv.advise_shop(player, shop, priors, index)
		out.append({"name": str(case.get("name", "")), "result": res})
	return _write_json("res://out.json", out)
