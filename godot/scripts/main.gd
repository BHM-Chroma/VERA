extends Node3D
## Главный скрипт: прозрачное окно, перетаскивание, сквозные клики, меню, связь с мозгом.

const SETTINGS_PATH := "user://settings.cfg"

@export var window_size := Vector2i(520, 640)
## Запас вокруг костей (в пикселях), где персонаж ловит мышь.
@export var hit_padding := 26.0
## Как часто обновлять область кликов (сек).
@export var passthrough_interval := 0.1

@onready var camera: Camera3D = $Camera3D
@onready var character: Character = $Character
@onready var bubble: SpeechBubble = $UI/Bubble
@onready var brain: BrainLink = $BrainLink

var _dragging := false
var _moved := false
var _press_pos := Vector2i.ZERO
var _drag_offset := Vector2i.ZERO
var _pt_timer := 0.0
var _menu: PopupMenu
var _chat_window: Window
var _chat_input: LineEdit
var _stats := {}
var _talk_left := 0.0
var _settings := ConfigFile.new()
var _sleeping := false
var wanderer: Wanderer
var platform: Platform

## Переноска: плита под ней плавно догоняет курсор.
@export var carry_follow_speed := 16.0
## Курсор на плите: столько пикселей ниже кости таза (минус — выше).
@export var carry_cursor_offset := -6.0
## На какой высоте окна (пиксели сверху) стоит плита во время переноски.
@export var carry_seat_y := 430.0
## Зазор между нижними костями позы и верхом плиты (пиксели; толщина ног/подошв).
@export var carry_plate_gap := 6.0
var _carry := false
var _carry_pos := Vector2.ZERO        # позиция окна (дробная, для плавности)
var _carry_grab := Vector2.ZERO       # точка в окне, которая должна быть под курсором
var _carry_ready := false             # плита уже стоит под ней

enum MenuId { CHAT, STATS, ON_TOP, RESET_POS, QUIT, WANDER, WANDER_NOW }

const POKE_LINES := [
	"Эй, щекотно!",
	"Я тут, я слежу за твоим ПК.",
	"Что-то нужно?",
	"Правая кнопка мыши — меню.",
]


func _ready() -> void:
	get_viewport().transparent_bg = true
	DisplayServer.window_set_size(window_size)
	_settings.load(SETTINGS_PATH)
	wanderer = Wanderer.new()
	wanderer.name = "Wanderer"
	add_child(wanderer)
	wanderer.set_enabled(bool(_settings.get_value("wander", "enabled", true)))
	platform = Platform.new()
	platform.name = "Platform"
	add_child(platform)
	_restore_position()
	_build_menu()
	_build_chat_window()
	brain.message_received.connect(_on_brain_message)
	brain.connection_changed.connect(_on_brain_connection)
	_start_brain_if_bundled()
	say("Привет! Я на месте.", 3.0)
	_wave_hello()

	var args := OS.get_cmdline_user_args()
	for a in args:
		if a.begins_with("--screenshot="):
			_debug_screenshot(a.get_slice("=", 1))


## При запуске машет рукой (idle_Wave_start), потом встаёт в idle.
func _wave_hello() -> void:
	await get_tree().create_timer(0.3).timeout
	if character.current_pose == character.idle_animation and not _dragging:
		character.play("wave", 0.2)


# ---------- запуск мозга ----------

## В собранной версии мозг лежит рядом: brain/VeronikaBrain.exe.
## Запускаем его сами — если он уже работает, второй экземпляр просто выйдет.
func _start_brain_if_bundled() -> void:
	if OS.has_feature("editor"):
		return
	var exe := OS.get_executable_path().get_base_dir().path_join("brain/VeronikaBrain.exe")
	if FileAccess.file_exists(exe):
		OS.create_process(exe, [])


func _quit() -> void:
	# закрываем и мозг, чтобы он не остался висеть в фоне
	if brain.connected:
		brain.send({"type": "shutdown"})
		await get_tree().create_timer(0.3).timeout
	get_tree().quit()


# ---------- окно и позиция ----------

func _restore_position() -> void:
	var pos = _settings.get_value("window", "position") if _settings.has_section_key("window", "position") else null
	if pos is Vector2i and _is_on_screen(pos):
		DisplayServer.window_set_position(pos)
	else:
		_reset_position()


## Стоит в правом нижнем углу, ногами на панели задач.
func _reset_position() -> void:
	wanderer.on_user_moved()
	wanderer.place_in_corner(character.idle_animation, 1)


