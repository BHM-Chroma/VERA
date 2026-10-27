// Мультяшный шейдер тела и волос.
// Повторяет логику нод из Blender:
//   текстура (Emission 3.0, Mix 0.5)  -> base * tex_strength
//   Shader to RGB + ColorRamp(Constant) -> ступенчатое освещение
//   Wave Texture (Rings, 45°)           -> диагональная штриховка в тенях
// Все значения можно крутить в инспекторе Godot прямо во время игры.
shader_type spatial;
render_mode unshaded, cull_disabled, depth_draw_opaque;
#include "res://shaders/vanish.gdshaderinc"

uniform sampler2D tex : source_color, filter_linear_mipmap, repeat_enable;
uniform float tex_strength = 1.5;

// Направление НА источник света, в мировых координатах (задаётся из character.gd)
uniform vec3 light_dir_world = vec3(-0.45, 0.75, 0.6);
uniform float ambient = 0.1;

// Светлая ступень (в Blender: ColorRamp, позиция 0.838)
uniform float highlight_threshold = 0.838;
uniform vec4 highlight_color : source_color = vec4(0.35, 0.08, 0.38, 1.0);
uniform float highlight_strength = 0.35;

// Тень (всё, что темнее порога)
uniform float shadow_threshold = 0.15;
uniform float shadow_darken = 0.25;

// Штриховка (в Blender: Wave Texture, Scale 25, поворот 45°)
uniform vec4 stripe_color : source_color = vec4(0.55, 0.05, 0.2, 1.0);
uniform float stripe_strength = 0.45;
uniform float stripe_spacing_px = 5.0;
uniform float stripe_width = 0.3;
uniform float stripe_angle_deg = 45.0;
// 1.0 = штриховка ещё и на чёрных участках текстуры (как на волосах в рендере)
uniform float stripes_on_dark = 0.0;

void vertex() {
	VERTEX = vanish_vertex(VERTEX, TIME);
}

void fragment() {
	float vm = vanish_mask(FRAGCOORD.xy);
	if (vm < 0.0) {
		discard;
	}
	vec3 base = texture(tex, UV).rgb;
	vec3 n = normalize((INV_VIEW_MATRIX * vec4(NORMAL, 0.0)).xyz);
	float l = clamp(dot(n, normalize(light_dir_world)), 0.0, 1.0) + ambient;

	vec3 col = base * tex_strength;

	float lit = step(highlight_threshold, l);
	col += highlight_color.rgb * highlight_strength * lit;

	float shadow = 1.0 - step(shadow_threshold, l);
	col *= 1.0 - shadow * shadow_darken;

	float a = radians(stripe_angle_deg);
	float coord = dot(FRAGCOORD.xy, vec2(cos(a), sin(a))) / stripe_spacing_px;
	float stripe = step(1.0 - stripe_width, fract(coord));
	float dark_tex = 1.0 - step(0.04, max(base.r, max(base.g, base.b)));
	float stripe_mask = max(shadow, dark_tex * stripes_on_dark);
	col = mix(col, stripe_color.rgb, stripe * stripe_mask * stripe_strength);

	ALBEDO = vanish_tint(col, FRAGCOORD.xy, vm);
}
