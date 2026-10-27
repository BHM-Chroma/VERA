class_name Character
extends Node3D
## Персонаж: загружает модель, ставит шейдеры, играет анимации, управляет лицом.

const MODEL_SCENE := preload("res://assets/model/lowpolybodyChar.glb")
const TOON_SHADER := preload("res://shaders/toon.gdshader")
const FACE_SHADER := preload("res://shaders/face.gdshader")
const OUTLINE_SHADER := preload("res://shaders/outline.gdshader")

## Какой материал из Blender чем заменить (имя материала -> роль).
const MATERIAL_ROLES := {
	"MateriaFace": "body",
	"Material.002": "hair",
	"Material": "face",
	"outline1": "outline1",
	"outline2": "outline2",
}

enum Face { CALM = 0, TALK = 1, BLINK = 2 }

## Направление на свет (влияет на тени и штриховку).
@export var light_direction := Vector3(-0.45, 0.75, 0.6)
@export var outline1_color := Color(0.0, 0.0, 0.0)
@export var outline2_color := Color(0.0, 0.0, 0.0)
@export var idle_animation := "idle"
## Моргать из кода, если в текущей анимации нет ключей кости cc_facecontr.
@export var auto_blink := true
## Сколько кость должна простоять в зоне «говорит», чтобы рот открылся (сек).
@export var talk_hold_time := 0.12
## Длительность плавного перехода idle <-> talk (сек).
@export var talk_blend := 0.35

var model: Node3D
var anim: AnimationPlayer
var skeleton: Skeleton3D
var materials: Array[ShaderMaterial] = []
var all_materials: Array[ShaderMaterial] = []
## Текущая поза (имя анимации): idle, idle_sit, idle_lying, idle_sit_on_Window...
var current_pose := "idle"
## 0 — виден, 1 — рассыпался (эффект перемещения).
var vanish := 0.0:
	set(v):
		vanish = v
		for m in all_materials:
			m.set_shader_parameter("vanish", v)
var face_material: ShaderMaterial

var talking := false
var _face_bone := -1
var _talk_timer := 0.0
var _talk_open := false
var _blink_timer := 3.0
var _blink_left := 0.0
var _anim_has_face_track := false
var _forced_face := -1
var _talk_band_time := 0.0
var _last_bone_face := Face.CALM
var _outline_bones: PackedInt32Array = []


func _ready() -> void:
	model = MODEL_SCENE.instantiate()
	add_child(model)
	anim = model.find_child("AnimationPlayer", true, false) as AnimationPlayer
	var skels := model.find_children("*", "Skeleton3D", true, false)
	if skels.size() > 0:
		skeleton = skels[0]
		_face_bone = _find_bone("cc_facecontr")
		_outline_bones = _deform_bones()
	_hide_helper_meshes()
	_setup_materials()
	if anim:
		anim.animation_finished.connect(_on_animation_finished)
		for anim_name in anim.get_animation_list():
			var a := anim.get_animation(anim_name)
			var lname := anim_name.to_lower()
			if lname.begins_with("idle") or lname.begins_with("sleep") or lname.begins_with("pose"):
				a.loop_mode = Animation.LOOP_LINEAR
			_make_face_track_stepped(a)
	play(idle_animation)


## Кости, которые реально двигают меш (по весам вершин). Контроллеры рига,
## IK-цели и прочие вспомогательные кости сюда не попадают — иначе область
## кликов и расчёт позы «раздуваются».
func _deform_bones() -> PackedInt32Array:
	var result := {}
	for mi in model.find_children("*", "MeshInstance3D", true, false):
		var m := mi as MeshInstance3D
		if m.skin == null or m.mesh == null:
			continue
		var binds := {}
		for s in m.mesh.get_surface_count():
			var arrays := m.mesh.surface_get_arrays(s)
			var bones = arrays[Mesh.ARRAY_BONES]
			var weights = arrays[Mesh.ARRAY_WEIGHTS]
			if bones == null or weights == null:
				continue
			for k in bones.size():
				if weights[k] > 0.001:
					binds[int(bones[k])] = true
		for bind in binds.keys():
			if bind >= m.skin.get_bind_count():
				continue
			var b := m.skin.get_bind_bone(bind)
			if b == -1:
				b = skeleton.find_bone(m.skin.get_bind_name(bind))
			if b != -1 and b != _face_bone:
				result[b] = true
	var res := PackedInt32Array()
	for b in result.keys():
		res.append(b)
	if res.is_empty():
		for i in skeleton.get_bone_count():
			if i != _face_bone:
				res.append(i)
	return res


