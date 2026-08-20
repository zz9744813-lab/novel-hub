import { useEffect, useRef } from "react";

// Interactive fluid field: fbm domain warp + cursor swirl + click ripple.
// Zero dependencies (raw WebGL1). Falls back silently when WebGL is missing;
// renders a single static frame under prefers-reduced-motion.

const VERT = `
attribute vec2 a_pos;
void main() {
  gl_Position = vec4(a_pos, 0.0, 1.0);
}
`;

const FRAG = `
precision highp float;
uniform vec2 u_res;
uniform float u_time;
uniform vec2 u_mouse;
uniform float u_click_age;
uniform vec2 u_click_pos;

float hash(vec2 p) {
  p = fract(p * vec2(234.34, 435.345));
  p += dot(p, p + 34.23);
  return fract(p.x * p.y);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float a = hash(i);
  float b = hash(i + vec2(1.0, 0.0));
  float c = hash(i + vec2(0.0, 1.0));
  float d = hash(i + vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float fbm(vec2 p) {
  float v = 0.0;
  float amp = 0.5;
  mat2 rot = mat2(0.8, 0.6, -0.6, 0.8);
  for (int i = 0; i < 4; i++) {
    v += amp * noise(p);
    p = rot * p * 2.02;
    amp *= 0.5;
  }
  return v;
}

void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * u_res) / min(u_res.x, u_res.y);
  float t = u_time * 0.06;

  // Cursor in the same normalized space, then swirl + pull the field around it
  vec2 mp = (u_mouse * u_res - 0.5 * u_res) / min(u_res.x, u_res.y);
  vec2 dvec = p - mp;
  float md = length(dvec);
  float infl = exp(-md * 2.6);
  vec2 perp = vec2(-dvec.y, dvec.x);
  p += perp * infl * 0.32;
  p += dvec * infl * 0.10;

  // Click ripple: expanding ring that displaces the field
  vec2 cp = (u_click_pos * u_res - 0.5 * u_res) / min(u_res.x, u_res.y);
  float cd = length(p - cp);
  float ring = exp(-u_click_age * 1.6)
    * sin(cd * 22.0 - u_click_age * 9.0)
    * exp(-abs(cd - u_click_age * 0.55) * 5.0);
  p += normalize(p - cp + vec2(1e-4)) * ring * 0.06;

  // Double domain warp -> flowing structure
  vec2 q = vec2(fbm(p + vec2(0.0, t)), fbm(p + vec2(5.2, 1.3) - t));
  vec2 r = vec2(
    fbm(p + 1.7 * q + vec2(1.7, 9.2) + 0.35 * t),
    fbm(p + 1.7 * q + vec2(8.3, 2.8) - 0.26 * t)
  );
  float f = fbm(p + 1.9 * r);

  vec3 base   = vec3(0.047, 0.051, 0.063);  // #0c0d10 canvas
  vec3 deep   = vec3(0.086, 0.098, 0.155);  // dark indigo pool
  vec3 brand  = vec3(0.420, 0.478, 1.000);  // #6b7aff
  vec3 accent = vec3(0.545, 0.557, 1.000);  // #8b8eff

  vec3 col = base;
  col = mix(col, deep, clamp(f * 1.6, 0.0, 1.0));
  float hi = smoothstep(0.42, 0.95, f + 0.22 * length(r));
  col = mix(col, brand * 0.36, hi * 0.55);
  col += accent * pow(hi, 3.0) * 0.16;

  // Faint cursor glow + ripple shimmer, then calm the edges with a vignette
  col += brand * infl * 0.05;
  col += accent * max(ring, 0.0) * 0.25;
  float vg = smoothstep(1.35, 0.35, length(p));
  col *= mix(0.75, 1.0, vg);

  gl_FragColor = vec4(col, 1.0);
}
`;

export function FluidBackground({ opacity = 1 }: { opacity?: number }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const gl = canvas.getContext("webgl", {
      antialias: false,
      alpha: false,
      depth: false,
      stencil: false,
      powerPreference: "low-power",
    });
    // StrictMode remounts return the same canvas; a lost context can never be
    // revived via getContext, so bail out instead of painting with dead calls.
    if (!gl || gl.isContextLost()) return;

    const compile = (type: number, src: string) => {
      const shader = gl.createShader(type);
      if (!shader) return null;
      gl.shaderSource(shader, src);
      gl.compileShader(shader);
      return shader;
    };
    const vs = compile(gl.VERTEX_SHADER, VERT);
    const fs = compile(gl.FRAGMENT_SHADER, FRAG);
    const prog = gl.createProgram();
    if (!vs || !fs || !prog) return;
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const posLoc = gl.getAttribLocation(prog, "a_pos");
    gl.enableVertexAttribArray(posLoc);
    gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0);

    const uRes = gl.getUniformLocation(prog, "u_res");
    const uTime = gl.getUniformLocation(prog, "u_time");
    const uMouse = gl.getUniformLocation(prog, "u_mouse");
    const uClickAge = gl.getUniformLocation(prog, "u_click_age");
    const uClickPos = gl.getUniformLocation(prog, "u_click_pos");

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // Render below native resolution: the fluid look tolerates (and benefits from) upscale blur
    const scale = 0.75 * Math.min(window.devicePixelRatio || 1, 1.25);
    const resize = () => {
      const w = Math.max(1, Math.floor(window.innerWidth * scale));
      const h = Math.max(1, Math.floor(window.innerHeight * scale));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }
      gl.viewport(0, 0, w, h);
    };
    resize();

    const mouse = { x: 0.5, y: 0.5, tx: 0.5, ty: 0.5 };
    const clickPos = { x: 0.5, y: 0.5 };
    let clickAge = 100;
    const t0 = performance.now();
    let last = t0;
    let raf = 0;

    const draw = (time: number) => {
      gl.uniform2f(uRes, canvas.width, canvas.height);
      gl.uniform1f(uTime, time);
      gl.uniform2f(uMouse, mouse.x, mouse.y);
      gl.uniform1f(uClickAge, clickAge);
      gl.uniform2f(uClickPos, clickPos.x, clickPos.y);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };

    const frame = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      clickAge += dt;
      mouse.x += (mouse.tx - mouse.x) * 0.07;
      mouse.y += (mouse.ty - mouse.y) * 0.07;
      draw((now - t0) / 1000);
      raf = requestAnimationFrame(frame);
    };

    const onMove = (e: PointerEvent) => {
      mouse.tx = e.clientX / window.innerWidth;
      mouse.ty = 1 - e.clientY / window.innerHeight;
    };
    const onDown = (e: PointerEvent) => {
      clickAge = 0;
      clickPos.x = e.clientX / window.innerWidth;
      clickPos.y = 1 - e.clientY / window.innerHeight;
    };
    const onVis = () => {
      if (document.hidden) {
        cancelAnimationFrame(raf);
        raf = 0;
      } else if (!raf && !reduced) {
        last = performance.now();
        raf = requestAnimationFrame(frame);
      }
    };

    window.addEventListener("resize", resize);
    if (!reduced) {
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerdown", onDown);
      document.addEventListener("visibilitychange", onVis);
      raf = requestAnimationFrame(frame);
    } else {
      draw(0);
    }

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerdown", onDown);
      document.removeEventListener("visibilitychange", onVis);
      // Note: no loseContext() here — React StrictMode remounts reuse this
      // canvas, and a lost context cannot be re-acquired on the same node.
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="fluid-bg"
      style={{
        position: "fixed",
        inset: 0,
        width: "100%",
        height: "100%",
        pointerEvents: "none",
        zIndex: 0,
        opacity,
      }}
    />
  );
}
