class_name Wanderer
extends Node
## Перемещения по экрану. Раз в 3–5 минут, пока с персонажем никто не
## взаимодействует, она рассыпается и появляется в другом месте и позе:
##   • в нижнем углу экрана — стоит (idle), сидит (idle_sit) или лежит (idle_lying);
##   • на верхнем краю активного окна — сидит (idle_sit_on_Window), если окно
##     не развёрнуто на весь экран. Если окно двигают — едет вместе с ним,
##     если окно закрыли или развернули — перебирается в угол.

@export var min_interval := 180.0
@export var max_interval := 300.0
@export var vanish_time := 0.6
@export var corner_margin := 24
## Подошвы ниже костей стоп (пиксели окна).
@export var foot_pad := 6.0
## Сдвиг кромки окна относительно кости таза (пиксели окна; минус — кромка выше таза, на уровне ладоней).
@export var seat_pad := -14.0
## Вероятность сесть на окно (если подходящее окно есть).
@export var window_chance := 0.5
@export var corner_poses: PackedStringArray = ["idle", "idle_sit", "idle_sit_default", "idle_lying"]
@export var window_pose := "idle_sit_on_Window"
## Запас над головой для облачка (пиксели).
@export var head_room := 80.0

var enabled := true
var busy := false

var main: Node
var character: Character
var _timer := 0.0
var _follow_hwnd := 0
var _follow_off := Vector2i.ZERO      # смещение нашего окна от угла чужого окна
var _follow_win := Rect2i()
var _follow_pos := Vector2.ZERO       # где сейчас наше окно (дробно, для плавности)
var _follow_target := Vector2.ZERO    # куда ему надо
## Как быстро догонять окно (больше — резче, меньше — плавнее, но с отставанием).
@export var follow_speed := 45.0
## Если отпустить её так, что плита в стольких пикселях от верхнего края окна, — садится на него.
@export var drop_tolerance := 45
var _last_side := 1                   # -1 левый угол, 1 правый
## Как часто проверять, не ушла ли она за край экрана (сек).
@export var watch_interval := 4.0
var _watch_timer := 2.0
var _log_path := ""
var _head_offset := 0.0               # где макушка в окне персонажа (пиксели)


func _ready() -> void:
	main = get_parent()
	character = main.get_node("Character")
	reset_timer()


func reset_timer() -> void:
	_timer = randf_range(min_interval, max_interval)


func set_enabled(value: bool) -> void:
	enabled = value
	reset_timer()


func is_following_window() -> bool:
	return _follow_hwnd != 0


## Пользователь сам перетащил персонажа — перестаём ездить за окном.
func on_user_moved() -> void:
	_stop_follow()
	reset_timer()


func _start_follow(hwnd: int, win: Rect2i, pos: Vector2i) -> void:
	_follow_hwnd = hwnd
	_follow_win = win
	_follow_off = pos - win.position
	_follow_pos = Vector2(pos)
	_follow_target = _follow_pos
	# мозг сам будет присылать window_moved, как только окно сдвинется
	main.brain.send({"type": "follow_window", "hwnd": hwnd})


func _stop_follow() -> void:
	if _follow_hwnd != 0:
		main.brain.send({"type": "follow_window", "hwnd": 0})
	_follow_hwnd = 0


func _process(delta: float) -> void:
	if busy:
		return
	if _follow_hwnd != 0:
		_glide(delta)
	# сторож: раз в несколько секунд проверяем, что она целиком на экране
	_watch_timer -= delta
	if _watch_timer <= 0.0:
		_watch_timer = watch_interval
		if _follow_hwnd == 0 and main.is_idle() and character.vanish <= 0.01:
			keep_on_screen("сторож")
	if not enabled or not main.is_idle():
		return
	_timer -= delta
	if _timer <= 0.0:
		wander()


# ---------- само перемещение ----------