func _is_on_screen(pos: Vector2i) -> bool:
	var center := pos + window_size / 2
	for s in DisplayServer.get_screen_count():
		if DisplayServer.screen_get_usable_rect(s).has_point(center):
			return true
	return false


## Можно ли сейчас переместиться: никто с ней не взаимодействует.
func is_idle() -> bool:
	return not _dragging and not bubble.visible and not _chat_window.visible \
		and not _menu.visible and not character.talking and not _sleeping


func _save_position() -> void:
	_settings.set_value("window", "position", DisplayServer.window_get_position())
	_settings.save(SETTINGS_PATH)


# ---------- сквозные клики ----------

func _process(delta: float) -> void:
	if _carry:
		_update_carry(delta)
	_pt_timer -= delta
	if _pt_timer <= 0.0 and not _dragging:
		_pt_timer = passthrough_interval
		_update_passthrough()

	if _talk_left > 0.0:
		_talk_left -= delta
		if _talk_left <= 0.0:
			character.set_talking(false)

	if bubble.visible:
		_place_bubble()


## Облачко держится над головой, в какой бы позе она ни была.
func _place_bubble() -> void:
	var m := character.pose_metrics(camera)
	if m.is_empty():
		return
	var head: Vector2 = m["head"]
	var bbox: Rect2 = m["bbox"]
	# видимая часть окна (окно может частично заходить за край экрана)
	var scr := DisplayServer.screen_get_usable_rect(DisplayServer.window_get_current_screen())
	var wpos := DisplayServer.window_get_position()
	var visible := Rect2(Vector2(scr.position - wpos), Vector2(scr.size))
	bubble.place_above(Vector2(head.x, min(head.y, bbox.position.y) - 12.0), visible)


## Окно ловит мышь только внутри выпуклой оболочки вокруг костей (и облачка).
## Всё остальное окно пропускает клики к рабочему столу.
func _update_passthrough() -> void:
	var pts := PackedVector2Array()
	for p in character.get_outline_points_world():
		if camera.is_position_behind(p):
			continue
		var s := camera.unproject_position(p)
		for k in 8:
			pts.append(s + Vector2.from_angle(k * TAU / 8.0) * hit_padding)
	if bubble.visible:
		var r := bubble.get_global_rect()
		pts.append_array([r.position, Vector2(r.end.x, r.position.y), r.end, Vector2(r.position.x, r.end.y)])
	if pts.size() < 3:
		return
	var hull := Geometry2D.convex_hull(pts)
	DisplayServer.window_set_mouse_passthrough(hull)


# ---------- мышь ----------

func _input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		var mb := event as InputEventMouseButton
		if mb.button_index == MOUSE_BUTTON_LEFT:
			if mb.pressed:
				if wanderer.busy:
					return   # она как раз рассыпается/собирается — не хватаем
				_dragging = true
				_moved = false
				_press_pos = DisplayServer.mouse_get_position()
				_drag_offset = _press_pos - DisplayServer.window_get_position()
				# пока тащим — всё окно ловит мышь, чтобы не «соскользнуть»
				DisplayServer.window_set_mouse_passthrough(PackedVector2Array())
			elif _dragging:
				_dragging = false
				if _moved:
					_end_carry()
				else:
					_on_poke()
		elif mb.button_index == MOUSE_BUTTON_RIGHT and mb.pressed:
			_menu.set_item_checked(_menu.get_item_index(MenuId.ON_TOP), get_window().always_on_top)
			_menu.set_item_checked(_menu.get_item_index(MenuId.WANDER), wanderer.enabled)
			_menu.position = DisplayServer.mouse_get_position()
			_menu.popup()
	elif event is InputEventMouseMotion and _dragging:
		var m := DisplayServer.mouse_get_position()
		if not _moved and (m - _press_pos).length() > 4:
			_moved = true
			_begin_carry()


# ---------- переноска на плите ----------

