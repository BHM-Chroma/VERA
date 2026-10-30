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
var notes: MusicNotes
## Что сейчас играет (от мозга): {"playing": bool, "title": "...", "artist": "..."}.
var music := {}

## Переноска: плита под ней плавно догоняет курсор.
@export var carry_follow_speed := 16.0
## Курсор на плите: столько пикселей ниже кости таза (минус — выше).
@export var carry_cursor_offset := -6.0
## На какой высоте окна (пиксели сверху) стоит плита во время переноски.
@export var carry_seat_y := 430.0
## Зазор между нижними костями позы и верхом плиты (пиксели; толщина ног/подошв).
@export var carry_plate_gap := 6.0
var _carry := false
## Махание при запуске: во сколько раз быстрее, чем в Blender.
@export var wave_speed := 1.6
## «Скука»: сколько секунд без внимания (клик, перетаскивание, голос, чат) до того,
## как она сядет, и до того, как уснёт.
@export var bored_sit_after := 150.0
@export var bored_sleep_after := 480.0
var _idle_time := 0.0
var _sleep_requested := false
var _carry_pos := Vector2.ZERO        # позиция окна (дробная, для плавности)
var _carry_grab := Vector2.ZERO       # точка в окне, которая должна быть под курсором
var _carry_ready := false             # плита уже стоит под ней
var _carry_top := Vector2.ZERO        # верх плиты в окне (пиксели)

enum MenuId { CHAT, STATS, ON_TOP, RESET_POS, QUIT, WANDER, WANDER_NOW, HIDE, AUTOSTART, AUTOHIDE }

const TRAY_ICON := preload("res://assets/fx/tray.png")
const RUN_KEY := "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"

## Работа в фоне: спрятана по просьбе (значок у часов) / сама спряталась (полноэкранная игра, видео).
var _user_hidden := false
var _auto_hidden := false
var _peeking := false
var _shown := true
## Прятаться самой, пока открыта полноэкранная игра/видео (можно выключить в меню).
var auto_hide := true
var _fullscreen_now := false
var _tray: StatusIndicator

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
	auto_hide = bool(_settings.get_value("background", "auto_hide", true))
	platform = Platform.new()
	platform.name = "Platform"
	add_child(platform)
	notes = MusicNotes.new()
	notes.name = "MusicNotes"
	add_child(notes)
	_restore_position()
	_build_menu()
	_build_chat_window()
	brain.message_received.connect(_on_brain_message)
	brain.connection_changed.connect(_on_brain_connection)
	_start_brain_if_bundled()
	_build_tray()
	_build_glitch()
	_startup_peek()

	var args := OS.get_cmdline_user_args()
	for a in args:
		if a.begins_with("--screenshot="):
			_debug_screenshot(a.get_slice("=", 1))


## Глитч поверх всего окна (она, плита, ноты). Облачко — выше, его не ломаем.
@export var glitch_style := 2          # «плохой сигнал»
## Случайные короткие «помехи» раз в столько секунд (0 — выключить); под музыку — чаще.
@export var ambient_glitch_min := 60.0
@export var ambient_glitch_max := 150.0
@export var music_glitch_min := 25.0
@export var music_glitch_max := 60.0
## Мелкие помехи (короткие подёргивания) — чаще; под музыку — ещё чаще.
@export var micro_glitch_min := 8.0
@export var micro_glitch_max := 20.0
@export var micro_music_min := 4.0
@export var micro_music_max := 10.0
var _micro_timer := 10.0
var _glitch_mat: ShaderMaterial
var _glitch_pulse_tw: Tween
var _ambient_timer := 90.0


func _build_glitch() -> void:
	var layer := CanvasLayer.new()
	layer.name = "Glitch"
	layer.layer = 2
	var rect := ColorRect.new()
	rect.set_anchors_preset(Control.PRESET_FULL_RECT)
	rect.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_glitch_mat = ShaderMaterial.new()
	_glitch_mat.shader = preload("res://shaders/glitch.gdshader")
	_glitch_mat.set_shader_parameter("style", glitch_style)
	rect.material = _glitch_mat
	layer.add_child(rect)
	add_child(layer)
	character.glitch_material = _glitch_mat
	character.vanish = character.vanish
	_ambient_timer = randf_range(ambient_glitch_min, ambient_glitch_max)


