extends "res://ui/menus/shop/shop_items_container.gd"
# 商店栏位容器：本 mod 唯一的驱动点（单人 1 个、合作每玩家各 1 个）。
# 只 hook 游戏里确认存在的方法（connect_shop_items / update_buttons_color，见 Bantato 同类 hook），
# 周期重算靠自建 Timer，避免覆盖 _ready/_process 打断游戏自身逻辑。
# 我们自己新增的成员/函数一律带 _adv_ 前缀：脚本扩展与父脚本共用一个命名空间，
# 撞名会静默覆盖游戏自己的实现。
# 任何取不到的字段都退回默认值；hook 失败最坏情况只是「不显示建议」。

const ADV_REFRESH_SEC := 0.6
const ADV_TIMER_NAME := "ShopAdvisorTick"
const ADV_SUMMARY_NAME := "ShopAdvisorSummary"

# 防止脚本扩展的 super 调用形成递归：父脚本内部再调 update_buttons_color() 时会派发到我们的版本
var _adv_busy := false
# 局面没变就不重算：整店组合选择要穷举子集，定时器高频重复算会掉帧
var _adv_sig := ""
# 环境探测结果（哪条读取途径命中的），攒着在每次重算前交给枢纽落盘
var _adv_notes := {}


# 故意不写返回类型：写成 Node 会让 Godot 3 把 hub.advise() 当成「Node 上找不到该方法」的编译错误
func _adv_hub():
	var path := "/root/ModLoader/BrotatoHelper-ShopAdvisor/ShopAdvisor"
	var root := get_tree().root
	return root.get_node(path) if root.has_node(path) else null


func connect_shop_items() -> void:
	.connect_shop_items()
	_adv_sig = ""		# 商店重建（新栏位节点、下一波）后强制重算一次
	_adv_ensure_tick()


func update_buttons_color() -> void:
	if _adv_busy:
		.update_buttons_color()
		return
	_adv_busy = true
	update_advisor()
	_adv_busy = false
	.update_buttons_color()


func _adv_ensure_tick() -> void:
	if has_node(ADV_TIMER_NAME):
		return
	var t = Timer.new()
	t.name = ADV_TIMER_NAME
	t.wait_time = ADV_REFRESH_SEC
	t.one_shot = false
	t.autostart = true
	t.connect("timeout", self, "_adv_on_tick")
	add_child(t)


func _adv_on_tick() -> void:
	if is_visible_in_tree():
		update_advisor()


func update_advisor() -> void:
	var hub = _adv_hub()
	if hub == null or hub.get("enabled") != true:
		return
	var slots = _shop_items
	if typeof(slots) != TYPE_ARRAY or slots.size() == 0:
		return
	var p_index := 0
	var pi = get("player_index")
	if pi != null:
		p_index = int(pi)

	var offers := []
	var has_item := []
	var locked := []
	var sig_bits := PoolStringArray()
	for it in slots:
		var ok := is_instance_valid(it) and it.get("item_data") != null
		has_item.append(ok)
		# 传 ShopItem 节点：显示价 ShopItem.value 已含折扣
		offers.append(it if ok else null)
		var lk := _adv_is_locked(it) if ok else false
		if lk:
			locked.append(slots.find(it))
		if ok:
			sig_bits.append("%s@%s%s" % [_adv_item_id(it), str(it.get("value")), "*" if lk else ""])
		else:
			sig_bits.append("-")
	var reroll := _adv_reroll_cost(p_index)
	var tier := _adv_shop_tier()
	var sig := "%d|%s|%.1f|%d|%d" % [p_index, sig_bits.join(","), reroll, tier, int(_adv_gold(p_index))]
	if sig == _adv_sig:
		return
	_adv_sig = sig

	if hub.has_method("note_probe"):
		for nk in _adv_notes.keys():
			hub.note_probe(nk, str(_adv_notes[nk]))
	# 诊断模式下扫一次游戏对象的字段清单（只跑一次，不参与建议计算）
	if hub.get("debug") == true and hub.has_method("set_inventory") and not hub.has_inventory():
		_adv_scan_inventory(hub, slots)
	var result = hub.advise(RunData, p_index, offers, reroll, tier, locked)
	if typeof(result) != TYPE_DICTIONARY or not result.has("items"):
		return
	hub.last_result = result

	var advised = result.get("items", [])
	for i in range(slots.size()):
		var node = slots[i]
		if not is_instance_valid(node) or not node.has_method("shop_advisor_set_result"):
			continue
		if has_item[i] and i < advised.size():
			node.shop_advisor_set_result(advised[i])
		else:
			node.shop_advisor_set_result({})   # 空栏/已买走：清掉角标
	_adv_update_summary(result)


func _adv_reroll_cost(p_index: int) -> float:
	# 游戏公式：首次 floor(wave*0.75)，之后每次 +floor(wave*0.40)
	var wave := _adv_wave()
	var used := _adv_rerolls_used(p_index)
	var base := int(floor(float(wave) * 0.75))
	var per := int(floor(float(wave) * 0.40))
	return float(max(0, base + used * per))


func _adv_note(key: String, value: String) -> void:
	_adv_notes[key] = value