func wander(force_corner := false) -> void:
	if busy:
		return
	busy = true
	_stop_follow()
	await _fade(1.0)

	var placed := false
	if not force_corner and character.has_animation(window_pose) and randf() < window_chance:
		placed = await _try_place_on_window()
	if not placed:
		await place_in_corner()

	await _fade(0.0)
	busy = false
	reset_timer()


## Сон: из любой позы рассыпается и ложится спать (idle_sleep) в нижнем углу.
func go_to_sleep() -> void:
	while busy:
		await get_tree().process_frame
	if not character.has_animation("sleep"):
		return
	busy = true
	_stop_follow()
	await _fade(1.0)
	await place_in_corner("sleep", _last_side)
	await _fade(0.0)
	busy = false
	keep_on_screen("сон")


func is_sleeping_pose() -> bool:
	return character.has_animation("sleep") and \
		character.current_pose == character._resolve_animation("sleep")


## Проснулась — встаёт там же, в углу.
func wake_up() -> void:
	while busy:
		await get_tree().process_frame
	if not is_sleeping_pose():
		return
	busy = true
	await _fade(1.0)
	await place_in_corner(character.idle_animation, _last_side)
	await _fade(0.0)
	busy = false
	reset_timer()
	keep_on_screen("пробуждение")
	if _follow_hwnd == 0:
		keep_on_screen("перемещение")


func _fade(to: float) -> void:
	var tw := create_tween()
	tw.tween_property(character, "vanish", to, vanish_time).set_trans(Tween.TRANS_SINE)
	await tw.finished


## Ставит позу без перехода и ждёт, пока скелет обновится.
func _apply_pose(pose: String) -> Dictionary:
	if not character.set_pose(pose, 0.0):
		character.set_pose(character.idle_animation, 0.0)
	await get_tree().process_frame
	await get_tree().process_frame
	character.center_in_view(main.camera)
	return character.pose_metrics(main.camera)


func _screen_rect() -> Rect2i:
	# экран, на котором центр окна; если такого нет (монитор отключили) — главный
	var center: Vector2i = DisplayServer.window_get_position() + main.window_size / 2
	for i in DisplayServer.get_screen_count():
		var r := DisplayServer.screen_get_usable_rect(i)
		if r.has_point(center):
			return r
	return DisplayServer.screen_get_usable_rect(DisplayServer.get_primary_screen())


## Нижний угол экрана: левый или правый, поза — случайная из доступных.
func place_in_corner(pose := "", side := 0) -> void:
	if pose == "":
		var options: Array[String] = []
		for p in corner_poses:
			if character.has_animation(p):
				options.append(p)
		pose = options.pick_random() if options.size() > 0 else character.idle_animation
	if side == 0:
		side = -_last_side if randf() < 0.75 else _last_side
	_last_side = side
	character.screen_side = side

	var m := await _apply_pose(pose)
	var bbox: Rect2 = m["bbox"]
	var r := _screen_rect()
	var x: int
	if side < 0:
		x = r.position.x + corner_margin - int(bbox.position.x)
	else:
		x = r.end.x - corner_margin - int(bbox.end.x)
	var y := r.end.y - int(bbox.end.y + foot_pad)
	DisplayServer.window_set_position(Vector2i(x, y))
	_log("угол: поза %s, сторона %d, экран %s, окно (%d, %d), кости %s" % [character.current_pose, side, str(r), x, y, str(bbox)])


# ---------- сидение на окне ----------

func _try_place_on_window() -> bool:
	var reply = await main.brain.request({"type": "window_query"})
	if reply == null or not (reply.get("window") is Dictionary):
		return false
	return await _place_on_window(reply["window"])


