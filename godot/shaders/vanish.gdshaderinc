// Эффект исчезновения/появления при перемещении персонажа.
// vanish = 0 — персонаж виден целиком, 1 — полностью рассыпался.
// Подключается во все шейдеры персонажа через #include.

uniform float vanish : hint_range(0.0, 1.0) = 0.0;
uniform vec4 vanish_color : source_color = vec4(1.0, 0.12, 0.45, 1.0);
uniform vec4 vanish_color2 : source_color = vec4(0.45, 0.2, 1.0, 1.0);
uniform float vanish_pixel = 4.0;   // размер «пикселей» рассыпания (в пикселях экрана)

float vanish_hash(vec2 p) {
	return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

// Глитч: горизонтальные полосы модели дёргаются в стороны.
vec3 vanish_vertex(vec3 v, float t) {
	if (vanish <= 0.0) {
		return v;
	}
	float band = floor(v.y * 10.0);
	float r = vanish_hash(vec2(band, floor(t * 24.0)));
	v.x += (r - 0.5) * 0.8 * vanish * step(0.55, r);
	return v;
}

// -1.0 — пиксель исчез (discard), иначе сила цветного свечения на краю.
float vanish_mask(vec2 frag) {
	if (vanish <= 0.0) {
		return 0.0;
	}
	float n = vanish_hash(floor(frag / vanish_pixel));
	float edge = n - vanish * 1.2 + 0.1;
	if (edge < 0.0) {
		return -1.0;
	}
	return 1.0 - smoothstep(0.0, 0.18, edge);
}

vec3 vanish_tint(vec3 col, vec2 frag, float glow) {
	vec3 neon = mix(vanish_color.rgb, vanish_color2.rgb, step(0.5, fract(frag.y / (vanish_pixel * 2.0))));
	col = mix(col, neon * 1.6, glow);
	return mix(col, vanish_color.rgb, vanish * 0.25);
}
