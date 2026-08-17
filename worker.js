/* Runs the Python program inside Pyodide.
   Lives in a worker so input() can block on Atomics.wait without freezing the page. */
"use strict";

let pyodide, ctrl, inBuf;
const dec = new TextDecoder();          // stdin, whole lines
const outDec = new TextDecoder();       // stdout, may split multi-byte chars

const APP = "/app";      // Python sources, replaced on every load
const DATA = "/data";    // working dir + HOME; this is what gets persisted
const MAX_FILE = 1_000_000;
const MAX_TOTAL = 3_500_000;

let sourcePaths = new Set();

/* ---------- persistence: the virtual FS <-> a plain JSON snapshot ---------- */

const b64 = {
  encode(bytes) {
    let s = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(s);
  },
  decode(str) {
    const bin = atob(str);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
};

function snapshot() {
  const out = {};
  let total = 0;

  const walk = (dir) => {
    let entries;
    try { entries = pyodide.FS.readdir(dir); } catch { return; }
    for (const name of entries) {
      if (name === "." || name === "..") continue;
      const path = `${dir}/${name}`.replace("//", "/");
      let stat;
      try { stat = pyodide.FS.stat(path); } catch { continue; }
      if (pyodide.FS.isDir(stat.mode)) { walk(path); continue; }
      // Symmetric with restore(): there is no point persisting a file the
      // restore side will refuse to write back, and persisting anything
      // broader means localStorage carries paths that only exist to be
      // rejected later.
      if (!restorable(path)) continue;
      if (stat.size > MAX_FILE || total + stat.size > MAX_TOTAL) continue;
      try {
        out[path] = b64.encode(pyodide.FS.readFile(path));
        total += stat.size;
      } catch { /* unreadable, skip */ }
    }
  };

  walk(DATA);
  walk(APP);   // catches saves written next to the sources, i.e. /app/saves
  return out;
}

// Where a snapshot is allowed to put things back. The program lives under
// /app and /app is sys.path[0], so anything writable there is code the
// interpreter may import. A backup file is untrusted input — it arrives
// from the Restore button, i.e. from whoever sent it to you — and the old
// filter allowed any *new* path under /app. Dropping /app/textwrap.py into
// a snapshot was therefore arbitrary Python on the next load, shadowing a
// stdlib module that had not been imported yet.
//
// Saves are written to /data and to /app/saves, so those are the only two
// places a snapshot needs to reach, and neither is importable.
const RESTORE_ROOTS = [DATA + "/", APP + "/saves/"];
const RESTORE_SUFFIXES = [".13save", ".json", ".txt"];
const MAX_RESTORE_FILES = 200;

function restorable(path) {
  if (typeof path !== "string" || path.length > 255) return false;
  if (!path.startsWith("/") || path.includes("..") || path.includes("//")) return false;
  if (!RESTORE_ROOTS.some((root) => path.startsWith(root))) return false;
  if (sourcePaths.has(path)) return false;               // never the program
  const name = path.slice(path.lastIndexOf("/") + 1);
  if (!name || name.startsWith(".")) return false;
  // Extension allowlist, not a .py denylist: .pth, .pyw and friends are all
  // reachable by the import machinery, and an allowlist does not need to
  // enumerate them.
  return RESTORE_SUFFIXES.some((suffix) => name.endsWith(suffix));
}

function restore(snap) {
  const restored = [];
  const rejected = [];
  let total = 0;

  for (const [path, data] of Object.entries(snap || {})) {
    if (restored.length >= MAX_RESTORE_FILES) break;
    if (!restorable(path) || typeof data !== "string") { rejected.push(path); continue; }
    let bytes;
    try { bytes = b64.decode(data); } catch { rejected.push(path); continue; }
    // Same ceilings the snapshot side respects. Without them a crafted or
    // simply enormous backup could exhaust memory before Python ever ran.
    if (bytes.length > MAX_FILE || total + bytes.length > MAX_TOTAL) {
      rejected.push(path);
      continue;
    }
    try {
      pyodide.FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
      pyodide.FS.writeFile(path, bytes);
      restored.push(path);
      total += bytes.length;
    } catch { rejected.push(path); }
  }

  if (rejected.length) {
    self.postMessage({ type: "progress",
                       text: `ignored ${rejected.length} entr(y/ies) in the backup that were not save files` });
  }
  return restored;
}

function persist() {
  try { self.postMessage({ type: "fs", snapshot: snapshot() }); } catch { /* ignore */ }
}

/* ---------- stdio ---------- */

// The Erase button raises a flag in the control block. We can only act on it at a
// prompt, so check on both sides of the wait — otherwise a checkpoint taken just
// before the click would write the files straight back.
function maybeWipe() {
  if (Atomics.exchange(ctrl, 2, 0) !== 1) return false;
  const drop = (dir) => {
    let entries;
    try { entries = pyodide.FS.readdir(dir); } catch { return; }
    for (const name of entries) {
      if (name === "." || name === "..") continue;
      const path = `${dir}/${name}`.replace("//", "/");
      let stat;
      try { stat = pyodide.FS.stat(path); } catch { continue; }
      if (pyodide.FS.isDir(stat.mode)) { drop(path); continue; }
      if (sourcePaths.has(path)) continue;      // never delete the program
      try { pyodide.FS.unlink(path); } catch { /* in use, leave it */ }
    }
  };
  drop(DATA);
  drop(APP);
  return true;
}

function readLineBlocking() {
  maybeWipe();
  persist();                                    // checkpoint at every prompt
  Atomics.store(ctrl, 0, 0);
  self.postMessage({ type: "need-input" });
  Atomics.wait(ctrl, 0, 0);
  if (maybeWipe()) persist();
  const len = Atomics.load(ctrl, 1);
  // TextDecoder rejects views backed by a SharedArrayBuffer, so copy out first.
  const bytes = new Uint8Array(len);
  bytes.set(inBuf.subarray(0, len));
  return dec.decode(bytes) + "\n";
}

/* ---------- boot ---------- */

const say = (text) => self.postMessage({ type: "progress", text });

async function init(msg) {
  ctrl = new Int32Array(msg.sab, 0, 3);
  inBuf = new Uint8Array(msg.sab, 12);

  say(`fetching runtime from ${msg.indexURL}`);
  importScripts(msg.indexURL + "pyodide.js");

  say("starting Python");
  pyodide = await loadPyodide({
    indexURL: msg.indexURL,
    env: {
      HOME: DATA,
      PYTHONUNBUFFERED: "1",
      TERM: "xterm-256color",
      COLUMNS: String(msg.cols || 80),
      LINES: String(msg.rows || 24),
      // No __pycache__. A .pyc is not a source file, so it was being
      // snapshotted to localStorage and written back on the next visit —
      // stale bytecode sitting on top of a freshly deployed program, and
      // eating the storage budget for nothing.
      PYTHONDONTWRITEBYTECODE: "1"
    }
  });

  say(`Python ${pyodide.version || ""} ready`);

  pyodide.FS.mkdirTree(APP);
  pyodide.FS.mkdirTree(DATA);

  for (const [rel, bytes] of Object.entries(msg.sources)) {
    const path = `${APP}/${rel}`;
    const dir = path.slice(0, path.lastIndexOf("/"));
    if (dir !== APP) pyodide.FS.mkdirTree(dir);
    pyodide.FS.writeFile(path, bytes);
    sourcePaths.add(path);
  }

  say(`${Object.keys(msg.sources).length} program file(s) copied`);
  const restored = restore(msg.restore);

  if (msg.packages?.length) {
    say(`installing ${msg.packages.join(", ")}`);
    await pyodide.loadPackage("micropip");
    const micropip = pyodide.pyimport("micropip");
    await micropip.install(msg.packages);
  }

  pyodide.setStdout({
    isatty: true,
    write: (b) => { self.postMessage({ type: "stdout", text: outDec.decode(b, { stream: true }) }); return b.length; }
  });
  pyodide.setStderr({
    isatty: true,
    write: (b) => { self.postMessage({ type: "stdout", text: "\x1b[31m" + outDec.decode(b, { stream: true }) + "\x1b[0m" }); return b.length; }
  });
  pyodide.setStdin({ stdin: readLineBlocking, isatty: true });

  // Pyodide's own stdin plumbing raises OSError 29 inside a worker, so input()
  // and sys.stdin are pointed straight at the host instead.
  pyodide.registerJsModule("_hostio", { readline: readLineBlocking });
  pyodide.runPython(`
import builtins, io, sys, _hostio

class _HostStdin(io.TextIOBase):
    encoding = "utf-8"
    errors = "strict"

    def readable(self):
        return True

    def isatty(self):
        return True

    def readline(self, size=-1):
        return _hostio.readline()

    read = readline

    def __iter__(self):
        while True:
            line = self.readline()
            if not line:
                return
            yield line

sys.stdin = _HostStdin()

def _input(prompt=""):
    if prompt:
        sys.stdout.write(str(prompt))
        sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        raise EOFError("no more input")
    return line.rstrip("\\n")

builtins.input = _input
`);

  say(`running ${msg.entry}`);
  self.postMessage({ type: "ready", restored });

  try {
    pyodide.runPython(`
import os, sys, runpy
os.chdir(${JSON.stringify(DATA)})
sys.path.insert(0, ${JSON.stringify(APP)})
sys.argv = [${JSON.stringify(msg.entry)}]
runpy.run_path(${JSON.stringify(APP + "/" + msg.entry)}, run_name="__main__")
`);
  } catch (err) {
    const text = String(err.message || err);
    if (!/SystemExit/.test(text)) {
      self.postMessage({ type: "error", detail: text });
    }
  }

  persist();
  self.postMessage({ type: "exit" });
}

self.onmessage = ({ data }) => {
  if (data.type === "init") {
    init(data).catch((e) => self.postMessage({ type: "error", detail: String(e && e.message || e) }));
  }
};