## Короткая вспышка помех: amount — сила, time — длительность.
func glitch_pulse(amount := 0.6, time := 0.35) -> void:
	if _glitch_mat == null:
		return
	if _glitch_pulse_tw:
		_glitch_pulse_tw.kill()
	_glitch_mat.set_shader_parameter("amount", amount)
	_glitch_pulse_tw = create_tween()
	_glitch_pulse_tw.tween_method(func(v: float): _glitch_mat.set_shader_parameter("amount", v),
		amount, 0.0, time).set_trans(Tween.TRANS_EXPO).set_ease(Tween.EASE_IN)


func _update_ambient_glitch(delta: float) -> void:
	if ambient_glitch_min <= 0.0 or not _shown or _sleeping or wanderer.busy:
		return
	var playing := bool(music.get("playing", false))
	_ambient_timer -= delta
	if playing and _ambient_timer > music_glitch_max:
		_ambient_timer = randf_range(music_glitch_min, music_glitch_max)   # музыка включилась — чаще
	if _ambient_timer <= 0.0:
		_ambient_timer = randf_range(music_glitch_min, music_glitch_max) if playing \
			else randf_range(ambient_glitch_min, ambient_glitch_max)
		glitch_pulse(randf_range(0.5, 0.8), randf_range(0.2, 0.45))
	_micro_timer -= delta
	if _micro_timer <= 0.0:
		_micro_timer = randf_range(micro_music_min, micro_music_max) if playing \
			else randf_range(micro_glitch_min, micro_glitch_max)
		glitch_pulse(randf_range(0.18, 0.35), randf_range(0.06, 0.14))


## При запуске выглядывает из-за края экрана (idle_screen_start_anim), потом с глитчем
## появляется на своём месте. Если такой анимации нет — машет, как раньше.
@export var peek_anim := "idle_screen_start_anim"
## Сколько секунд анимации играть (дальше в Blender она уже стоит на месте).
@export var peek_length := 3.5
## Насколько голова заходит в экран из-за края (пиксели от края до центра головы).
## Как в Blender: край экрана режет её голову почти посередине, тело — за краем.
@export var peek_head_inside := 35.0
## Поворот вокруг своей оси при выглядывании (градусы, «к краю» экрана) и размер.
@export var peek_yaw := 13.0
@export var peek_scale := 1.15


func _startup_peek() -> void:
	if not character.has_animation(peek_anim):
		say("Привет! Я на месте.", 3.0)
		await get_tree().create_timer(0.3).timeout
		if character.current_pose == character.idle_animation and not _dragging:
			character.play("wave", 0.2, wave_speed)
		return
	wanderer.busy = true
	character.vanish = 1.0
	await get_tree().create_timer(0.25).timeout   # дождаться, пока окно встанет на своё место
	var final_pos := DisplayServer.window_get_position()
	var scr := wanderer._screen_rect()
	var side := -1 if final_pos.x + window_size.x / 2 < scr.get_center().x else 1

	# считаем, где она в анимации, пока её не видно
	character.vanish = 1.0
	character.position = Vector3.ZERO
	# в Blender выглядывает слева; справа — зеркально. Чуть крупнее и развёрнута к краю экрана.
	character.scale = Vector3(peek_scale if side < 0 else -peek_scale, peek_scale, peek_scale)
	character.rotation_degrees.y = peek_yaw * side
	character.play(peek_anim, 0.0)
	character.anim.seek(2.0, true)
	await get_tree().process_frame
	await get_tree().process_frame
	var m := character.pose_metrics(camera)
	var bbox: Rect2 = m["bbox"]
	var head: Vector2 = m["head"]
	var px := float(window_size.y) / camera.size
	# внутри окна: голова по центру окна, по высоте — вся она в окне
	var dx := window_size.x * 0.5 - head.x
	var dy := 70.0 - bbox.position.y
	character.position = Vector3(dx / px, -dy / px, 0.0)
	head += Vector2(dx, dy)
	# окно заходит за край экрана так, что в экране видна только голова (центр — на peek_head_inside от края);
	# тело остаётся за краем, как в Blender
	var wx: int
	if side < 0:
		wx = int(scr.position.x + peek_head_inside - head.x)
	else:
		wx = int(scr.end.x - peek_head_inside - head.x)
	var wy := clampi(int(scr.position.y + scr.size.y * 0.42 - head.y), scr.position.y - 60, scr.end.y - window_size.y)
	DisplayServer.window_set_position(Vector2i(wx, wy))

	character.anim.seek(0.0, true)
	character.play(peek_anim, 0.0)
	var tw := create_tween()
	tw.tween_property(character, "vanish", 0.0, 0.25)
	await get_tree().create_timer(0.35).timeout
	glitch_pulse(0.5, 0.3)
	await get_tree().create_timer(peek_length - 0.35).timeout

	# глитч — и она на своём месте
	glitch_pulse(0.9, 0.5)
	tw = create_tween()
	tw.tween_property(character, "vanish", 1.0, 0.35)
	await tw.finished
	character.position = Vector3.ZERO
	character.rotation_degrees.y = 0.0
	character.scale = Vector3.ONE
	character.screen_side = side
	character.set_pose(character.idle_animation, 0.0)
	DisplayServer.window_set_position(final_pos)
	await get_tree().process_frame
	tw = create_tween()
	tw.tween_property(character, "vanish", 0.0, 0.35)
	glitch_pulse(0.7, 0.45)
	await tw.finished
	wanderer.busy = false
	say("Привет! Я на месте.", 3.0)
	wanderer.keep_on_screen("запуск")


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
	return _shown and not _dragging and not bubble.visible and not _chat_window.visible \
		and not _menu.visible and not character.talking and not _sleeping


