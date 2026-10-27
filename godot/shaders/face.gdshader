// Лицо: атлас из трёх выражений.
//   0 = спокойствие (левая верхняя клетка)
//   1 = говорит     (левая средняя клетка)
//   2 = моргание    (правая верхняя клетка)
// face_index меняет character.gd: по кости cc_facecontr из анимации или из кода.
shader_type spatial;
render_mode unshaded, cull_disabled, depth_draw_opaque;
#include "res://shaders/vanish.gdshaderinc"

uniform sampler2D tex : source_color, filter_linear_mipmap, repeat_enable;
uniform int face_index = 0;
uniform vec2 offset_talk = vec2(0.006, 0.361);
uniform vec2 offset_blink = vec2(0.488, 0.0);
uniform float tex_strength = 1.2;
uniform float alpha_cut = 0.5;

void vertex() {
	VERTEX = vanish_vertex(VERTEX, TIME);
}

void fragment() {
	float vm = vanish_mask(FRAGCOORD.xy);
	if (vm < 0.0) {
		discard;
	}
	vec2 off = vec2(0.0);
	if (face_index == 1) {
		off = offset_talk;
	} else if (face_index == 2) {
		off = offset_blink;
	}
	vec4 c = texture(tex, UV + off);
	if (c.a < alpha_cut) {
		discard;
	}
	ALBEDO = vanish_tint(c.rgb * tex_strength, FRAGCOORD.xy, vm);
}
