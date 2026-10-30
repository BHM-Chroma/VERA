// Глитч поверх окна персонажа: берёт уже нарисованную картинку (её, плиту, ноты)
// и ломает её — сдвиг полос, расслоение цветов, битые блоки, сканлайны.
// amount  — сила помех (0 — чисто), vanish — исчезновение (1 — её не видно).
shader_type canvas_item;

uniform sampler2D screen_tex : hint_screen_texture, filter_nearest;
uniform float amount : hint_range(0.0, 1.0) = 0.0;
uniform float vanish : hint_range(0.0, 1.0) = 0.0;
uniform int style = 0;                 // 0 — полосы+RGB, 1 — битые блоки, 2 — сигнал
uniform vec4 neon1 : source_color = vec4(1.0, 0.12, 0.45, 1.0);
uniform vec4 neon2 : source_color = vec4(0.45, 0.2, 1.0, 1.0);
uniform float fps = 18.0;              // как часто «перещёлкиваются» помехи

float h(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

void fragment() {
	vec2 px = SCREEN_PIXEL_SIZE;
	vec2 uv = SCREEN_UV;
	float t = floor(TIME * fps);
	float k = max(amount, vanish);
	vec2 frag = uv / px;

	// 1) сдвиг горизонтальных полос
	float band_h = style == 2 ? 3.0 : (style == 1 ? 14.0 : 8.0);
	float band = floor(frag.y / band_h);
	float r = h(vec2(band, t));
	float shift = 0.0;
	if (r < 0.25 * k + 0.02 * k) {
		shift = (h(vec2(band, t + 7.0)) - 0.5) * 90.0 * k;
	}
	if (style == 2) {               // «сигнал»: вся картинка подрагивает целиком
		shift += (h(vec2(t, 3.0)) - 0.5) * 18.0 * k;
	}
	uv.x += shift * px.x;

	// 2) битые блоки: прямоугольники съезжают или пропадают
	vec2 blk = floor(frag / vec2(style == 1 ? 34.0 : 22.0, style == 1 ? 18.0 : 10.0));
	float rb = h(blk + t * 1.7);
	bool broken = rb < (style == 1 ? 0.22 : 0.08) * k;
	if (broken) {
		uv += (vec2(h(blk + t), h(blk - t)) - 0.5) * vec2(60.0, 6.0) * px * k;
	}

	// 3) расслоение цветов (красный и синий разъезжаются)
	float split = (style == 2 ? 6.0 : 4.0) * k + 1.5 * k * h(vec2(t, 1.0));
	vec4 c = texture(screen_tex, uv);
	vec4 cr = texture(screen_tex, uv + vec2(split, 0.0) * px);
	vec4 cb = texture(screen_tex, uv - vec2(split, 0.0) * px);
	vec4 col = vec4(cr.r, c.g, cb.b, max(c.a, max(cr.a, cb.a)));

	// 4) неоновые вспышки в битых блоках
	if (broken && col.a > 0.0) {
		col.rgb = mix(col.rgb, (h(blk) < 0.5 ? neon1.rgb : neon2.rgb) * 1.4, 0.55);
	}

	// 5) сканлайны
	if (style == 2 || k > 0.0) {
		float line = mod(frag.y, 3.0) < 1.0 ? 1.0 : 0.0;
		col.rgb *= 1.0 - line * 0.25 * k;
	}

	// 6) исчезновение: блоки и полосы гаснут, пока не пропадёт всё
	if (vanish > 0.0) {
		vec2 vb = floor(frag / vec2(26.0, 6.0));
		float rv = h(vb + floor(TIME * 30.0) * 0.01);
		float gone = step(rv, vanish * 1.15 - 0.1);
		col.a *= 1.0 - gone;
		col.a *= 1.0 - smoothstep(0.85, 1.0, vanish);
	}
	COLOR = col;
}