# 天赋/怪物/ban 这些信息本机查不到游戏字段名，所以第一次重算时把 RunData、
# ItemService、角色节点、ShopItem 及其 item_data 的自声明属性/方法名交给枢纽落盘
func _adv_scan_inventory(hub, slots) -> void:
	var col = hub.get("collector")
	if col == null or not col.has_method("scan_inventory"):
		return
	var sample = null
	for it in slots:
		if is_instance_valid(it):
			sample = it
			break
	hub.set_inventory(col.call("scan_inventory", get_tree().root, RunData, sample))


func _adv_gold(p_index: int) -> float:
	if RunData != null and RunData.has_method("get_player_gold"):
		_adv_note("gold", "RunData.get_player_gold(%d)" % p_index)
		return float(RunData.call("get_player_gold", p_index))
	var g = RunData.get("gold") if RunData != null else null
	_adv_note("gold", "RunData.gold 兜底（合作模式不是本玩家的钱）")
	return float(g) if g != null else 0.0


func _adv_item_id(node) -> String:
	var data = node.get("item_data")
	if data == null:
		return ""
	var mid = data.get("my_id")
	return "" if mid == null else String(mid)


func _adv_is_locked(node) -> bool:
	# 锁定状态的字段名本机无法验证：几个候选按顺序试，命中哪个记进 probes。
	# 锁定只影响「刷新会换掉几个栏位」的期望值，猜错最坏也只是刷新建议偏保守。
	if not is_instance_valid(node):
		return false
	for key in ["locked", "is_locked", "lock"]:
		var v = node.get(key)
		if v != null:
			_adv_note("locked", "ShopItem.%s=%s" % [key, str(v)])
			return bool(v)
	var data = node.get("item_data")
	if data != null:
		for key in ["locked", "is_locked"]:
			var dval = data.get(key)
			if dval != null:
				_adv_note("locked", "item_data.%s=%s" % [key, str(dval)])
				return bool(dval)
	_adv_note("locked", "未找到字段，按未锁处理")
	return false


func _adv_rerolls_used(p_index: int) -> int:
	for cand in [["ItemService", "rerolls"], ["ItemService", "rerolls_count"], ["RunData", "rerolls"]]:
		var owner_name := String(cand[0])
		var key := String(cand[1])
		var obj = _adv_singleton(owner_name)
		if obj == null:
			continue
		var v = obj.get(key)
		if v == null:
			continue
		_adv_note("rerolls", "%s.%s" % [owner_name, key])
		if typeof(v) == TYPE_ARRAY:
			if p_index < v.size():
				return int(v[p_index])
			return 0
		return int(v)
	_adv_note("rerolls", "没找到（按 0 次算，刷价可能偏低）")
	return 0


func _adv_singleton(node_name: String) -> Node:
	var path := "/root/" + node_name
	var root := get_tree().root
	return root.get_node(path) if root.has_node(path) else null


func _adv_wave() -> int:
	var wv = RunData.get("current_wave")
	_adv_note("wave", "RunData.current_wave=" + ("null" if wv == null else str(wv)))
	return int(wv) if wv != null else 1


func _adv_shop_tier() -> int:
	var wave := _adv_wave()
	var svc = _adv_singleton("ItemService")
	if svc != null and svc.has_method("get_tier_from_wave"):
		_adv_note("tier", "ItemService.get_tier_from_wave")
		return int(svc.call("get_tier_from_wave", wave))
	_adv_note("tier", "内置波次→档位表兜底")
	if wave >= 19:
		return 4
	if wave >= 14:
		return 3
	if wave >= 9:
		return 2
	if wave >= 4:
		return 1
	return 0


# 刷新/锁定/整套建议写在 RerollButton 旁边（原来由 shop.gd 负责，改在这里省一个 hook 点）
func _adv_update_summary(result: Dictionary) -> void:
	var anchor = _adv_find_reroll_button()
	if anchor == null:
		return
	var label = null
	if anchor.has_node(ADV_SUMMARY_NAME):
		label = anchor.get_node(ADV_SUMMARY_NAME)
	else:
		label = Label.new()
		label.name = ADV_SUMMARY_NAME
		label.mouse_filter = Control.MOUSE_FILTER_IGNORE
		label.z_index = 4090
		label.rect_position = Vector2(-10, -46)
		anchor.add_child(label)
	var parts := []
	var advised = result.get("items", [])
	var buy_set = result.get("buy_set", [])
	if typeof(buy_set) == TYPE_ARRAY and buy_set.size() > 0:
		var names := PoolStringArray()
		for bi in buy_set:
			var slot := int(bi)
			if slot >= 0 and slot < advised.size():
				names.append(str(advised[slot].get("name", "")))
		if names.size() > 0:
			parts.append("[买这套] %s　花%d 剩%d" % [names.join("+"),
					int(round(float(result.get("spend", 0.0)))), int(round(float(result.get("left", 0.0))))])
	if str(result.get("reroll", "no")) == "yes":
		parts.append("[刷新] 这里重掷更划算")
	var lock = result.get("lock")
	if typeof(lock) == TYPE_DICTIONARY and not lock.empty():
		parts.append("[锁定] %s 值得留下" % str(lock.get("name", "")))
	label.text = PoolStringArray(parts).join("　")
	label.visible = parts.size() > 0


func _adv_find_reroll_button() -> Node:
	var p = get_parent()
	var hops := 0
	while p != null and hops < 8:
		if p.has_node("RerollButton"):
			return p.get_node("RerollButton")
		p = p.get_parent()
		hops += 1
	var root = get_tree().root
	return root.get_node("RerollButton") if root.has_node("RerollButton") else null