func _save_position() -> void:
	_settings.set_value("window", "position", DisplayServer.window_get_position())
	_settings.save(SETTINGS_PATH)


# ---------- сквозные клики ----------

func _process(delta: float) -> void:
	if _carry:
		_update_carry(delta)
	_update_boredom(delta)
	_update_ambient_glitch(delta)
	if character.head_look:
		character.head_look.allowed = _shown and not _sleeping and character.vanish <= 0.01 \
			and character.can_look()
	# ноты — пока играет музыка и она видна (не рассыпается, не спит)
	var want_notes := bool(music.get("playing", false)) and not _sleeping \
		and not wanderer.busy and character.vanish <= 0.01
	if want_notes != notes.active:
		notes.active = want_notes
	if character.vanish > 0.01 and notes.get_child_count() > 0:
		notes.clear()   # рассыпается — ноты пропадают сразу (иначе дотают сами)
	_pt_timer -= delta
	if _pt_timer <= 0.0 and not _dragging and _shown:
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
	if not _shown:
		return
	if event is InputEventMouseButton:
		var mb := event as InputEventMouseButton
		if mb.button_index == MOUSE_BUTTON_LEFT:
			if mb.pressed:
				if _sleeping or _sleep_requested:
					_request_wake()   # спит — клик её будит
					return
				attention()
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
			attention()
			_open_menu(DisplayServer.mouse_get_position())
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
	glitch_pulse(0.45, 0.3)
	# поднимаем её так, чтобы плита была на уровне carry_seat_y (а не у нижнего края окна)
	var dy := top.y - carry_seat_y
	if absf(dy) > 2.0:
		var tw := create_tween()
		tw.tween_property(character, "position:y", character.position.y + dy / px_per_unit, 0.2) \
			.set_trans(Tween.TRANS_SINE)
		top.y -= dy
	_carry_top = top
	_carry_grab = top + Vector2(0.0, -carry_cursor_offset)
	_carry_ready = true


func _end_carry() -> void:
	var was_ready := _carry_ready
	_carry = false
	_carry_ready = false
	platform.disappear()
	glitch_pulse(0.45, 0.3)
	wanderer.on_user_moved()
	# отпустили плиту у верхнего края окна — садится на него
	if was_ready:
		var plate := Vector2(DisplayServer.window_get_position()) + _carry_top
		if await wanderer.drop_on_window(plate):
			return
		if _carry:
			return   # её уже снова схватили
	character.end_drag()
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


# ---------- скука и сон ----------

## С ней пообщались — отсчёт «скуки» сначала.
func attention() -> void:
	_idle_time = 0.0


func _update_boredom(delta: float) -> void:
	if _sleeping or _sleep_requested or not _shown:
		return
	if _dragging or _carry or _menu.visible or _chat_window.visible:
		_idle_time = 0.0
		return
	_idle_time += delta
	if wanderer.busy or character.talking:
		return
	var playing := bool(music.get("playing", false))
	# долго скучает — засыпает (но не под музыку: она её слушает)
	if _idle_time >= bored_sleep_after and not playing:
		_fall_asleep()
		return
	# заскучала — садится прямо там, где стоит
	if _idle_time >= bored_sit_after and character.current_pose == character.idle_animation \
			and String(character.anim.current_animation) in [character.idle_animation, character.idle_animation + "_music"]:
		character.screen_side = _screen_side()
		# садится «прямо» (idle_sit_default), а если такой позы нет — как раньше
		character.set_pose("idle_sit_default" if character.has_animation("idle_sit_default") else "idle_sit", 0.8)
		glitch_pulse(0.4, 0.25)


