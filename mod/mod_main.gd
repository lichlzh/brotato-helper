extends Node
# Brotato ShopAdvisor 入口：由游戏内置 godot-mod-loader 实例化并调用 _init。
# 结构参考 lilongfei2000/Bantato、otDan/RollsLeft（godot-mod-loader v4+）。
#
# 节点树：/root/ModLoader/BrotatoHelper-ShopAdvisor（本文件）/ShopAdvisor（数据枢纽）
# 商店 UI 的两个 hook 都靠这个路径找回枢纽，改名字要同步改 extensions 里的常量。

const MOD_NAME := "BrotatoHelper-ShopAdvisor"
const LOG_TAG := "ShopAdvisor:Main"

var mod_dir_path := ""


# v6.1.0 之前的 loader 会传入 ModLoader 参数，带默认值可同时兼容新旧
func _init(_modLoader = null) -> void:
	mod_dir_path = ModLoaderMod.get_unpacked_dir().plus_file(MOD_NAME)

	var hub_script = load(mod_dir_path.plus_file("scripts/shop_advisor.gd"))
	if hub_script == null:
		ModLoaderLog.error("无法加载 scripts/shop_advisor.gd", LOG_TAG)
		return
	var hub = hub_script.new()
	hub.name = "ShopAdvisor"
	# 把 loader 实际解压出的目录交给枢纽，避免和 manifest 的 namespace/name 拼法绑死
	hub.set("mod_dir", mod_dir_path + "/")
	add_child(hub)

	_install_extension("extensions/ui/menus/shop/shop_item.gd")
	_install_extension("extensions/ui/menus/shop/shop_items_container.gd")


func _install_extension(rel_path: String) -> void:
	var full = mod_dir_path.plus_file(rel_path)
	var f = File.new()
	if not f.file_exists(full):
		ModLoaderLog.error("缺少 hook 脚本: " + rel_path, LOG_TAG)
		return
	ModLoaderMod.install_script_extension(full)


func _ready() -> void:
	ModLoaderLog.info("ShopAdvisor 已加载：商店会显示 买/可考虑/锁定/跳过 与刷新建议", LOG_TAG)