## Прячем всё, что не привязано к скелету: плоскости-референсы, формы
## контроллеров и т.п. (они попадают в файл при обычном экспорте Blender).
func _hide_helper_meshes() -> void:
	for mi in model.find_children("*", "MeshInstance3D", true, false):
		var m := mi as MeshInstance3D
		if m.skin == null and (m.mesh == null or not _mesh_has_bones(m.mesh)):
			m.visible = false


func _mesh_has_bones(mesh: Mesh) -> bool:
	for s in mesh.get_surface_count():
		var arrays := mesh.surface_get_arrays(s)
		if arrays[Mesh.ARRAY_BONES] != null and arrays[Mesh.ARRAY_BONES].size() > 0:
			return true
	return false


## Кость лица должна переключаться скачком (как Constant в Blender),
## иначе при моргании она проезжает через «говорящее» лицо.
func _make_face_track_stepped(a: Animation) -> void:
	for t in a.get_track_count():
		if String(a.track_get_path(t)).ends_with(":cc_facecontr"):
			a.track_set_interpolation_type(t, Animation.INTERPOLATION_NEAREST)


func _find_bone(bone_name: String) -> int:
	var idx := skeleton.find_bone(bone_name)
	if idx == -1:
		idx = skeleton.find_bone(bone_name.replace(".", "_"))
	return idx


# ---------- материалы ----------

func _setup_materials() -> void:
	for mi in model.find_children("*", "MeshInstance3D", true, false):
		var mesh_instance := mi as MeshInstance3D
		var mesh := mesh_instance.mesh
		if mesh == null:
			continue
		for s in mesh.get_surface_count():
			var src := mesh.surface_get_material(s)
			var src_name := src.resource_name if src else ""
			var role: String = MATERIAL_ROLES.get(src_name, "body")
			var mat := _make_material(role, src)
			all_materials.append(mat)
			mesh_instance.set_surface_override_material(s, mat)


func _source_texture(src: Material) -> Texture2D:
	if src is BaseMaterial3D:
		var bm := src as BaseMaterial3D
		if bm.emission_texture:
			return bm.emission_texture
		return bm.albedo_texture
	return null


func _make_material(role: String, src: Material) -> ShaderMaterial:
	var mat := ShaderMaterial.new()
	match role:
		"face":
			mat.shader = FACE_SHADER
			mat.set_shader_parameter("tex", _source_texture(src))
			face_material = mat
		"outline1":
			mat.shader = OUTLINE_SHADER
			mat.set_shader_parameter("outline_color", outline1_color)
		"outline2":
			mat.shader = OUTLINE_SHADER
			mat.set_shader_parameter("outline_color", outline2_color)
		_:
			mat.shader = TOON_SHADER
			mat.set_shader_parameter("tex", _source_texture(src))
			mat.set_shader_parameter("light_dir_world", light_direction)
			if role == "hair":
				mat.set_shader_parameter("stripes_on_dark", 1.0)
			materials.append(mat)
	return mat


func set_light_direction(dir: Vector3) -> void:
	light_direction = dir
	for m in materials:
		m.set_shader_parameter("light_dir_world", dir)


# ---------- анимации ----------

## Сменить позу (зацикленную анимацию). Возвращает false, если такой нет.
func set_pose(pose: String, blend := 0.25) -> bool:
	var found := _resolve_animation(pose) if anim else ""
	if found == "":
		return false
	current_pose = found
	return play(found, blend)


## Играет анимацию по имени. Если такой нет — остаётся idle.
func play(anim_name: String, blend := 0.25) -> bool:
	if anim == null:
		return false
	var found := _resolve_animation(anim_name)
	if found == "":
		return false
	anim.play(found, blend)
	_anim_has_face_track = _has_face_track(anim.get_animation(found))
	return true


func has_animation(anim_name: String) -> bool:
	return anim != null and _resolve_animation(anim_name) != ""


func _resolve_animation(anim_name: String) -> String:
	for a in anim.get_animation_list():
		if a.to_lower() == anim_name.to_lower():
			return a
	return ""


func _has_face_track(a: Animation) -> bool:
	if a == null:
		return false
	for t in a.get_track_count():
		if String(a.track_get_path(t)).ends_with(":cc_facecontr"):
			return true
	return false


func _on_animation_finished(anim_name: StringName) -> void:
	var a := anim.get_animation(anim_name)
	if a and a.loop_mode == Animation.LOOP_NONE:
		play(current_pose)


func start_drag() -> void:
	play("drag", 0.15)


func end_drag() -> void:
	# куда бы её ни поставили — встаёт в обычную позу
	if not position.is_zero_approx():
		position = Vector3.ZERO   # idle и так стоит по центру окна
		set_pose(idle_animation, 0.0)
	else:
		set_pose(idle_animation, 0.2)


