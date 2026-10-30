// Нота с глитчем: появляется и исчезает «помехами» — полосы съезжают, цвета расслаиваются.
shader_type spatial;
render_mode unshaded, cull_disabled, depth_test_disabled;

uniform sampler2D tex : source_color, filter_nearest;
instance uniform vec4 tint = vec4(1.0);
instance uniform float glitch = 0.0;   // 0 — чистая нота, 1 — полностью рассыпалась

float h(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

void fragment() {
	vec2 uv = UV;
	float t = floor(TIME * 20.0);
	float band = floor(uv.y * 6.0);
	if (h(vec2(band, t)) < glitch * 0.8) {
		uv.x += (h(vec2(band, t + 3.0)) - 0.5) * 0.6 * glitch;
	}
	float split = 0.08 * glitch;
	vec4 c = texture(tex, uv);
	vec4 cr = texture(tex, uv + vec2(split, 0.0));
	vec4 cb = texture(tex, uv - vec2(split, 0.0));
	float a = max(c.a, max(cr.a, cb.a));
	vec3 col = vec3(cr.r, c.g, cb.b) * tint.rgb;
	// пропадающие полоски при сильном глитче
	if (h(vec2(band, t + 9.0)) < glitch * glitch) {
		a = 0.0;
	}
	if (a < 0.5) {
		discard;
	}
	ALBEDO = col;
}
