// Everything you need to change to run a different Python program.
window.APP_CONFIG = {
  // Entry point, relative to python/
  entry: "play.py",

  // Leave this empty and run `python3 build.py` — it walks python/ and writes
  // python/manifest.json, which covers whole package trees, data files and all.
  // Only set it by hand for a one-file script: files: ["play.py"]
  files: [],

  // Third-party PyPI packages, installed at boot with micropip.
  // Pure-Python wheels only (or anything in the Pyodide distribution, e.g. numpy).
  packages: [],

  // Shown once at boot if files were restored from browser storage. Set to ""
  // for scripts that resume by themselves.
  resumeHint: "Type LOAD to pick up where you left off.",

  // Label on the terminal title bar.
  name: "arbitration terminal",

  // localStorage key holding the save data. Bump the suffix to invalidate old saves.
  storageKey: "thirteen-clauses:fs:v1",

  // Pyodide build. Bump to upgrade Python.
  pyodide: "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/"
};
