/* Thirteen Clauses — browser wrapper.
   Main thread: terminal UI, keyboard, and the only place that touches localStorage. */
(() => {
  "use strict";

  const cfg = window.APP_CONFIG;
  const $ = (id) => document.getElementById(id);

  const el = {
    term: $("term"), seal: $("seal"), sealText: $("sealText"),
    hint: $("hint"), name: $("consoleName"),
    picker: $("filePicker")
  };

  const MAX_INPUT_BYTES = 8192;
  const SCROLL_STEP = 5;      // lines per tap on the scroll keys
  const enc = new TextEncoder();

  let term, fit, worker;
  let awaitingInput = false;
  let ready = false;
  let phase = "boot";
  let queued = [];          // lines typed before Python asked for them
  let line = "";            // current line buffer (desktop, in-terminal editing)
  let history = [], histIdx = -1;

  /* ---------- tab completion ---------- */

  // Published by inputline.py once per prompt (thirteen:vocab). Readline
  // cannot work here — Pyodide has no tty — so the page completes for itself,
  // against the same words the terminal build would have used.
  let vocab = null;
  let promptText = "";      // whatever Python printed after its last newline
  let touchKeyboardReady = false;   // the on-screen keyboard exists and is wired
  let nativeMuted = false;          // xterm's textarea is currently unfocusable
  const KB_PREF = (cfg.storageKey || "thirteen") + ":kb";
  const MAX_PROMPT_TAIL = 120;   // a prompt, not a paragraph

  window.addEventListener("thirteen:vocab", (e) => { vocab = e.detail || null; });

  // Mirrors Completer.candidates in python/frontends/terminal/inputline.py.
  function candidatesFor(text) {
    if (!vocab) return [];
    const parts = text.split(/\s+/).filter(Boolean);
    const first = (parts[0] || "").toLowerCase();
    const typingFirst = parts.length === 0 || (parts.length === 1 && !/\s$/.test(text));

    if (typingFirst) return [...(vocab.verbs || []), ...(vocab.abilities || [])];
    if (first === "sell" || (vocab.item_verbs || []).includes(first)) return vocab.items || [];
    if ((vocab.ability_verbs || []).includes(first)) return vocab.abilities || [];
    if (first === "pace") return vocab.pace || [];
    if (first === "narrator") {
      return (parts[1] || "").toLowerCase() === "voice"
        ? (vocab.voices || []) : (vocab.narrator || []);
    }
    if (first === "load") return vocab.saves || [];
    if (first === "buy" || first === "b") return ["bag"];
    if (first === "attack" || first === "a" || first === "hit") return vocab.enemies || [];
    return [];
  }

  // Space is not a delimiter: item names have spaces in them, so the word
  // being completed is everything after the verb, not the last token.
  // `narrator voice <id>` is the one command with a sub-verb to skip.
  function wordUnderCursor(text) {
    const parts = text.split(/\s+/).filter(Boolean);
    if (parts.length === 0) return "";
    if (parts.length === 1 && !/\s$/.test(text)) return text.trimStart();
    const skip = (parts[0] || "").toLowerCase() === "narrator"
      && (parts[1] || "").toLowerCase() === "voice" ? 2 : 1;
    return text.replace(new RegExp(`^\\s*(?:\\S+\\s+){${skip - 1}}\\S+\\s*`), "");
  }

  function longestSharedPrefix(list) {
    if (!list.length) return "";
    let out = list[0];
    for (const s of list) {
      let i = 0;
      while (i < out.length && i < s.length && out[i] === s[i]) i++;
      out = out.slice(0, i);
    }
    return out;
  }

  function complete() {
    if (!awaitingInput && !ready) return;
    const word = wordUnderCursor(line).toLowerCase();
    const pool = candidatesFor(line);
    if (!pool.length) return;

    let hits = [...new Set(pool.filter((c) => c.startsWith(word)))].sort();
    // Fall back to substring, so "coffee" finds "vending machine coffee".
    if (!hits.length && word) {
      hits = [...new Set(pool.filter((c) => c.includes(word)))].sort();
    }
    if (!hits.length) return;

    const pick = hits.length === 1 ? hits[0] : longestSharedPrefix(hits);
    if (pick && pick.length > word.length) {
      const head = line.slice(0, line.length - wordUnderCursor(line).length);
      line = head + pick;
      term.write("\r\x1b[2K" + promptEcho() + line);
    }
    if (hits.length > 1) {
      term.write("\r\n\x1b[90m  " + hits.slice(0, 12).join("   ") +
                 (hits.length > 12 ? "   …" : "") + "\x1b[0m\r\n");
      term.write(promptEcho() + line);
    }
  }

  // Redrawing the line means reprinting the prompt: `\r\x1b[2K` clears the
  // whole row, prompt included. Python prints its prompt through stdout like
  // anything else, so the tail after the last newline is the prompt.
  function promptEcho() { return promptText; }

  /* ---------- state pill ---------- */

  // Waiting for input is the normal state of a text game, so it gets no separate
  // label — it would read as an alert that never goes away.
  const STATES = {
    boot:    "Booting",
    running: "In session",
    await:   "In session",
    done:    "Adjourned",
    error:   "Objection sustained"
  };
  function setState(k) {
    const state = k === "error" ? "done" : k;
    phase = state;
    el.seal.dataset.state = state;
    el.sealText.textContent = STATES[k] || k;
    // mobile hides the seal, so the header lamps carry the same signal
    const bar = document.querySelector(".console__bar");
    if (bar) bar.dataset.state = state;
  }

  /* ---------- storage (save files live here and nowhere else) ---------- */

  const store = {
    load() {
      try { return JSON.parse(localStorage.getItem(cfg.storageKey)) || {}; }
      catch { return {}; }
    },
    save(snapshot) {
      try { localStorage.setItem(cfg.storageKey, JSON.stringify(snapshot)); return true; }
      catch { return false; }
    },
    clear() { localStorage.removeItem(cfg.storageKey); }
  };

  /* ---------- cross-origin isolation (needed for SharedArrayBuffer) ---------- */

  // The service worker is what lets the CDN's files through COEP, so we wait for
  // it to take control even on hosts that already send the isolation headers.
  async function ensureIsolation() {
    if (location.protocol === "file:" || !("serviceWorker" in navigator)) {
      return self.crossOriginIsolated;
    }
    if (!navigator.serviceWorker.controller) {
      if (sessionStorage.getItem("coi-retry")) return self.crossOriginIsolated;
      try {
        await navigator.serviceWorker.register("sw.js", { scope: "./" });
        await navigator.serviceWorker.ready;
        sessionStorage.setItem("coi-retry", "1");
        location.reload();
        return new Promise(() => {});   // page is going away
      } catch {
        return self.crossOriginIsolated;
      }
    }
    sessionStorage.removeItem("coi-retry");
    return self.crossOriginIsolated;
  }

  /* ---------- viewport ---------- */

  // Android PWAs report a 100dvh that can exceed what is actually visible, which
  // pushes the bottom keyboard row off screen. Measure it instead.
  function syncViewportHeight() {
    const h = window.visualViewport?.height ?? window.innerHeight;
    document.documentElement.style.setProperty("--app-height", `${Math.round(h)}px`);
  }

  function watchViewport() {
    syncViewportHeight();
    window.visualViewport?.addEventListener("resize", syncViewportHeight);
    window.addEventListener("resize", syncViewportHeight);
    window.addEventListener("orientationchange", () => setTimeout(syncViewportHeight, 300));
    // some Android shells settle their system bars a beat after load
    setTimeout(syncViewportHeight, 400);
  }

  /* ---------- terminal ---------- */

  function initTerminal() {
    term = new Terminal({
      convertEol: true,
      cursorBlink: true,
      fontFamily: getComputedStyle(document.body).fontFamily,
      fontSize: window.innerWidth < 760 ? 13 : 14,
      lineHeight: 1.25,
      scrollback: 5000,
      theme: {
        background: "#171310", foreground: "#ecdfc4",
        cursor: "#e8a33d", cursorAccent: "#171310",
        selectionBackground: "rgba(232,163,61,.3)",
        black: "#0c0a07",  red: "#c25a48",  green: "#8fae6f",  yellow: "#e8a33d",
        blue: "#6d8fb0",   magenta: "#a67ba0", cyan: "#68a3a0", white: "#e7dfcb",
        brightBlack: "#6f675a", brightRed: "#e5897c", brightGreen: "#9dc48c",
        brightYellow: "#f2c079", brightBlue: "#94b4d4", brightMagenta: "#c8a0c2",
        brightCyan: "#8fc9c6", brightWhite: "#fff6e4"
      }
    });
    fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(el.term);
    fit.fit();

    const refit = () => { try { fit.fit(); } catch {} };
    new ResizeObserver(refit).observe(el.term.parentElement);
    window.addEventListener("orientationchange", () => setTimeout(refit, 250));

    term.onData(onTermData);
  }

  function onTermData(data) {
    // arrow-key history, handled before the per-character pass
    if (data === "\x1b[A" || data === "\x1b[B") {
      if (!history.length) return;
      histIdx = data === "\x1b[A"
        ? Math.min(histIdx + 1, history.length - 1)
        : Math.max(histIdx - 1, -1);
      line = histIdx >= 0 ? history[histIdx] : "";
      term.write("\r\x1b[2K" + promptEcho() + line);
      return;
    }
    if (data.startsWith("\x1b")) return;   // ignore other escape sequences

    for (const ch of data) {
      if (ch === "\t") { complete(); }
      else if (ch === "\r" || ch === "\n") { term.write("\r\n"); submit(line); line = ""; }
      else if (ch === "\x7f" || ch === "\b") {
        if (line) { line = line.slice(0, -1); term.write("\b \b"); }
      }
      else if (ch === "\x03") { term.write("^C\r\n"); line = ""; }
      else if (ch >= " ") { line += ch; term.write(ch); }
    }
  }

  function note(msg) {
    term.write(`\r\n\x1b[90m${msg}\x1b[0m\r\n`);
  }

  function submit(text) {
    // xterm's scrollOnUserInput only fires for input it handled itself, which
    // excludes the on-screen keyboard and anything sent while stdin is disabled.
    term.scrollToBottom();
    // The line the prompt was on is spent. Anything Python prints next
    // starts a new tail, so the old one must not carry over.
    promptText = "";
    if (text.trim()) { history.unshift(text); histIdx = -1; }
    if (awaitingInput) { deliver(text); return; }

    // Queueing ahead is fine while the program is running. It is not fine if the
    // program never started or has exited — that used to swallow input silently.
    if (!ready) {
      note("Still loading — nothing can accept input yet. See the lines above.");
      return;
    }
    if (phase === "done") {
      note("The program has exited. Press Restart to play again.");
      return;
    }
    queued.push(text);
  }

  /* ---------- input channel to the worker ---------- */

  let ctrl, buf;   // Int32Array control block, Uint8Array data block

  function deliver(text) {
    const bytes = enc.encode(text.slice(0, MAX_INPUT_BYTES));
    buf.set(bytes.subarray(0, buf.length));
    Atomics.store(ctrl, 1, Math.min(bytes.length, buf.length));
    Atomics.store(ctrl, 0, 1);
    Atomics.notify(ctrl, 0);
    awaitingInput = false;
    setState("running");
  }

  function onNeedInput() {
    if (queued.length) { deliver(queued.shift()); return; }
    awaitingInput = true;
    setState("await");
  }

  /* ---------- boot ---------- */

  async function fileList() {
    if (Array.isArray(cfg.files) && cfg.files.length) return cfg.files;
    const res = await fetch("python/manifest.json", { cache: "no-cache" });
    if (!res.ok) {
      throw new Error("no files in config.js and no python/manifest.json — run build.py");
    }
    const list = await res.json();
    if (!Array.isArray(list) || !list.length) throw new Error("python/manifest.json is empty");
    return list;
  }

  async function fetchPython() {
    const out = {};
    const files = await fileList();
    const missing = [];
    await Promise.all(files.map(async (f) => {
      const res = await fetch(`python/${f}`, { cache: "no-cache" });
      if (!res.ok) {
        if (f === cfg.entry) throw new Error(`python/${f} — ${res.status} ${res.statusText}`);
        missing.push(f);
        return;
      }
      out[f] = new Uint8Array(await res.arrayBuffer());   // binary-safe: covers data files too
    }));
    if (missing.length) {
      term.write(`\x1b[33mSkipped ${missing.length} file(s) the host would not serve: ` +
                 `${missing.slice(0, 5).join(", ")}\x1b[0m\r\n`);
    }
    return out;
  }

  // Isolation fails for a small number of specific reasons. Name the one that applies.
  function diagnose() {
    const secure = self.isSecureContext;
    const lines = [
      `origin            ${location.origin}`,
      `secure context    ${secure}`,
      `service worker    ${"serviceWorker" in navigator ? "available" : "unavailable"}`,
      `crossOriginIsolated ${self.crossOriginIsolated}`,
      `SharedArrayBuffer ${typeof SharedArrayBuffer !== "undefined" ? "available" : "unavailable"}`,
      ""
    ];

    if (location.protocol === "file:") {
      lines.push("Opening index.html from disk cannot work. Serve it over HTTP(S).");
    } else if (!secure) {
      lines.push(
        "This origin is not a secure context, so the browser blocks both service",
        "workers and SharedArrayBuffer. Python needs SharedArrayBuffer to read input.",
        "",
        "Serve the site over HTTPS. localhost is exempt, which is why it works there",
        "and not here. A self-signed certificate is enough for a LAN box.");
    } else {
      lines.push(
        "The server must send both of these response headers:",
        "  Cross-Origin-Opener-Policy: same-origin",
        "  Cross-Origin-Embedder-Policy: require-corp",
        "",
        "sw.js supplies them itself once registered, so a reload usually fixes this.",
        "If it persists, set them on the server. See WRAPPER.md.");
    }
    return lines.join("\n");
  }

  function fail(msg, detail) {
    setState("error");
    term.write(`\r\n\x1b[31m${msg}\x1b[0m\r\n`);
    if (detail) term.write(`\x1b[90m${String(detail).replace(/\n/g, "\r\n")}\x1b[0m\r\n`);
  }

  async function boot() {
    const required = { term: "#term", seal: "#seal", sealText: "#sealText" };
    const absent = Object.entries(required).filter(([k]) => !el[k]).map(([, sel]) => sel);
    if (absent.length) {
      document.body.insertAdjacentHTML("afterbegin",
        `<pre style="color:#ef9c8d;padding:16px;font:13px monospace">` +
        `index.html is missing ${absent.join(", ")} — it is an older version than app.js. ` +
        `Redeploy every file from the same build.</pre>`);
      return;
    }
    if (el.name) el.name.textContent = cfg.name;
    watchViewport();
    initTerminal();
    setState("boot");
    term.write("\x1b[90mLoading the record...\x1b[0m\r\n");

    const isolated = await ensureIsolation();
    if (!isolated || typeof SharedArrayBuffer === "undefined") {
      return fail("This page cannot run Python here.", diagnose());
    }

    let sources;
    try { sources = await fetchPython(); }
    catch (e) { return fail("Could not load the Python sources.", e.message); }

    const sab = new SharedArrayBuffer(12 + MAX_INPUT_BYTES);
    ctrl = new Int32Array(sab, 0, 3);
    buf = new Uint8Array(sab, 12);

    worker = new Worker("worker.js");
    worker.onerror = (e) => fail("The sandbox crashed.", e.message);

    // The game runs inside the worker, where there is no window to dispatch on.
    // effects.py posts its events out; re-fire them on window for effects.js.
    worker.addEventListener("message", (e) => {
      if (e.data && e.data.__thirteen) {
        window.dispatchEvent(new CustomEvent(e.data.kind, { detail: e.data.detail }));
      }
    });
    worker.onmessage = ({ data: m }) => {
      if (m.__thirteen) return;          // handled by the bridge above
      switch (m.type) {
        case "stdout": {
          term.write(m.text);
          // The tail after the last newline is the prompt the cursor is
          // sitting on, and redrawing the line has to restore it. A chunk
          // with no newline in it is a continuation of that tail — Python's
          // prompt ("  > ") has no trailing newline and can arrive on its
          // own — so it appends. submit() clears the tail afterwards; without
          // that, every prompt appended to the last and the line redrew as
          // "  >   >   >   > ".
          const nl = m.text.lastIndexOf("\n");
          promptText = nl === -1 ? promptText + m.text : m.text.slice(nl + 1);
          if (promptText.length > MAX_PROMPT_TAIL) {
            promptText = promptText.slice(-MAX_PROMPT_TAIL);
          }
          break;
        }
        case "progress":
          term.write(`\x1b[90m  ${m.text}\x1b[0m\r\n`);
          console.info("[boot]", m.text);
          break;
        case "need-input": onNeedInput(); break;
        case "fs":
          if (!store.save(m.snapshot)) {
            term.write("\r\n\x1b[33mSave failed: this browser's storage is full.\x1b[0m\r\n");
          }
          break;
        case "ready":
          ready = true;
          setState("running");
          if (m.restored?.length) {
            term.write(`\x1b[90mRestored ${m.restored.length} file(s) from this browser.` +
                       `${cfg.resumeHint ? " " + cfg.resumeHint : ""}\x1b[0m\r\n`);
          }
          break;
        case "exit":
          awaitingInput = false;
          setState("done");
          term.write("\r\n\x1b[90m-- proceedings closed. Restart to file again. --\x1b[0m\r\n");
          break;
        case "error": fail("Python stopped.", m.detail); break;
      }
    };

    setTimeout(() => {
      if (ready) return;
      fail("Startup has stalled.",
        "The last line above is where it stopped.\n\n" +
        "If it stopped while fetching the runtime, this browser cannot reach\n" +
        `${cfg.pyodide}\n` +
        "Check the Network tab for requests to that host: a proxy, firewall or\n" +
        "content filter blocking the CDN looks exactly like this — quiet, with\n" +
        "nothing in the console.");
    }, 45000);

    worker.postMessage({
      type: "init",
      sab,
      indexURL: cfg.pyodide,
      entry: cfg.entry,
      sources,
      packages: cfg.packages || [],
      cols: term.cols,
      rows: term.rows,
      restore: store.load()
    });

    if (matchMedia("(pointer: coarse)").matches) {
      // The on-screen keyboard replaces the native one, which xterm can't
      // reliably summon on mobile anyway.
      if (enableTouchKeyboard()) {
        const btn = document.querySelector('[data-act="keyboard"]');
        if (btn) btn.hidden = false;     // only useful where there are two options
        setTouchKeyboard(touchKeyboardPreference() !== "off", false);
      }
      // No #kb markup: fall back to the device keyboard, which xterm
      // raises when its textarea takes focus.
      else term.focus();
    } else {
      term.focus();
    }
  }

  /* ---------- on-screen keyboard ---------- */

  // xterm keeps a hidden textarea for input and a11y. Tapping the terminal focuses
  // it, which is what raises the Android/iOS keyboard even with stdin disabled.
  const blurOnFocus = (e) => e.target.blur();

  function muteNativeKeyboard() {
    const ta = term.textarea;
    if (!ta || nativeMuted) return;
    ta.setAttribute("inputmode", "none");     // explicit "no virtual keyboard"
    ta.setAttribute("aria-hidden", "true");
    ta.readOnly = true;
    ta.tabIndex = -1;
    ta.style.display = "none";                // unfocusable, so xterm's focus() no-ops
    ta.addEventListener("focus", blurOnFocus);
    nativeMuted = true;
  }

  // The exact reverse, so the device keyboard can be handed back. Named
  // handler rather than a closure, because it has to be removable.
  function unmuteNativeKeyboard() {
    const ta = term.textarea;
    if (!ta || !nativeMuted) return;
    ta.removeEventListener("focus", blurOnFocus);
    ta.removeAttribute("inputmode");
    ta.removeAttribute("aria-hidden");
    ta.readOnly = false;
    ta.tabIndex = 0;
    ta.style.display = "";
    nativeMuted = false;
  }


  // Every key routes through onTermData, so the touch keyboard, a physical
  // keyboard and the history buffer all share one code path.
  function enableTouchKeyboard() {
    const kb = $("kb");
    if (!kb) return false;

    const letters = $("kbLetters"), symbols = $("kbSymbols"), shiftKey = $("kbShift");
    let shift = false;

    function setShift(on) {
      shift = on;
      shiftKey?.classList.toggle("is-active", on);
      shiftKey?.setAttribute("aria-pressed", String(on));
      if (!letters) return;
      for (const key of letters.querySelectorAll(".kb__key[data-k]")) {
        const k = key.dataset.k;
        if (k.length === 1 && k >= "a" && k <= "z") key.textContent = on ? k.toUpperCase() : k;
      }
    }

    function showPage(sym) {
      if (letters) letters.hidden = sym;
      if (symbols) symbols.hidden = !sym;
    }

    kb.addEventListener("pointerdown", (e) => {
      const key = e.target.closest(".kb__key");
      if (!key?.dataset.k) return;
      e.preventDefault();                 // keep focus off the terminal, no tap-zoom
      key.classList.add("is-down");
      setTimeout(() => key.classList.remove("is-down"), 110);

      switch (key.dataset.k) {
        case "enter":     onTermData("\r"); setShift(false); break;
        case "backspace": onTermData("\x7f"); break;
        case "space":     onTermData(" "); break;
        case "tab":       onTermData("\t"); break;
        case "up":        onTermData("\x1b[A"); break;
        case "down":      onTermData("\x1b[B"); break;   // no button, physical arrows still work
        case "scroll-up":   term.scrollLines(-SCROLL_STEP); break;
        case "scroll-down": term.scrollLines(SCROLL_STEP); break;
        case "shift":     setShift(!shift); break;
        case "page-sym":  showPage(true); break;
        case "page-abc":  showPage(false); break;
        default: {
          const k = key.dataset.k;
          onTermData(shift ? k.toUpperCase() : k);
          if (shift) setShift(false);
        }
      }
    });

    kb.hidden = false;
    touchKeyboardReady = true;
    return true;
  }

  /* ---------- choosing between the two keyboards ---------- */

  // On a tablet the screen is big enough for the device's own keyboard, and a
  // physical one may be attached — but both tablets and phones report
  // `pointer: coarse`, so there is no reliable way to tell them apart. The
  // toolbar button decides it instead, and the choice is remembered per
  // device. Phones get the built-in keyboard by default, which is the only
  // thing that works there.
  function setTouchKeyboard(on, remember) {
    const kb = $("kb");
    if (!touchKeyboardReady || !kb) return;

    kb.hidden = !on;
    term.options.disableStdin = on;
    if (on) {
      muteNativeKeyboard();
    } else {
      unmuteNativeKeyboard();
      term.focus();          // tapping the terminal now raises the device keyboard
    }

    const btn = document.querySelector('[data-act="keyboard"]');
    if (btn) {
      // The label stays "OSKB" either way — it names the thing the button
      // toggles, not the action. State is carried by aria-pressed and the
      // dimmed/dashed styling that hangs off it.
      btn.setAttribute("aria-pressed", String(on));
      btn.classList.toggle("is-active", on);
      btn.title = on
        ? "On-screen keyboard is on. Tap to use this device's keyboard instead."
        : "On-screen keyboard is off. Tap the terminal to type, or tap here to bring it back.";
    }
    if (remember) {
      try { localStorage.setItem(KB_PREF, on ? "on" : "off"); } catch { /* private mode */ }
    }
  }

  function touchKeyboardPreference() {
    try { return localStorage.getItem(KB_PREF); } catch { return null; }
  }

  /* The mobile composer that used to live here has gone. Its markup was not
     in index.html, so it had been dead for some time — and unlike onTermData
     it passed the raw field value to submit() without dropping control
     characters, which was an escape sequence straight into xterm and on into
     Python's stdin. The on-screen keyboard above replaced it; the device
     keyboard is the fallback. */

  /* ---------- toolbar ---------- */

  document.querySelector(".console__actions")?.addEventListener("click", (e) => {
    const act = e.target.closest("button")?.dataset.act;
    if (!act) return;

    if (act === "restart") location.reload();

    if (act === "export") {
      const blob = new Blob([JSON.stringify(store.load(), null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `thirteen-clauses-save-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    }

    if (act === "import") el.picker.click();

    if (act === "keyboard") setTouchKeyboard($("kb")?.hidden === true, true);

    if (act === "erase") {
      const ok = confirm(
        "Erase saved games from this browser?\n\n" +
        "This deletes every save file the game has written here, freeing the space " +
        "they take up. It cannot be undone — download a backup first if you want to " +
        "keep them.\n\n" +
        "The game you are playing right now keeps running. Only the saved files go.");
      if (!ok) return;
      store.clear();
      if (ctrl) Atomics.store(ctrl, 2, 1);   // tell the sandbox to drop them too
      term.write("\r\n\x1b[33mSaved games erased. This session continues.\x1b[0m\r\n");
    }
  });

  el.picker?.addEventListener("change", async () => {
    const file = el.picker.files?.[0];
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      if (typeof parsed !== "object" || parsed === null) throw new Error("not a backup file");
      const n = Object.keys(parsed).length;
      if (!confirm(`Restore ${n} file(s), replacing what this browser has stored?`)) return;
      store.save(parsed);
      location.reload();
    } catch (err) {
      alert("That isn't a Thirteen Clauses backup: " + err.message);
    } finally {
      el.picker.value = "";
    }
  });

  boot().catch((e) => {
    try { fail("Startup failed.", (e && e.stack) || String(e)); }
    catch { console.error(e); }
  });
})();
