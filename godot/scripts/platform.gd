class_name Platform
extends Node3D
## Неоновая шестигранная плита под ней, пока её несут мышкой.
## Появляется и исчезает тем же пиксельным эффектом, что и перемещение.

const SHADER := preload("res://shaders/platform.gdshader")

@export var radius := 0.62
@export var thickness := 0.09
## Наклон к камере, чтобы был виден верх плиты (градусы).
@export var tilt := 12.0
@export var fade_time := 0.25

var vanish := 1.0:
	set(v):
		vanish = v
		if _mat:
			_mat.set_shader_parameter("vanish", v)
		if _dust:
			_dust.emitting = v < 0.5

var _mat: ShaderMaterial
var _mesh: CylinderMesh
var _dust: CPUParticles3D
var _tween: Tween


func _ready() -> void:
	_mesh = CylinderMesh.new()
	_mesh.height = thickness
	_mesh.radial_segments = 6
	_mesh.rings = 1
	_mat = ShaderMaterial.new()
	_mat.shader = SHADER
	_mat.set_shader_parameter("thickness", thickness)
	set_radius(radius)
	var mi := MeshInstance3D.new()
	mi.mesh = _mesh
	mi.material_override = _mat
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	mi.rotation_degrees = Vector3(tilt, 30.0, 0.0)
	add_child(mi)
	_build_dust()
	vanish = 1.0
	visible = false


## Пиксельная «пыль», осыпающаяся из-под плиты.
func _build_dust() -> void:
	_dust = CPUParticles3D.new()
	_dust.amount = 18
	_dust.lifetime = 0.8
	_dust.local_coords = false
	_dust.emission_shape = CPUParticles3D.EMISSION_SHAPE_BOX
	_dust.emission_box_extents = Vector3(radius * 0.8, 0.02, 0.1)
	_dust.position = Vector3(0, -thickness, 0)
	_dust.direction = Vector3.DOWN
	_dust.spread = 12.0
	_dust.gravity = Vector3(0, -0.6, 0)
	_dust.initial_velocity_min = 0.15
	_dust.initial_velocity_max = 0.45
	_dust.scale_amount_min = 0.6
	_dust.scale_amount_max = 1.2
	var fade := Curve.new()
	fade.add_point(Vector2(0, 1))
	fade.add_point(Vector2(1, 0))
	_dust.scale_amount_curve = fade
	var colors := Gradient.new()
	colors.set_color(0, Color(1.0, 0.12, 0.45))
	colors.set_color(1, Color(0.45, 0.2, 1.0))
	_dust.color_initial_ramp = colors
	var quad := QuadMesh.new()
	quad.size = Vector2(0.06, 0.06)
	var m := StandardMaterial3D.new()
	m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	m.vertex_color_use_as_albedo = true
	m.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	quad.material = m
	_dust.mesh = quad
	_dust.emitting = false
	add_child(_dust)


## Размер плиты под позу (радиус в единицах сцены).
func set_radius(r: float) -> void:
	radius = r
	_mesh.top_radius = r
	_mesh.bottom_radius = r
	_mat.set_shader_parameter("radius", r)
	if _dust:
		_dust.emission_box_extents = Vector3(r * 0.8, 0.02, 0.1)


func appear() -> void:
	visible = true
	_fade_to(0.0)


func disappear() -> void:
	await _fade_to(1.0)
	if vanish >= 1.0:
		visible = false


func _fade_to(to: float) -> void:
	if _tween:
		_tween.kill()
	_tween = create_tween()
	_tween.tween_property(self, "vanish", to, fade_time).set_trans(Tween.TRANS_SINE)
	await _tween.finished
