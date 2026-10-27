// Контур (inverted hull). Оболочка из Blender уже раздута и вывернута,
// поэтому достаточно рисовать её одним цветом с отсечением передних граней.
shader_type spatial;
render_mode unshaded, cull_back, depth_draw_opaque;
#include "res://shaders/vanish.gdshaderinc"

uniform vec4 outline_color : source_color = vec4(0.0, 0.0, 0.0, 1.0);

void vertex() {
	VERTEX = vanish_vertex(VERTEX, TIME);
}

void fragment() {
	float vm = vanish_mask(FRAGCOORD.xy);
	if (vm < 0.0) {
		discard;
	}
	ALBEDO = vanish_tint(outline_color.rgb, FRAGCOORD.xy, vm);
}
