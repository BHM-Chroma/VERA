class_name MusicNotes
extends Node3D
## Пиксельные ноты, которые всплывают у её головы, пока играет музыка.

const TEXTURES := [
	preload("res://assets/fx/note1.png"),
	preload("res://assets/fx/note2.png"),
	preload("res://assets/fx/note3.png"),
]
const NOTE_SHADER := preload("res://shaders/note.gdshader")
const COLORS := [
	Color(1.0, 0.35, 0.62),   # розовый
	Color(0.68, 0.45, 1.0),   # фиолетовый
	Color(1.0, 0.72, 0.88),   # светло-розовый
]

## Пауза между нотами (сек).
@export var interval_min := 0.45
@export var interval_max := 0.9
## Насколько высоко ноты улетают (пиксели) и сколько живут (сек).
@export var rise := 150.0
@export var life := 1.9
@export var max_notes := 8

var active := false
var main: Node
var _timer := 0.0


func _ready() -> void:
	main = get_parent()


func _process(delta: float) -> void:
	if not active:
		return
	_timer -= delta
	if _timer <= 0.0:
		_timer = randf_range(interval_min, interval_max)
		if get_child_count() < max_notes:
			_spawn()


func _spawn() -> void:
	var m: Dictionary = main.character.pose_metrics(main.camera)
	if m.is_empty():
		return
	var head: Vector2 = m["head"]
	var cam: Camera3D = main.camera
	var px := float(main.window_size.y) / cam.size       # пикселей на единицу сцены
	var side := -1.0 if randf() < 0.5 else 1.0
	var start := head + Vector2(side * randf_range(60.0, 95.0), randf_range(-40.0, 0.0))

	# Ноты не должны выходить за видимую часть окна (окно бывает частично за краем экрана,
	# например когда она сидит на окне у верха экрана).
	var vis := _visible_rect()
	var edge := 28.0                      # полразмера ноты с запасом
	var side_room := edge + 52.0          # + максимальный снос и покачивание
	start.x = clampf(start.x, vis.position.x + side_room, vis.end.x - side_room)
	start.y = clampf(start.y, vis.position.y + edge + 60.0, vis.end.y - edge)
	var rise_px := minf(rise, start.y - (vis.position.y + edge))

	var s := Sprite3D.new()
	s.texture = TEXTURES.pick_random()
	s.pixel_size = randf_range(2.0, 2.6) / px             # 1 пиксель картинки ≈ 2–2.6 пикселя экрана
	s.flip_h = randf() < 0.3
	var mat := ShaderMaterial.new()                       # нота с глитчем, как у неё
	mat.shader = NOTE_SHADER
	mat.set_shader_parameter("tex", s.texture)
	s.material_override = mat
	s.set_instance_shader_parameter("tint", COLORS.pick_random())
	s.set_instance_shader_parameter("glitch", 1.0)
	add_child(s)
	var p0 := cam.project_position(start, cam.global_position.z)
	s.global_position = p0
	s.scale = Vector3.ZERO

	var drift := side * randf_range(10.0, 35.0) / px
	var sway := randf_range(8.0, 16.0) / px
	var phase := randf() * TAU
	var tw := create_tween().set_parallel(true)
	tw.tween_property(s, "scale", Vector3.ONE, 0.18).set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
	tw.tween_method(func(t: float):
		if not is_instance_valid(s):
			return
		# всплывает, покачиваясь; шаг по пикселям — чтобы движение было «пиксельным»
		var x := drift * t + sin(phase + t * 7.0) * sway
		var y := rise_px / px * t
		var pos := p0 + Vector3(x, y, 0.0)
		pos.x = snappedf(pos.x, 1.0 / px)
		pos.y = snappedf(pos.y, 1.0 / px)
		s.global_position = pos
		s.rotation.z = sin(phase + t * 5.0) * 0.25
		# появляется и исчезает глитчем
		var g := 1.0 - smoothstep(0.0, 0.12, t) + smoothstep(0.7, 1.0, t)
		s.set_instance_shader_parameter("glitch", clampf(g, 0.0, 1.0))
	, 0.0, 1.0, life)
	tw.chain().tween_callback(s.queue_free)


## Видимая часть окна персонажа (пиксели окна): окно ∩ экран.
func _visible_rect() -> Rect2:
	var win := Rect2(Vector2.ZERO, Vector2(main.window_size))
	var scr := DisplayServer.screen_get_usable_rect(DisplayServer.window_get_current_screen())
	var wpos := Vector2(DisplayServer.window_get_position())
	var on_screen := Rect2(Vector2(scr.position) - wpos, Vector2(scr.size))
	var r := win.intersection(on_screen)
	return r if r.has_area() else win


## Убрать все ноты (например, когда она рассыпается или прячется).
func clear() -> void:
	for c in get_children():
		c.queue_free()
