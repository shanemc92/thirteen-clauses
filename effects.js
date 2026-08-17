/* Thirteen Clauses — visual effects.
   Listens for the events effects.py dispatches and paints an overlay above the
   terminal. The on-screen keyboard sits above this layer (see .kb z-index in
   styles.css) so effects never cover or interfere with it.

   Two kinds of effect arrive:
     held  — detail.persist === true. Runs until thirteen:effect:end with the
             same name. Only reachable now via the `fx <name> hold` debug
             command; every floor effect fires as a burst.
     burst — detail.seconds is real. Most floors send 30, i.e.
             ENTRANCE_EFFECT_SECONDS in step.py; a floor can override that
             with `effect_seconds` in its JSON, and Floor 13's blank sends 10
             because a long drain to white is hard to read through. Ends
             itself, no :end is sent. A floor fires its burst three times at
             most: on arrival, on a load, and once on the room outside the
             boss door.

   Layers stack, so lowhp and static compose during the last fight rather than
   replacing one another. Nothing animates in the 3-30 Hz band, and
   prefers-reduced-motion drops the storm lightning, freezes party, and makes
   sever, static and lowhp still.

   Manual testing, from the console:
     thirteenEffects.fire("sever", { persist: true })
     thirteenEffects.fire("ember", { seconds: 8 })
     thirteenEffects.end("sever")
     thirteenEffects.end()
*/
(() => {
  "use strict";

  const calm = matchMedia("(prefers-reduced-motion: reduce)");
  const isCalm = () => calm.matches;

  const stage = document.createElement("div");
  stage.className = "fx";
  stage.setAttribute("aria-hidden", "true");
  const mount = () => { if (document.body && !stage.isConnected) document.body.appendChild(stage); };
  document.addEventListener("DOMContentLoaded", mount);
  mount();

  const screen = () => document.querySelector(".console__screen");

  const running = new Map();     // name -> teardown fn

  function stop(name) {
    const teardown = running.get(name);
    if (!teardown) return;
    running.delete(name);
    teardown();
  }

  function stopTimed() {
    for (const name of [...running.keys()]) if (name !== "palette") stop(name);
  }

  /** A layer that fades itself out before it is removed. */
  function layer(cls, tag = "div") {
    mount();
    const el = document.createElement(tag);
    el.className = cls;
    if (isCalm()) el.classList.add("is-calm");
    stage.appendChild(el);
    requestAnimationFrame(() => el.classList.add("is-in"));
    return el;
  }

  function retire(el) {
    el.classList.remove("is-in");
    setTimeout(() => el.remove(), 900);
  }

  /** Adds a class to the terminal screen and hands back the remover. */
  function mark(cls) {
    const el = screen();
    if (!el) return () => {};
    el.classList.add(cls);
    return () => el.classList.remove(cls);
  }

  /** Full-viewport canvas that keeps itself sized. `after` runs on every resize. */
  function canvasLayer(cls, after) {
    const el = layer(cls, "canvas");
    const ctx = el.getContext("2d");
    const size = { w: 0, h: 0 };

    let live = false;                 // the first size runs before the caller's state exists
    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      size.w = window.innerWidth;
      size.h = window.innerHeight;
      el.width = Math.round(size.w * dpr);
      el.height = Math.round(size.h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (live && after) after();
    }
    resize();
    live = true;
    window.addEventListener("resize", resize);

    return { el, ctx, size, off: () => window.removeEventListener("resize", resize) };
  }

  /* ---------------- storm: floor 12 entrance burst, floor 7 passing ---------------- */

  function buildStorm(detail) {
    // `persist` only arrives from `fx storm hold` now; Floor 12's floor
    // burst is told apart from Floor 7's passing downpour by duration alone
    // (30s vs 7s - see ENTRANCE_EFFECT_SECONDS in step.py).
    const heavy = !!detail.persist || Number(detail.seconds) >= 10;
    let drops = [], raf = 0, bolt = 0;
    const slant = isCalm() ? 0.10 : 0.28;

    const { el, ctx, size, off } = canvasLayer("fx__canvas", () => stock());
    const flash = layer("fx__flash");

    function seed() {
      return {
        x: Math.random() * (size.w + 160) - 80,
        y: Math.random() * size.h,
        len: (heavy ? 8 : 6) + Math.random() * (heavy ? 18 : 12),
        v: (isCalm() ? 3 : heavy ? 7 : 5) + Math.random() * (heavy ? 9 : 6),
        a: (heavy ? 0.12 : 0.07) + Math.random() * (heavy ? 0.35 : 0.2)
      };
    }

    function stock() {
      const density = isCalm() ? 4200 : heavy ? 3400 : 6500;
      const cap = isCalm() ? 70 : heavy ? 420 : 220;
      drops = Array.from({ length: Math.min(cap, Math.round((size.w * size.h) / density)) }, seed);
    }
    stock();

    function frame() {
      ctx.clearRect(0, 0, size.w, size.h);
      ctx.lineWidth = 1.1;
      ctx.lineCap = "round";
      for (const d of drops) {
        ctx.strokeStyle = `rgba(176, 198, 224, ${d.a})`;
        ctx.beginPath();
        ctx.moveTo(d.x, d.y);
        ctx.lineTo(d.x + d.len * slant, d.y + d.len);
        ctx.stroke();
        d.y += d.v;
        d.x += d.v * slant;
        if (d.y > size.h) { Object.assign(d, seed()); d.y = -d.len; }
      }
      raf = requestAnimationFrame(frame);
    }
    raf = requestAnimationFrame(frame);

    // Lightning is a slow fade, never a strobe, and is dropped entirely when
    // the visitor has asked for reduced motion.
    function strike() {
      if (isCalm()) return;
      flash.classList.add("is-lit");
      setTimeout(() => flash.classList.remove("is-lit"), 180);
      bolt = setTimeout(strike, (heavy ? 2600 : 4200) + Math.random() * 4200);
    }
    bolt = setTimeout(strike, (heavy ? 900 : 2200) + Math.random() * 1800);

    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(bolt);
      off();
      retire(el);
      retire(flash);
    };
  }

  /* ---------------- party: the SING easter egg ---------------- */

  function buildParty() {
    const el = layer("fx__party");
    for (let i = 0; i < 4; i++) el.appendChild(document.createElement("i"));
    return () => retire(el);
  }

  /* ---------------- floor cleared ---------------- */

  function buildFloorCleared() {
    const el = layer("fx__cleared");
    el.appendChild(document.createElement("i"));
    return () => retire(el);
  }

  /* ---------------- cold: floor 5, the catacomb ---------------- */

  function buildCold() {
    const el = layer("fx__cold");
    const unmark = mark("fx-cold");
    return () => { unmark(); retire(el); };
  }

  /* ---------------- sever: floor 9, the page cut in half ---------------- */

  function buildSever() {
    const el = layer("fx__sever");
    // Two halves ruled with the same fine lines, three pixels out of step, and
    // a seam down the middle where they fail to meet.
    el.innerHTML = '<i class="fx__sever-l"></i><i class="fx__sever-r"></i><b></b>';
    return () => retire(el);
  }

  /* ---------------- session: floor 10, court is sitting ---------------- */

  function buildSession() {
    const el = layer("fx__session");
    return () => retire(el);
  }

  /* ---------------- amend: floor 11 rewrites a room ---------------- */

  function buildAmend() {
    // Fires on roughly half of all revisits, so this is one element and one
    // transform: no canvas, no listeners, nothing to garbage collect.
    const el = layer("fx__amend");
    el.appendChild(document.createElement("i"));
    return () => retire(el);
  }

  /* ---------------- ember: the Reaper of Records dies ---------------- */

  function buildEmber() {
    const { el, ctx, size, off } = canvasLayer("fx__canvas fx__ember");
    let raf = 0;

    const count = isCalm() ? 40 : 110;
    const flakes = Array.from({ length: count }, () => ({
      x: Math.random() * size.w,
      y: Math.random() * -size.h,
      w: 2 + Math.random() * 5,
      h: 1 + Math.random() * 4,
      v: 12 + Math.random() * 26,           // px per second, paper falls slowly
      drift: (Math.random() - 0.5) * 18,
      phase: Math.random() * Math.PI * 2,
      a: 0.18 + Math.random() * 0.4,
      spin: (Math.random() - 0.5) * 0.6
    }));

    let last = performance.now();
    function frame(now) {
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      ctx.clearRect(0, 0, size.w, size.h);
      for (const f of flakes) {
        f.y += f.v * dt;
        f.phase += f.spin * dt;
        f.x += Math.sin(f.phase) * f.drift * dt;
        if (f.y > size.h + 10) { f.y = -10; f.x = Math.random() * size.w; }
        ctx.fillStyle = `rgba(206, 200, 189, ${f.a})`;
        ctx.fillRect(f.x, f.y, f.w, f.h);
      }
      raf = requestAnimationFrame(frame);
    }
    raf = requestAnimationFrame(frame);

    return () => { cancelAnimationFrame(raf); off(); retire(el); };
  }

  /* ---------------- blank: floor 13, everything drains ---------------- */

  function buildBlank() {
    const el = layer("fx__blank");
    const unmark = mark("fx-blank");
    return () => { unmark(); retire(el); };
  }

  /* ---------------- static: the secret final boss ---------------- */

  /** One noise tile, drawn once and then only moved. Cheap to hold for a fight. */
  function noiseTile(size = 140) {
    const c = document.createElement("canvas");
    c.width = c.height = size;
    const g = c.getContext("2d");
    const img = g.createImageData(size, size);
    const d = img.data;
    for (let i = 0; i < d.length; i += 4) {
      const v = 110 + Math.random() * 145;
      d[i] = d[i + 1] = d[i + 2] = v;
      d[i + 3] = Math.random() * 60;
    }
    g.putImageData(img, 0, 0);
    return c.toDataURL();
  }

  function buildStatic() {
    const el = layer("fx__static");
    el.style.backgroundImage = `url(${noiseTile()})`;
    // The roll bar is the only moving part, and it takes five seconds to cross.
    if (!isCalm()) el.appendChild(document.createElement("i"));
    return () => retire(el);
  }

  /* ---------------- signature: the withdrawal ending ---------------- */

  function buildSignature() {
    const el = layer("fx__signature");
    el.innerHTML = "<i></i><i></i><i></i>";
    return () => retire(el);
  }

  /* ---------------- lowhp: under a quarter health ---------------- */

  function buildLowHp() {
    // Sits above static so both read at once during the last fight.
    const el = layer("fx__lowhp");
    return () => retire(el);
  }

  /* ---------------- palette: floors 5 and 8 ---------------- */

  const PALETTES = { rainbow: "fx-rainbow", mono: "fx-mono", full: "" };
  let paletteTimer = 0;

  function palette({ palette: which = "full", seconds, persist } = {}) {
    const el = screen();
    if (!el) return;

    if (paletteTimer) { clearTimeout(paletteTimer); paletteTimer = 0; }
    el.classList.remove("fx-rainbow", "fx-mono", "fx-still");
    running.delete("palette");

    const cls = PALETTES[which];
    if (!cls) return;                       // "full" means back to normal

    el.classList.add(cls);
    if (isCalm()) el.classList.add("fx-still");
    running.set("palette", () => {
      if (paletteTimer) { clearTimeout(paletteTimer); paletteTimer = 0; }
      el.classList.remove(cls, "fx-still");
    });

    // A burst by default (Floor 5's cold, Floor 8's colour_gag both fire
    // this way now, for ENTRANCE_EFFECT_SECONDS). `persist` is only for the
    // debug `fx cold hold` / `fx colour_gag hold` path, which still holds
    // until an explicit end.
    if (!persist) {
      const secs = Number(seconds) > 0 && Number(seconds) < 3600
        ? Number(seconds) : 15;
      paletteTimer = setTimeout(() => stop("palette"), secs * 1000);
    }
  }

  /* ---------------- dispatch ---------------- */

  const BUILDERS = {
    storm: buildStorm,
    party: buildParty,
    floor_cleared: buildFloorCleared,
    cold: buildCold,
    sever: buildSever,
    session: buildSession,
    amend: buildAmend,
    ember: buildEmber,
    blank: buildBlank,
    static: buildStatic,
    signature: buildSignature,
    lowhp: buildLowHp
  };

  // Used when a burst arrives without seconds, e.g. from the in-game fx command.
  const DEFAULT_SECONDS = {
    storm: 7, party: 12, floor_cleared: 1.8, cold: 15, sever: 15, session: 15,
    amend: 1.6, ember: 8, blank: 15, static: 20, signature: 6, lowhp: 12
  };

  function fire(name, detail = {}) {
    // colour_gag is purely a palette change in the Python, so it is handled
    // here rather than as a builder. cold is not: it sends a palette notify
    // *and* a cold notify, so it falls through to buildCold below and the
    // frost is drawn on top of the desaturation.
    if (name === "palette") return palette(detail);
    if (name === "colour_gag") {
      return palette({ palette: detail.palette || "rainbow",
                       seconds: detail.seconds, persist: detail.persist });
    }

    const build = BUILDERS[name];
    if (!build) { console.warn("[fx] no handler for", name); return; }

    stop(name);
    let teardown;
    try { teardown = build(detail); }
    catch (err) { console.error("[fx]", name, err); return; }

    let timer = 0;
    if (!detail.persist) {
      const secs = Number(detail.seconds) > 0 && Number(detail.seconds) < 3600
        ? Number(detail.seconds)
        : DEFAULT_SECONDS[name] || 6;
      timer = setTimeout(() => stop(name), secs * 1000);
    }
    running.set(name, () => { if (timer) clearTimeout(timer); teardown(); });
  }

  window.addEventListener("thirteen:effect", (e) => {
    const detail = e.detail || {};
    if (detail.name) fire(detail.name, detail);
  });

  window.addEventListener("thirteen:effect:end", (e) => {
    const name = e.detail?.name;
    if (name === "cold" || name === "colour_gag") palette({ palette: "full" });
    if (name) stop(name); else stopTimed();
  });

  window.thirteenEffects = {
    fire,
    end: (n) => (n ? stop(n) : stopTimed()),
    active: () => [...running.keys()]
  };
})();