## С какой стороны экрана она сейчас: -1 — левая половина, 1 — правая.
func _screen_side() -> int:
	var scr := DisplayServer.screen_get_usable_rect(DisplayServer.window_get_current_screen())
	var cx := DisplayServer.window_get_position().x + window_size.x / 2
	return -1 if cx < scr.get_center().x else 1


func _fall_asleep() -> void:
	_sleep_requested = true
	print("[скука] засыпаю")
	if brain.connected:
		brain.send({"type": "sleep"})   # мозг включит режим сна и пришлёт anim/face
	else:
		_sleeping = true
		character.force_face(2)
		wanderer.go_to_sleep()


func _request_wake() -> void:
	attention()
	_sleep_requested = false
	if brain.connected:
		brain.send({"type": "wake"})
	else:
		_sleeping = false
		character.force_face(-1)
		wanderer.wake_up()


func _on_poke() -> void:
	glitch_pulse(0.35, 0.15)
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
	_menu.add_item("Спрятаться (значок у часов)", MenuId.HIDE)
	_menu.add_check_item("Прятаться в полноэкранных играх", MenuId.AUTOHIDE)
	_menu.add_check_item("Запускать вместе с Windows", MenuId.AUTOSTART)
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
		MenuId.HIDE:
			set_user_hidden(true)
		MenuId.AUTOSTART:
			_set_autostart(not _autostart_enabled())
		MenuId.AUTOHIDE:
			set_auto_hide(not auto_hide)
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


# ---------- работа в фоне ----------

func _open_menu(at: Vector2i) -> void:
	_menu.set_item_checked(_menu.get_item_index(MenuId.ON_TOP), get_window().always_on_top)
	_menu.set_item_checked(_menu.get_item_index(MenuId.WANDER), wanderer.enabled)
	_menu.set_item_checked(_menu.get_item_index(MenuId.AUTOSTART), _autostart_enabled())
	_menu.set_item_checked(_menu.get_item_index(MenuId.AUTOHIDE), auto_hide)
	_menu.position = at
	_menu.popup()


## Значок у часов: левый клик — спрятать/показать, правый — меню.
func _build_tray() -> void:
	if not ClassDB.class_exists("StatusIndicator"):
		return
	_tray = StatusIndicator.new()
	_tray.icon = TRAY_ICON
	_tray.tooltip = "VERONIKA — левый клик: спрятать/показать, правый: меню"
	_tray.pressed.connect(_on_tray_pressed)
	add_child(_tray)


func _on_tray_pressed(button: int, pos: Vector2i) -> void:
	attention()
	if button == MOUSE_BUTTON_LEFT:
		if _auto_hidden:
			# спряталась сама (полноэкранное окно) — по клику по значку показываемся всё равно
			_auto_hidden = false
			_apply_visibility()
			_vanish_in()
			return
		set_user_hidden(not _user_hidden)
	elif button == MOUSE_BUTTON_RIGHT:
		if _user_hidden:
			set_user_hidden(false)   # меню — часть окна персонажа, поэтому сначала показываемся
		if _auto_hidden:
			_auto_hidden = false
			_apply_visibility()
		_open_menu(pos - Vector2i(0, 260))


func set_auto_hide(value: bool) -> void:
	auto_hide = value
	_settings.set_value("background", "auto_hide", value)
	_settings.save(SETTINGS_PATH)
	_auto_hidden = _fullscreen_now and auto_hide
	_apply_visibility()


func set_user_hidden(value: bool) -> void:
	if value == _user_hidden:
		return
	_user_hidden = value
	if value:
		_peeking = false
		# сначала договорит («Прячусь…»), потом рассыпается
		await get_tree().create_timer(0.15).timeout
		while bubble.visible or character.talking:
			await get_tree().create_timer(0.2).timeout
		if not _user_hidden:
			return
		await _vanish_out()
	_apply_visibility()
	if not value and not _auto_hidden:
		await _vanish_in()