## Её отпустили мышкой: если плита у верхнего края окна — садится на этот край.
## point — где была плита (координаты экрана Godot).
func drop_on_window(point: Vector2) -> bool:
	if busy or not main.brain.connected or not character.has_animation(window_pose):
		return false
	var origin := DisplayServer.screen_get_position(DisplayServer.get_primary_screen())
	var reply = await main.brain.request({"type": "window_at", "x": int(point.x) - origin.x,
		"y": int(point.y) - origin.y, "tol": drop_tolerance}, 0.5)
	if reply == null or not (reply.get("window") is Dictionary):
		return false
	busy = true
	# короткий пиксельный «глитч» прячет смену позы
	var tw := create_tween()
	tw.tween_property(character, "vanish", 0.45, 0.1)
	await tw.finished
	var ok := await _place_on_window(reply["window"], int(point.x))
	tw = create_tween()
	tw.tween_property(character, "vanish", 0.0, 0.18)
	await tw.finished
	busy = false
	reset_timer()
	return ok


## Сесть на верхний край окна w (словарь от мозга). seat_x — где сесть (экранный x), -1 — случайно.
func _place_on_window(w: Dictionary, seat_x := -1) -> bool:
	var win := _to_godot_rect(w)
	if win.size.x < 300 or _is_own_window(win):
		return false

	var m := await _apply_pose(window_pose)
	var seat: Vector2 = m["seat"]
	var bbox: Rect2 = m["bbox"]
	var scr := _screen_for(win)
	# хватает ли места над окном для головы и облачка
	var above := seat.y - bbox.position.y + head_room
	if win.position.y - above < scr.position.y:
		return false

	var margin := 70
	var min_x := win.position.x + margin
	var max_x := win.end.x - margin
	if max_x <= min_x:
		return false
	var seat_screen_x := randi_range(min_x, max_x) if seat_x < 0 else clampi(seat_x, min_x, max_x)
	var pos := Vector2i(seat_screen_x - int(seat.x), win.position.y - int(seat.y + seat_pad))
	DisplayServer.window_set_position(pos)

	_head_offset = bbox.position.y
	_start_follow(int(w.get("hwnd", 0)), win, pos)
	return true


## Плавно подтягиваем своё окно к нужному месту (а не прыжком).
func _glide(delta: float) -> void:
	var d := _follow_target - _follow_pos
	if d.length_squared() < 0.25:
		if _follow_pos != _follow_target:
			_follow_pos = _follow_target
			DisplayServer.window_set_position(Vector2i(_follow_pos.round()))
		return
	_follow_pos = _follow_pos.lerp(_follow_target, 1.0 - exp(-follow_speed * delta))
	DisplayServer.window_set_position(Vector2i(_follow_pos.round()))


## Мозг сообщил: окно, на котором она сидит, сдвинулось / изменилось / пропало.
func on_window_moved(msg: Dictionary) -> void:
	if int(msg.get("hwnd", 0)) != _follow_hwnd or _follow_hwnd == 0 or busy:
		return
	var w = msg.get("window")
	if not (w is Dictionary):
		# окно закрыли, свернули или развернули на весь экран — уходим в угол
		_follow_hwnd = 0
		wander(true)
		return
	var win := _to_godot_rect(w)
	if win == _follow_win:
		return
	if _is_own_window(win):
		_stop_follow()
		wander(true)
		return
	# окно сдвинули или изменили размер — едем вместе с ним
	var off := _follow_off
	if win.size.x != _follow_win.size.x:
		var rel: float = float(off.x) / maxf(1.0, float(_follow_win.size.x))
		off.x = int(rel * win.size.x)
	var target := win.position + off
	# окно уехало так, что ей негде сидеть (голова за краем экрана) — уходим в угол
	var scr := _screen_for(win)
	if target.y + int(_head_offset) < scr.position.y or not scr.intersects(Rect2i(target, main.window_size)):
		_stop_follow()
		wander(true)
		return
	_follow_win = win
	_follow_off = off
	_follow_target = Vector2(target)


