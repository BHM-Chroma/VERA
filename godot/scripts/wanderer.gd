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
@export var corner_poses: PackedStringArray = ["idle", "idle_sit", "idle_lying"]
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
var _follow_timer := 0.0
var _last_side := 1                   # -1 левый угол, 1 правый
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
	_follow_hwnd = 0
	reset_timer()


func _process(delta: float) -> void:
	if busy:
		return
	if _follow_hwnd != 0:
		_follow_timer -= delta
		if _follow_timer <= 0.0:
			_follow_timer = 0.25
			_check_follow()
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
	_follow_hwnd = 0
	await _fade(1.0)

	var placed := false
	if not force_corner and character.has_animation(window_pose) and randf() < window_chance:
		placed = await _try_place_on_window()
	if not placed:
		await place_in_corner()

	await _fade(0.0)
	busy = false
	reset_timer()


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
	return DisplayServer.screen_get_usable_rect(DisplayServer.window_get_current_screen())


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


# ---------- сидение на окне ----------

func _try_place_on_window() -> bool:
	var reply = await main.brain.request({"type": "window_query"})
	if reply == null or not (reply.get("window") is Dictionary):
		return false
	var win := _to_godot_rect(reply["window"])
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
	var seat_screen_x := randi_range(min_x, max_x)
	var pos := Vector2i(seat_screen_x - int(seat.x), win.position.y - int(seat.y + seat_pad))
	DisplayServer.window_set_position(pos)

	_head_offset = bbox.position.y
	_follow_hwnd = int(reply["window"].get("hwnd", 0))
	_follow_win = win
	_follow_off = pos - win.position
	_follow_timer = 0.25
	return true


func _check_follow() -> void:
	var hwnd := _follow_hwnd
	var reply = await main.brain.request({"type": "window_query", "hwnd": hwnd}, 1.0)
	if hwnd != _follow_hwnd or busy:
		return
	if reply == null:
		return   # мозг молчит — просто остаёмся на месте
	var w = reply.get("window")
	if not (w is Dictionary):
		# окно закрыли, свернули или развернули на весь экран — уходим в угол
		_follow_hwnd = 0
		wander(true)
		return
	var win := _to_godot_rect(w)
	if win == _follow_win:
		return
	if _is_own_window(win):
		_follow_hwnd = 0
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
		_follow_hwnd = 0
		wander(true)
		return
	_follow_win = win
	_follow_off = off
	DisplayServer.window_set_position(target)


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
