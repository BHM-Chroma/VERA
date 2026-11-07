class_name SpeechBubble
extends PanelContainer
## Окошко с репликой над головой персонажа: прямоугольник со срезанными углами,
## пиксельный шрифт и текст, который «печатается» по буквам.

const FONT_PATH := "res://assets/fonts/digit_tech_16.ttf"
const BG := Color("#1b1622f2")
const BORDER := Color("#d31e5a")
const ACCENT := Color("#ff5aaa")
const TEXT := Color("#f4e8ff")
const CUT := 12.0               ## размер срезанного угла (левый верхний и правый нижний)

@export var max_width := 380.0
@export var top_margin := 8.0
## Скорость печати (букв в секунду). Длинные фразы печатаются быстрее — не дольше type_max_time.
@export var chars_per_second := 45.0
@export var type_max_time := 2.2

var _label: Label
var _time_left := 0.0
var _typing := false
var _shown_chars := 0.0
var _cps := 45.0
var _blink := 0.0

static var _font: Font = null
static var _font_checked := false


## Шрифт «табло» Digit Tech 16 (если он есть в сборке). В нём нет части строчных русских букв,
## поэтому текст с ним показываем ЗАГЛАВНЫМИ — как на электронном табло.
static func pixel_font() -> Font:
	if not _font_checked:
		_font_checked = true
		if ResourceLoader.exists(FONT_PATH):
			_font = load(FONT_PATH)
	return _font


## Текст для табло: заглавные, а кавычек-«ёлочек» и длинного тире в шрифте нет.
static func lcd_text(text: String) -> String:
	if pixel_font() == null:
		return text
	return text.to_upper().replace("«", "\"").replace("»", "\"").replace("—", "-").replace("–", "-")


func _ready() -> void:
	var style := StyleBoxEmpty.new()     # рамку рисуем сами в _draw()
	style.content_margin_left = 14
	style.content_margin_right = 14
	style.content_margin_top = 10
	style.content_margin_bottom = 10
	add_theme_stylebox_override("panel", style)

	_label = Label.new()
	_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_LEFT
	_label.visible_characters_behavior = TextServer.VC_CHARS_AFTER_SHAPING   # размер не прыгает при печати
	_label.add_theme_color_override("font_color", TEXT)
	var f := pixel_font()
	if f:
		_label.add_theme_font_override("font", f)
		_label.add_theme_font_size_override("font_size", 20)
		_label.add_theme_constant_override("line_spacing", 4)
	else:
		_label.add_theme_font_size_override("font_size", 15)
	add_child(_label)
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	visible = false


## Показать реплику. duration <= 0 — висит, пока не вызовут hide_bubble().
## Время показа отсчитывается после того, как текст допечатался.
func say(text: String, duration := 4.0) -> void:
	text = lcd_text(text)
	_label.text = text
	_label.custom_minimum_size.x = 0
	visible = true
	# подбираем ширину: короткие фразы — узкое окошко, длинные — переносятся
	var font := _label.get_theme_font("font")
	var fsize := _label.get_theme_font_size("font_size")
	var text_w := font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, fsize).x
	_label.custom_minimum_size.x = min(text_w + 4.0, max_width - 28.0)
	reset_size()
	_time_left = duration
	var n := text.length()
	if text == "…" or n <= 1:
		_typing = false
		_label.visible_characters = -1
	else:
		_typing = true
		_shown_chars = 0.0
		_cps = maxf(chars_per_second, n / type_max_time)
		_label.visible_characters = 0
	queue_redraw()


func hide_bubble() -> void:
	visible = false
	_typing = false


## Ставит окошко над точкой anchor (низ окошка — у anchor), не вылезая ни за
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
	if not visible:
		return
	if _typing:
		_shown_chars += _cps * delta
		var total := _label.get_total_character_count()
		if _shown_chars >= total:
			_typing = false
			_label.visible_characters = -1
		else:
			_label.visible_characters = int(_shown_chars)
		_blink += delta
		queue_redraw()
		return
	if _time_left > 0.0:
		_time_left -= delta
		if _time_left <= 0.0:
			visible = false


func _draw() -> void:
	var w := size.x
	var h := size.y
	var c := minf(CUT, minf(w, h) * 0.35)
	# прямоугольник со срезанными левым верхним и правым нижним углами
	var pts := PackedVector2Array([
		Vector2(c, 0), Vector2(w, 0), Vector2(w, h - c), Vector2(w - c, h), Vector2(0, h), Vector2(0, c)])
	draw_colored_polygon(pts, BG)
	var outline := pts.duplicate()
	outline.append(pts[0])
	draw_polyline(outline, BORDER, 2.0)
	# «технические» засечки у срезов
	draw_line(Vector2(3, c + 3), Vector2(c + 3, 3), ACCENT, 2.0)
	draw_line(Vector2(w - c - 3, h - 3), Vector2(w - 3, h - c - 3), ACCENT, 2.0)
	# маленькие метки на свободных углах
	draw_rect(Rect2(w - 7, 3, 4, 4), ACCENT)
	draw_rect(Rect2(3, h - 7, 4, 4), ACCENT)
	# мигающий курсор внизу, пока текст печатается
	if _typing and fmod(_blink, 0.5) < 0.3:
		draw_rect(Rect2(w - c - 14, h - 8, 8, 3), ACCENT)