## Координаты Windows -> координаты Godot (с учётом нескольких мониторов).
func _to_godot_rect(w: Dictionary) -> Rect2i:
	var origin := DisplayServer.screen_get_position(DisplayServer.get_primary_screen())
	return Rect2i(int(w.x) + origin.x, int(w.y) + origin.y, int(w.w), int(w.h))


func _screen_for(r: Rect2i) -> Rect2i:
	var c := r.get_center()
	for s in DisplayServer.get_screen_count():
		var sr := DisplayServer.screen_get_usable_rect(s)
		if sr.has_point(Vector2i(c.x, r.position.y)) or sr.has_point(c):
			return sr
	return _screen_rect()


## Защита: не садиться на собственное окно (иначе она «едет» за собой вверх).
func _is_own_window(win: Rect2i) -> bool:
	var own := Rect2i(DisplayServer.window_get_position(), DisplayServer.window_get_size())
	var d_pos := (own.position - win.position).abs()
	var d_size := (own.size - win.size).abs()
	return d_pos.x <= 8 and d_pos.y <= 8 and d_size.x <= 8 and d_size.y <= 8


# ---------- не уходить за край экрана ----------

## Где на экране реально нарисованы её пиксели (а не кости — волосы торчат дальше).
## Пусто, если её сейчас не видно.
func _drawn_rect() -> Rect2i:
	await RenderingServer.frame_post_draw
	var img := main.get_viewport().get_texture().get_image()
	var used := img.get_used_rect()
	if main.bubble.visible:
		# облачко тоже нарисовано — берём кости персонажа с запасом
		var m: Dictionary = character.pose_metrics(main.camera)
		if m.is_empty():
			return Rect2i()
		var b: Rect2 = m["bbox"]
		used = Rect2i(b.grow(40.0))
	if used.size.x <= 0:
		return Rect2i()
	return Rect2i(used.position + DisplayServer.window_get_position(), used.size)


## Если какая-то её часть за краем экрана — плавно сдвигаем окно обратно.
func keep_on_screen(reason := "") -> void:
	var drawn := await _drawn_rect()
	if drawn.size.x <= 0:
		return
	var scr := _screen_rect()
	var margin := 4
	var dx := 0
	var dy := 0
	var slack := 3                         # дыхание анимации — не повод двигать окно
	if drawn.position.x < scr.position.x - slack:
		dx = scr.position.x + margin - drawn.position.x
	elif drawn.end.x > scr.end.x + slack:
		dx = scr.end.x - margin - drawn.end.x
	if drawn.end.y > scr.end.y + 2 + slack:
		dy = scr.end.y + 2 - drawn.end.y
	elif drawn.position.y < scr.position.y - slack:
		dy = scr.position.y - drawn.position.y
	if dx == 0 and dy == 0:
		return
	var from := DisplayServer.window_get_position()
	var to := from + Vector2i(dx, dy)
	_log("%s: поза %s, нарисована %s, экран %s → сдвиг (%d, %d)" % [reason, character.current_pose, str(drawn), str(scr), dx, dy])
	var tw := create_tween()
	tw.tween_method(func(v: Vector2): DisplayServer.window_set_position(Vector2i(v.round())),
		Vector2(from), Vector2(to), 0.25).set_trans(Tween.TRANS_SINE)


## Журнал перемещений рядом с программой (veronika.log), чтобы разбирать такие случаи.
func _log(text: String) -> void:
	if _log_path == "":
		_log_path = "user://veronika.log" if OS.has_feature("editor") \
			else OS.get_executable_path().get_base_dir().path_join("veronika.log")
	var f := FileAccess.open(_log_path, FileAccess.READ_WRITE if FileAccess.file_exists(_log_path) else FileAccess.WRITE)
	if f == null:
		return
	if f.get_length() > 200_000:
		f.close()
		f = FileAccess.open(_log_path, FileAccess.WRITE)   # не даём журналу разрастись
	f.seek_end()
	f.store_line("%s  %s" % [Time.get_datetime_string_from_system(), text])
	f.close()
	print(text)