func _begin_carry() -> void:
	wanderer.on_user_moved()
	_carry = true
	_carry_ready = false
	_carry_pos = Vector2(DisplayServer.window_get_position())
	_carry_grab = Vector2(_drag_offset)
	character.start_drag()
	# ждём, пока она сядет, и ставим плиту под таз
	await get_tree().create_timer(0.2).timeout
	if not _carry:
		return
	var m := character.pose_metrics(camera)
	if m.is_empty():
		return
	var seat: Vector2 = m["seat"]
	var bbox: Rect2 = m["bbox"]
	# верх плиты — под самыми нижними костями позы (ноги лежат на плите), размер — по ширине позы
	var top := Vector2(seat.x, bbox.end.y + carry_plate_gap)
	var px_per_unit := float(window_size.y) / camera.size
	platform.set_radius(clampf(bbox.size.x * 0.42 / px_per_unit, 0.5, 1.3))
	# плита — дочерняя у персонажа: куда сдвинем её, туда и плиту
	if platform.get_parent() != character:
		platform.reparent(character, false)
	platform.global_position = camera.project_position(top, camera.global_position.z) \
		+ Vector3(0.0, -platform.thickness * 0.5, 0.0)
	platform.appear()
	# поднимаем её так, чтобы плита была на уровне carry_seat_y (а не у нижнего края окна)
	var dy := top.y - carry_seat_y
	if absf(dy) > 2.0:
		var tw := create_tween()
		tw.tween_property(character, "position:y", character.position.y + dy / px_per_unit, 0.2) \
			.set_trans(Tween.TRANS_SINE)
		top.y -= dy
	_carry_grab = top + Vector2(0.0, -carry_cursor_offset)
	_carry_ready = true


func _end_carry() -> void:
	_carry = false
	_carry_ready = false
	platform.disappear()
	character.end_drag()
	wanderer.on_user_moved()
	await get_tree().create_timer(0.35).timeout
	if _carry:
		return   # её уже снова схватили
	await _keep_on_screen()
	_save_position()


## После того как её поставили: ноги не под панелью задач, голова не за краем.
func _keep_on_screen() -> void:
	var m := character.pose_metrics(camera)
	if m.is_empty():
		return
	var bbox: Rect2 = m["bbox"]
	var r := DisplayServer.screen_get_usable_rect(DisplayServer.window_get_current_screen())
	var pos := DisplayServer.window_get_position()
	var to := pos
	to.y = mini(to.y, r.end.y - int(bbox.end.y + wanderer.foot_pad))
	to.y = maxi(to.y, r.position.y - int(bbox.position.y) + 10)
	to.x = clampi(to.x, r.position.x - int(bbox.position.x) + 8, r.end.x - int(bbox.end.x) - 8)
	if to == pos:
		return
	var tw := create_tween()
	tw.tween_method(func(v: Vector2): DisplayServer.window_set_position(Vector2i(v.round())),
		Vector2(pos), Vector2(to), 0.3).set_trans(Tween.TRANS_SINE).set_ease(Tween.EASE_OUT)
	await tw.finished


func _update_carry(delta: float) -> void:
	var mouse := Vector2(DisplayServer.mouse_get_position())
	var target := mouse - _carry_grab
	if not _carry_ready:
		_carry_pos = target   # пока она садится — окно просто за мышью, как раньше
	else:
		_carry_pos = _carry_pos.lerp(target, 1.0 - exp(-carry_follow_speed * delta))
	DisplayServer.window_set_position(Vector2i(_carry_pos.round()))


func _on_poke() -> void:
	if brain.connected:
		brain.send({"type": "poke"})
	else:
		say(POKE_LINES.pick_random(), 2.5)


# ---------- меню ----------

func _build_menu() -> void:
	_menu = PopupMenu.new()
	_menu.add_item("Написать сообщение", MenuId.CHAT)
	_menu.add_item("Состояние ПК", MenuId.STATS)
	_menu.add_separator()
	_menu.add_check_item("Поверх всех окон", MenuId.ON_TOP)
	_menu.add_check_item("Гулять по экрану", MenuId.WANDER)
	_menu.add_item("Переместиться сейчас", MenuId.WANDER_NOW)
	_menu.add_item("Вернуть в угол экрана", MenuId.RESET_POS)
	_menu.add_separator()
	_menu.add_item("Выход", MenuId.QUIT)
	_menu.id_pressed.connect(_on_menu)
	add_child(_menu)


func _on_menu(id: int) -> void:
	match id:
		MenuId.CHAT:
			_open_chat()
		MenuId.STATS:
			_show_stats()
		MenuId.ON_TOP:
			get_window().always_on_top = not get_window().always_on_top
		MenuId.WANDER:
			set_wander(not wanderer.enabled)
		MenuId.WANDER_NOW:
			wanderer.wander()
		MenuId.RESET_POS:
			_reset_position()
			if _settings.has_section_key("window", "position"):
				_settings.erase_section_key("window", "position")
				_settings.save(SETTINGS_PATH)
		MenuId.QUIT:
			_quit()


