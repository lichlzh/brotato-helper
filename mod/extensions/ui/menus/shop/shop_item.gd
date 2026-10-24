extends "res://ui/menus/shop/shop_item.gd"
# 每件商品左上角建议角标：买 / 可考虑 / 锁定 / 跳过 + 分数/价格。
# 结果由 shop_items_container 扩展统一下发（整店一起算才准确）。
# 新增成员一律带 _adv_ 前缀，避免覆盖游戏脚本自己的名字。
# 注意：这里不覆盖 _ready（父脚本是否有 _ready 不确定，覆盖会打断游戏自身初始化），
# 角标在第一次收到建议时惰性创建。

const ADV_BADGE_NAME := "ShopAdvisorBadge"

var _adv_badge: Label = null
var _adv_result: Dictionary = {}


func shop_advisor_set_result(result) -> void:
	_adv_result = result if typeof(result) == TYPE_DICTIONARY else {}
	_adv_refresh_badge()


func _adv_ensure_badge():
	if _adv_badge != null and is_instance_valid(_adv_badge):
		return _adv_badge
	if has_node(ADV_BADGE_NAME):
		_adv_badge = get_node(ADV_BADGE_NAME) as Label
		return _adv_badge
	_adv_badge = Label.new()
	_adv_badge.name = ADV_BADGE_NAME
	_adv_badge.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_adv_badge.z_index = 4090
	add_child(_adv_badge)
	_adv_badge.rect_position = Vector2(6, 2)
	return _adv_badge


func _adv_refresh_badge() -> void:
	if _adv_result.empty():
		if _adv_badge != null and is_instance_valid(_adv_badge):
			_adv_badge.text = ""
		return
	var badge = _adv_ensure_badge()
	if badge == null:
		return
	var rec := str(_adv_result.get("recommendation", "skip"))
	var score := int(round(float(_adv_result.get("score", 0.0))))
	var colors := {"buy": Color(0.25, 1.0, 0.35), "lock": Color(0.45, 0.8, 1.0),
			"maybe": Color(1.0, 0.85, 0.35), "skip": Color(0.8, 0.5, 0.5)}
	var words := {"buy": "买", "lock": "锁定", "maybe": "可考虑", "skip": "跳过"}
	badge.set("custom_colors/font_color", colors.get(rec, Color.WHITE))
	# 「分/价」并排：组合建议下会出现「分数更高的那件反而标锁定」，看到价就明白是预算不够
	var price := int(round(float(_adv_result.get("price", 0.0))))
	if price > 0:
		badge.text = "%s %d/%d" % [words.get(rec, rec), score, price]
	else:
		badge.text = "%s %d" % [words.get(rec, rec), score]
	var tip = _adv_result.get("reasons", [])
	if typeof(tip) == TYPE_ARRAY and tip.size() > 0:
		var strs := PoolStringArray()
		for t in tip:
			strs.append(str(t))
		badge.tooltip_text = strs.join("、")
