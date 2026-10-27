class_name SpeechBubble
extends PanelContainer
## Облачко с репликой над головой персонажа.

@export var max_width := 330.0
@export var top_margin := 8.0

var _label: Label
var _time_left := 0.0


func _ready() -> void:
	var style := StyleBoxFlat.new()
	style.bg_color = Color("#2a2530f0")
	style.border_color = Color("#d31e5a")
	style.set_border_width_all(2)
	style.set_corner_radius_all(12)
	style.content_margin_left = 12
	style.content_margin_right = 12
	style.content_margin_top = 8
	style.content_margin_bottom = 8
	add_theme_stylebox_override("panel", style)

	_label = Label.new()
	_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_label.add_theme_color_override("font_color", Color("#f4e8ff"))
	_label.add_theme_font_size_override("font_size", 15)
	add_child(_label)
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	visible = false


## Показать реплику. duration <= 0 — висит, пока не вызовут hide_bubble().
func say(text: String, duration := 4.0) -> void:
	_label.text = text
	_label.custom_minimum_size.x = 0
	visible = true
	# подбираем ширину: короткие фразы — узкое облачко, длинные — переносятся
	var font := _label.get_theme_font("font")
	var fsize := _label.get_theme_font_size("font_size")
	var text_w := font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, fsize).x
	_label.custom_minimum_size.x = min(text_w + 4.0, max_width - 24.0)
	reset_size()
	_time_left = duration


func hide_bubble() -> void:
	visible = false


## Ставит облачко над точкой anchor (низ облачка — у anchor), не вылезая ни за
## окно, ни за край экрана (visible — видимая на экране часть окна).
func place_above(anchor: Vector2, visible := Rect2(-1e6, -1e6, 2e6, 2e6)) -> void:
	reset_size()
	var area := get_viewport_rect().intersection(visible)
	if area.size.x < size.x + 8.0:
		area = get_viewport_rect()
	var x := clampf(anchor.x - size.x * 0.5, area.position.x + 4.0, area.end.x - size.x - 4.0)
	var y := clampf(anchor.y - size.y, maxf(top_margin, area.position.y + 4.0), area.end.y - size.y - 4.0)
	position = Vector2(x, y)


func _process(delta: float) -> void:
	if visible and _time_left > 0.0:
		_time_left -= delta
		if _time_left <= 0.0:
			visible = false
