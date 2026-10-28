// Неоновая плита, на которой она сидит, пока её несут мышкой.
// Те же цвета и то же пиксельное рассыпание, что и при перемещении.
shader_type spatial;
render_mode unshaded, cull_disabled, depth_draw_opaque;
#include "res://shaders/vanish.gdshaderinc"

uniform float radius = 0.62;
uniform float thickness = 0.09;

varying vec3 local_pos;

void vertex() {
	local_pos = VERTEX;
	VERTEX = vanish_vertex(VERTEX, TIME);
}

void fragment() {
	float vm = vanish_mask(FRAGCOORD.xy);
	if (vm < 0.0) {
		discard;
	}
	vec2 px = floor(FRAGCOORD.xy / vanish_pixel);
	float r = length(local_pos.xz) / radius;
	float top = step(thickness * 0.45, local_pos.y);        // верхняя грань
	float side = 1.0 - top;

	// основа: от фиолетового в центре к розовому у края
	vec3 col = mix(vanish_color2.rgb, vanish_color.rgb, smoothstep(0.15, 1.0, r));
	col *= mix(0.55, 1.0, top);                               // бок темнее верха
	// светящийся край и внутреннее кольцо
	col += vanish_color.rgb * 1.3 * smoothstep(0.86, 0.98, r) * top;
	col += vanish_color2.rgb * 0.9 * (1.0 - smoothstep(0.0, 0.035, abs(r - 0.62))) * top;
	// бегущие полосы по боку, как у экранов
	col += vanish_color.rgb * 0.6 * side * step(0.5, fract(FRAGCOORD.y / (vanish_pixel * 2.0) - TIME * 2.0));
	// мерцающие «пиксели» по поверхности
	float spark = vanish_hash(px + floor(TIME * 6.0));
	col += vec3(1.0, 0.75, 0.95) * step(0.965, spark) * 0.9;

	ALBEDO = vanish_tint(col, FRAGCOORD.xy, vm);
}