func _build_chat_window() -> void:
	_chat_window = Window.new()
	_chat_window.title = "Сообщение для VERONIKA"
	_chat_window.size = Vector2i(380, 64)
	_chat_window.unresizable = true
	_chat_window.always_on_top = true
	_chat_window.visible = false
	_chat_window.transparent = false
	_chat_window.close_requested.connect(_chat_window.hide)

	var bg := Panel.new()
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	_chat_window.add_child(bg)

	var box := HBoxContainer.new()
	box.set_anchors_preset(Control.PRESET_FULL_RECT)
	box.offset_left = 10
	box.offset_right = -10
	box.offset_top = 14
	box.offset_bottom = -14
	_chat_window.add_child(box)

	_chat_input = LineEdit.new()
	_chat_input.placeholder_text = "Напиши что-нибудь или команду…"
	_chat_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_chat_input.text_submitted.connect(_send_chat)
	box.add_child(_chat_input)

	var btn := Button.new()
	btn.text = "▶"
	btn.pressed.connect(func(): _send_chat(_chat_input.text))
	box.add_child(btn)
	add_child(_chat_window)


func _open_chat() -> void:
	var wpos := DisplayServer.window_get_position()
	var pos := wpos + Vector2i(int((window_size.x - _chat_window.size.x) * 0.5), int(window_size.y * 0.55))
	# не даём окну уйти за край экрана или под панель задач
	var r := DisplayServer.screen_get_usable_rect(DisplayServer.window_get_current_screen())
	pos.x = clampi(pos.x, r.position.x, r.end.x - _chat_window.size.x)
	pos.y = clampi(pos.y, r.position.y + 30, r.end.y - _chat_window.size.y - 10)
	_chat_window.position = pos
	_chat_window.show()
	_chat_window.grab_focus()
	_chat_input.grab_focus()


func _send_chat(text: String) -> void:
	text = text.strip_edges()
	if text == "":
		return
	_chat_input.clear()
	_chat_window.hide()
	if brain.connected:
		brain.send({"type": "chat", "text": text})
		say("…", 0.0)
	else:
		say("Мой «мозг» не запущен. Запусти brain.py, и я смогу ответить.", 5.0)


func set_wander(value: bool) -> void:
	wanderer.set_enabled(value)
	_settings.set_value("wander", "enabled", value)
	_settings.save(SETTINGS_PATH)


func _show_stats() -> void:
	if _stats.is_empty():
		say("Пока не знаю: «мозг» не прислал данные.", 3.0)
		return
	var lines := PackedStringArray()
	lines.append("CPU: %d%%" % int(_stats.get("cpu", 0)))
	lines.append("RAM: %d%%" % int(_stats.get("ram", 0)))
	if _stats.has("gpu"):
		lines.append("GPU: %d%%, %d°C" % [int(_stats.get("gpu", 0)), int(_stats.get("gpu_temp", 0))])
	say("\n".join(lines), 5.0)


# ---------- мозг ----------

func say(text: String, duration := -1.0) -> void:
	if duration < 0.0:
		duration = clamp(text.length() * 0.07, 2.5, 15.0)
	bubble.say(text, duration)
	if duration > 0.0 and text != "…":
		character.set_talking(true)
		_talk_left = min(duration, max(1.0, text.length() * 0.055))
	_pt_timer = 0.0


func _on_brain_connection(online: bool) -> void:
	if online:
		say("Мозг подключён. Слушаю!", 2.5)


func _on_brain_message(msg: Dictionary) -> void:
	match String(msg.get("type", "")):
		"say":
			say(String(msg.get("text", "")), float(msg.get("duration", -1.0)))
			if msg.has("anim") and character.current_pose == character.idle_animation:
				character.play(String(msg.anim))
		"anim":
			# разовые анимации (alarm и т.п.) — только когда стоит, а не сидит/лежит
			if character.current_pose == character.idle_animation:
				character.play(String(msg.get("name", "idle")))
		"speak_start":
			character.set_talking(true)
			_talk_left = 0.0
		"speak_end":
			character.set_talking(false)
		"face":
			character.force_face(int(msg.get("index", -1)))
			_sleeping = int(msg.get("index", -1)) == 2
		"stats":
			_stats = msg
		"wander":
			if msg.has("enabled"):
				set_wander(bool(msg.enabled))
			if msg.get("now", false):
				wanderer.wander()
		"quit":
			get_tree().quit()


# ---------- отладка ----------

func _debug_screenshot(path: String) -> void:
	await get_tree().create_timer(1.5).timeout
	await RenderingServer.frame_post_draw
	get_viewport().get_texture().get_image().save_png(path)
	get_tree().quit()