## Спрятать/показать. Главное окно Godot скрыть нельзя, поэтому «спрятана» — это:
## персонаж и облачко невидимы, окно пропускает все клики насквозь, кадров почти нет.
func _apply_visibility() -> void:
	var hide_now := (_user_hidden and not _peeking) or _auto_hidden
	if hide_now == (not _shown):
		return
	_shown = not hide_now
	character.visible = _shown
	if hide_now:
		notes.clear()
		bubble.hide()
		# крошечная область в углу — всё остальное окно прозрачно для мыши
		DisplayServer.window_set_mouse_passthrough(PackedVector2Array([Vector2.ZERO, Vector2(1, 0), Vector2(0, 1)]))
	else:
		_pt_timer = 0.0
	# спрятана — почти не тратим видеокарту
	Engine.max_fps = 5 if hide_now else 60
	OS.low_processor_usage_mode = hide_now


## Спрятана, но надо что-то сказать: появляется на время облачка.
func _peek(duration: float) -> void:
	if _peeking:
		return
	_peeking = true
	_apply_visibility()
	await _vanish_in()
	await get_tree().create_timer(duration + 0.6).timeout
	while bubble.visible or character.talking:
		await get_tree().create_timer(0.3).timeout
	if _peeking and _user_hidden:
		await _vanish_out()
	_peeking = false
	_apply_visibility()


func _vanish_out() -> void:
	if not _shown:
		return
	var tw := create_tween()
	tw.tween_property(character, "vanish", 1.0, 0.4)
	await tw.finished


func _vanish_in() -> void:
	character.vanish = 1.0
	var tw := create_tween()
	tw.tween_property(character, "vanish", 0.0, 0.4)
	await tw.finished


## Автозапуск — запись в реестре HKCU ... Run (без прав администратора).
func _autostart_enabled() -> bool:
	if OS.get_name() != "Windows":
		return false
	return OS.execute("reg", ["query", RUN_KEY, "/v", "VERONIKA"], [], false, false) == 0


func _set_autostart(enable: bool) -> void:
	if OS.get_name() != "Windows" or OS.has_feature("editor"):
		say("Автозапуск работает только в собранной программе.", 3.0)
		return
	var code: int
	if enable:
		var exe := OS.get_executable_path().replace("/", "\\")
		code = OS.execute("reg", ["add", RUN_KEY, "/v", "VERONIKA", "/t", "REG_SZ",
			"/d", "\"%s\"" % exe, "/f"], [], false, false)
	else:
		code = OS.execute("reg", ["delete", RUN_KEY, "/v", "VERONIKA", "/f"], [], false, false)
	if code == 0:
		say("Буду запускаться вместе с Windows." if enable else "Больше не запускаюсь сама.", 3.0)
	else:
		say("Не получилось изменить автозапуск.", 3.0)


# ---------- мозг ----------

func say(text: String, duration := -1.0) -> void:
	if duration < 0.0:
		duration = clamp(text.length() * 0.07, 2.5, 15.0)
	if not _shown:
		if _user_hidden and not _auto_hidden and text != "…":
			_peek(duration)   # спрятана — «выглядывает», чтобы сказать, и снова прячется
		else:
			return            # идёт полноэкранная игра/видео — не мешаем облачками
	bubble.say(text, duration)
	if duration > 0.0 and text != "…":
		if not character.talking:
			glitch_pulse(0.25, 0.12)
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
			var anim_name := String(msg.get("name", "idle"))
			if anim_name == "sleep":
				wanderer.go_to_sleep()       # из любой позы — в idle_sleep в углу
			elif anim_name == "idle" and wanderer.is_sleeping_pose():
				wanderer.wake_up()
			elif character.current_pose == character.idle_animation:
				# разовые анимации (alarm и т.п.) — только когда стоит, а не сидит/лежит
				character.play(anim_name)
		"speak_start":
			character.set_talking(true)
			_talk_left = 0.0
		"speak_end":
			character.set_talking(false)
		"face":
			character.force_face(int(msg.get("index", -1)))
			_sleeping = int(msg.get("index", -1)) == 2
			_sleep_requested = false
			attention()
		"stats":
			_stats = msg
		"music":
			if bool(msg.get("playing", false)) != bool(music.get("playing", false)):
				glitch_pulse(0.5, 0.3)
			music = msg
			character.set_music(bool(msg.get("playing", false)))
		"attention":
			attention()
		"visibility":
			set_user_hidden(not bool(msg.get("visible", true)))
		"fullscreen":
			_fullscreen_now = bool(msg.get("active", false))
			_auto_hidden = _fullscreen_now and auto_hide
			_apply_visibility()
		"autohide":
			set_auto_hide(bool(msg.get("enabled", true)))
		"window_moved":
			wanderer.on_window_moved(msg)
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