## Сдвигает модель так, чтобы текущая поза была по центру окна
## (например, лёжа она длиннее, чем стоя, и иначе вылезла бы за край).
func center_in_view(cam: Camera3D) -> void:
	position = Vector3.ZERO
	var m := pose_metrics(cam)
	if m.is_empty():
		return
	var vp := get_viewport().get_visible_rect().size
	var px_per_unit := vp.y / cam.size
	var bbox: Rect2 = m["bbox"]
	position.x = (vp.x * 0.5 - bbox.get_center().x) / px_per_unit
	# низ позы (с запасом на подошвы и волосы) не должен уходить за край окна
	var overflow := bbox.end.y - (vp.y - 16.0)
	if overflow > 0.0:
		position.y = overflow / px_per_unit


# ---------- лицо ----------

func set_talking(value: bool) -> void:
	if value == talking:
		return
	talking = value
	_talk_timer = 0.0
	_talk_open = false
	# Анимация тела на время речи (если есть «talk»), с плавным переходом.
	if anim == null or not has_animation("talk"):
		return
	var current := anim.current_animation.to_lower()
	if value and (current == "" or current == idle_animation.to_lower()):
		play("talk", talk_blend)
	elif not value and current == "talk":
		play(current_pose, talk_blend)


## Принудительно поставить выражение (-1 = вернуть автоматику).
func force_face(face: int) -> void:
	_forced_face = face


func _process(delta: float) -> void:
	if face_material == null:
		return
	var face := Face.CALM

	# 1) Выражение из анимации: кость cc_facecontr (как Value-нода в Blender)
	if skeleton and _face_bone != -1 and _anim_has_face_track:
		var x := skeleton.get_bone_pose_position(_face_bone).x
		var bone_face := Face.CALM
		if x > 2.0:
			bone_face = Face.BLINK
		elif x > 1.0:
			bone_face = Face.TALK
		# Кость едет плавно (0.85 -> 2.0) и по пути на долю секунды проходит
		# зону «говорит». Такой проскок игнорируем: рот открывается, только если
		# кость задержалась в этой зоне дольше talk_hold_time.
		if bone_face == Face.TALK:
			_talk_band_time += delta
			if _talk_band_time < talk_hold_time:
				bone_face = _last_bone_face if _last_bone_face != Face.TALK else Face.CALM
		else:
			_talk_band_time = 0.0
		_last_bone_face = bone_face
		face = bone_face

	# 2) Своё моргание, если анимация лицом не управляет
	if auto_blink and not _anim_has_face_track:
		_blink_timer -= delta
		if _blink_timer <= 0.0:
			_blink_left = 0.12
			_blink_timer = randf_range(2.5, 6.0)
		if _blink_left > 0.0:
			_blink_left -= delta
			face = Face.BLINK

	# 3) Речь: рот открывается и закрывается
	if talking and face != Face.BLINK:
		_talk_timer -= delta
		if _talk_timer <= 0.0:
			_talk_open = not _talk_open
			_talk_timer = randf_range(0.08, 0.18)
		face = Face.TALK if _talk_open else Face.CALM

	if _forced_face >= 0:
		face = _forced_face as Face

	face_material.set_shader_parameter("face_index", face)


# ---------- для сквозных кликов ----------

## Точки скелета в мировых координатах: из них строится область, куда можно кликать.
func get_outline_points_world() -> PackedVector3Array:
	var pts := PackedVector3Array()
	if skeleton == null:
		return pts
	var xf := skeleton.global_transform
	for i in _outline_bones:
		pts.append(xf * skeleton.get_bone_global_pose(i).origin)
	return pts


## Где на экране окна сейчас персонаж (в пикселях окна):
##   bbox — прямоугольник по костям; head — голова; seat — точка сидения.
func pose_metrics(cam: Camera3D) -> Dictionary:
	var res := {}
	if skeleton == null:
		return res
	var xf := skeleton.global_transform
	var pts := PackedVector2Array()
	for i in _outline_bones:
		pts.append(cam.unproject_position(xf * skeleton.get_bone_global_pose(i).origin))
	var r := Rect2(pts[0], Vector2.ZERO)
	for p in pts:
		r = r.expand(p)
	res["bbox"] = r
	var head := _find_bone("head.x")
	res["head"] = cam.unproject_position(xf * skeleton.get_bone_global_pose(head).origin) if head != -1 else Vector2(r.get_center().x, r.position.y)
	var seat_y := -INF
	var seat_x := r.get_center().x
	for bn in ["root.x", "thigh_stretch.l", "thigh_stretch.r"]:
		var b := _find_bone(bn)
		if b != -1:
			var sp := cam.unproject_position(xf * skeleton.get_bone_global_pose(b).origin)
			seat_y = maxf(seat_y, sp.y)
			if bn == "root.x":
				seat_x = sp.x
	res["seat"] = Vector2(seat_x, seat_y if seat_y > -INF else r.end.y)
	return res
