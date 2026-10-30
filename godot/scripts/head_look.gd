class_name HeadLook
extends SkeletonModifier3D
## Голова поворачивается к курсору, когда он рядом (в пределах radius пикселей).
## Работает поверх анимации: берёт позу из анимации и чуть доворачивает голову.

## Кость, которую поворачиваем. c_head.x — родитель и головы (head.x), и всех прядей волос,
## поэтому голова с волосами поворачиваются одним целым.
@export var bone_name := "c_head.x"
## Радиус, в котором она следит за мышкой (пиксели окна от головы).
@export var radius := 350.0
## «Глубина» взгляда: чем больше, тем меньше поворот на тот же сдвиг курсора.
@export var depth := 420.0
## Предельные углы поворота (градусы).
@export var max_yaw := 35.0
@export var max_pitch := 15.0
## Скорость доводки головы (больше — резче).
@export var speed := 7.0

## Можно ли сейчас смотреть (выключается во сне, лёжа и т.п.).
var allowed := true

var _bone := -1
var _weight := 0.0
var _yaw := 0.0
var _pitch := 0.0
var _last_ticks := 0


func _ready() -> void:
	var skel := get_skeleton()
	if skel:
		_bone = skel.find_bone(bone_name)
		if _bone == -1:
			_bone = skel.find_bone("head.x")
		if _bone == -1:
			_bone = skel.find_bone("head")
	_last_ticks = Time.get_ticks_msec()


func _process_modification() -> void:
	var skel := get_skeleton()
	var cam := get_viewport().get_camera_3d() if is_inside_tree() else null
	if skel == null or cam == null or _bone == -1:
		return
	var now := Time.get_ticks_msec()
	var dt := clampf((now - _last_ticks) / 1000.0, 0.0, 0.1)
	_last_ticks = now

	var head_pose := skel.get_bone_global_pose(_bone)
	var head_screen := cam.unproject_position(skel.global_transform * head_pose.origin)
	var mouse := Vector2(DisplayServer.mouse_get_position() - DisplayServer.window_get_position())
	var d := mouse - head_screen
	var dist := d.length()

	# внутри радиуса — смотрит полностью, чуть дальше — плавно отпускает
	var target_w := 0.0
	if allowed:
		target_w = 1.0 - smoothstep(radius, radius * 1.3, dist)
	var target_yaw := clampf(atan2(d.x, depth), -deg_to_rad(max_yaw), deg_to_rad(max_yaw))
	var target_pitch := clampf(atan2(d.y, depth), -deg_to_rad(max_pitch), deg_to_rad(max_pitch))
	var k := 1.0 - exp(-speed * dt)
	_weight = lerpf(_weight, target_w, k)
	_yaw = lerpf(_yaw, target_yaw, k)
	_pitch = lerpf(_pitch, target_pitch, k)
	if _weight < 0.005:
		return

	# поворот в мировых осях (лицо смотрит на камеру, +Z): Y — влево/вправо, X — вверх/вниз
	var rot_world := Basis(Vector3.UP, _yaw * _weight) * Basis(Vector3.RIGHT, _pitch * _weight)
	var xb := skel.global_transform.basis              # учитывает и зеркало (scale.x = -1)
	var rot_skel := xb.inverse() * rot_world * xb
	var new_global := rot_skel * head_pose.basis
	var parent := skel.get_bone_parent(_bone)
	var parent_basis := skel.get_bone_global_pose(parent).basis if parent != -1 else Basis()
	var local := (parent_basis.inverse() * new_global).orthonormalized()
	skel.set_bone_pose_rotation(_bone, local.get_rotation_quaternion())
