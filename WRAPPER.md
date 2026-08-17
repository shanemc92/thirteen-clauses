# Browser wrapper

A static site that runs a Python CLI program in a browser terminal. No backend, no
server-side state. Python runs as WebAssembly (Pyodide) inside a web worker; saves go
to `localStorage` in the visitor's browser.

This file covers **the wrapper only** — the shell, the terminal, the worker, hosting
and deployment. It is deliberately generic: the wrapper does not know or care what
Python program it is running.

For the game itself — commands, floors, combat, content format, tests — see
**[`python/SPOILERS.md`](python/SPOILERS.md)** — which, as the name says, gives away
the whole game. For the visual effects contract from the
game's side, see **[`python/EFFECTS.md`](python/EFFECTS.md)**.

## Files

```
index.html      page shell
styles.css      theme — BUILD ARTEFACT, see "Styles" below
keyboard.css    the on-screen keyboard block on its own
effects.css     the effects block on its own
effects.js      overlay visuals driven by the game's custom events
manifest.webmanifest  install-to-home-screen metadata
icons/          app and favicon PNGs
favicon.ico     16/32/48 multi-size
VERSION         which build this folder is
config.js       <- the only file you normally edit
app.js          terminal, keyboard, completion, storage (main thread)
worker.js       Pyodide runner (worker thread)
sw.js           service worker that adds COOP/COEP headers
_headers        same headers, for Netlify / Cloudflare Pages
sws.toml        static-web-server config: TLS plus the required headers
nginx.conf.example  reverse-proxy config, if nginx terminates TLS instead
build.py        regenerates python/manifest.json after you change python/
python/         the program being run — see python/SPOILERS.md
```

### Styles

`styles.css` is **generated**: it is the theme plus `keyboard.css` plus `effects.css`
concatenated. Editing it directly works until the next rebuild, then the change
vanishes. Edit `keyboard.css` or `effects.css` and re-concatenate instead.

## Swapping in a different program

The wrapper runs any line-based Python CLI. To point it at something else:

1. Put your files in `python/` — whole package trees are fine.
2. Run `python3 build.py`. It writes `python/manifest.json` listing everything the
   browser should copy in, skipping `__pycache__`, `.pyc` and friends.
3. Set `entry` in `config.js` to your entry point, relative to `python/`.

Re-run `build.py` any time you add, rename or delete a file. Forgetting to is what
`ModuleNotFoundError: No module named 'yourpackage'` means — the file never made it
into the sandbox.

For a single-file script you can skip the build step and list it directly instead:

```js
entry: "play.py",
files: ["play.py"],
```

The whole tree lands at `/app`, which is on `sys.path`, so `from frontends.terminal.main
import main` resolves the same way it does on disk. Non-Python files (JSON, ASCII art,
data) are copied byte-for-byte too.

### What your script can rely on

- `input()` and `print()`, line-based, as normal.
- ANSI colour and cursor codes — xterm.js renders them.
- A real filesystem at `/data` (the working directory and `$HOME`) and `/app`.
- `COLUMNS` set from the browser terminal's actual width at boot, so
  `shutil.get_terminal_size()` is right on load. Resizing the window afterwards does
  not update it.

### What won't work

- Network calls (`requests`, `socket`), threads, subprocesses, `curses`,
  `os.system("clear")` — use `\033[2J\033[H` instead.
- Single-keypress input (`getch`). Input is line-based: the browser sends a whole
  line when Enter is pressed.
- `time.sleep` as animation. It blocks the single JS thread, so the page freezes and
  nothing repaints until the call returns. Drive animation from the page instead —
  see "The event bridge" below.
- `readline`. There is no tty for it to attach to. The wrapper provides history and
  completion itself.
- C-extension packages that aren't in the Pyodide distribution.

## Input

`index.html` carries an on-screen keyboard (`#kb`) because xterm.js can't reliably
raise the native keyboard on mobile. `app.js` shows it when `pointer: coarse` matches
and sets `disableStdin` on the terminal so taps don't fight it.

Every key — letters, symbols, Shift, Backspace, Enter, Tab, history arrow, Ctrl-C —
routes through the same `onTermData` path a physical keyboard uses, so line editing
behaves identically either way. The top row carries `tab`, then the history arrow,
then the two scroll buttons.

Delete the `#kb` block from `index.html` and it falls back to a plain input bar, or
to the terminal itself if that's gone too.

### Choosing between the two keyboards

A tablet has room for the device's own keyboard, and may have a physical one
attached — but tablets and phones both report `pointer: coarse`, so there is no
reliable way to tell them apart. The `OSKB` button at the right of the toolbar
decides it instead, and the choice is remembered per device under
`<storageKey>:kb`. The label is fixed: it names what the button toggles, so state
is carried by `aria-pressed` and the dimmed, dashed styling that hangs off it.

Switched off, `#kb` is hidden, `disableStdin` goes back to false and xterm's
textarea is fully restored, so tapping the terminal raises the device keyboard and
a physical one works normally. Switched on, the textarea is muted again. The button
only appears where the on-screen keyboard actually loaded, since there is nothing to
choose between otherwise, and the default stays **on** — that is the only thing
that works on a phone.

`muteNativeKeyboard` has an exact reverse in `unmuteNativeKeyboard`; the focus
handler is a named function rather than a closure specifically so it can be removed
again. Toggling repeatedly must not stack listeners.

The preference lives under its own key, so `Erase` clears saved games without
resetting it.

### History

Up and down arrows walk previously submitted lines. Kept in `app.js`, not in Python.

### Tab completion

Completion runs on the page, because Pyodide has no tty and `readline` never sees
stdin. The program publishes its candidate words and the page matches against them,
so the vocabulary lives in one place:

```js
{ __thirteen: true, kind: "thirteen:vocab", detail: {
    verbs: [...], items: [...], abilities: [...], enemies: [...],
    voices: [...], saves: [...], pace: [...], narrator: [...],
    item_verbs: [...], ability_verbs: [...]
} }
```

`candidatesFor` in `app.js` decides which set applies from the first word of the
line; it mirrors `Completer.candidates` in `python/frontends/terminal/inputline.py`.
**If you add a verb that takes an argument, change both.** Matching is prefix first,
then substring, so `use coff` finds vending machine coffee. A single match completes;
several complete to the longest shared prefix and list the options.

Space is not a delimiter — item names contain spaces, so the word being completed is
everything after the verb, not the last token.

A program that never sends `thirteen:vocab` simply gets no completion.

### Redrawing the line

Recalling history or completing has to redraw the input line, and `\r\x1b[2K` clears
the whole row — prompt included. The prompt is not visible from the page, so `app.js`
reconstructs it: it keeps the tail of stdout after the last newline, since that is
what the cursor is sitting on. A chunk with no newline in it appends to that tail,
because a prompt has no trailing newline and can arrive as its own message.

`submit()` clears the tail. Without that, a submitted line producing no output at all
lets the next prompt append to the last, and the line redraws as `>  >  >  > `. There
is also a 120-character cap as a backstop.

## The event bridge

The program runs inside the worker, where there is no `window` to dispatch on, so it
posts its events out instead:

```js
{ __thirteen: true, kind: "thirteen:effect", detail: { name, seconds, persist } }
```

`app.js` re-fires anything carrying `__thirteen` on `window` as a `CustomEvent`, keyed
by `kind`. The relay is kind-agnostic — adding a new event type needs no change here,
only a listener. Two kinds are in use: `thirteen:effect` (with `thirteen:effect:end`)
and `thirteen:vocab`.

`effects.js` listens for the effect events and paints a fixed overlay above the
terminal. Neither side needs to know about the other: the program only posts, the
overlay only listens on `window`.

Effects are either **held** (`persist: true`, runs until `thirteen:effect:end` with
the same name) or **burst** (a real `seconds`, ends itself, no `:end` sent).
`python/EFFECTS.md` carries the full table: every effect, its payload, and a
floor-by-floor list of every place each one is raised.

A floor may raise more than one at a time — Floor 12 runs `storm` and `ember`
together, so ash comes down through the rain. `running` is a Map keyed by effect
name with a layer each, so concurrent effects compose without any special handling
here.

The overlay is `pointer-events: none` at `z-index: 50`, and `.kb` sits at `60` — so
effects cover the terminal, header and page, but never the on-screen keyboard, which
stays unfiltered and tappable throughout. Palette filters are applied to
`.console__screen` rather than the overlay, so they tint the terminal itself without
washing out the controls.

Nothing strobes. Lights move on 5–8 second cycles and lightning fades over 180ms
rather than flickering, which keeps the whole thing clear of the flash rates that
trigger photosensitive seizures. `prefers-reduced-motion` drops lightning entirely,
freezes the party lights into a static wash, and turns the rainbow cycle into a fixed
hue shift.

To try one from the console without playing to the floor that fires it:

```js
thirteenEffects.fire("storm", { seconds: 8 })
thirteenEffects.fire("palette", { palette: "rainbow" })
thirteenEffects.active()
thirteenEffects.end("storm")
thirteenEffects.end()
```

## Saves

Two separate things, which is worth being clear about:

- **In-game save/load.** Your script writes files as normal. Anything under `/data`
  (the working directory and `$HOME`) or created under `/app` is snapshotted to
  `localStorage` at every prompt and on exit, then written back on the next visit.
  Resuming is still the script's job.
- **The backup buttons.** `Download backup` writes the whole snapshot to a JSON file
  on disk. `Restore backup` reads one back in and reloads. `Erase` clears storage.
  These move files around; they don't start or resume anything.

If files were restored at boot, the terminal says so and prints `resumeHint` from
`config.js`. Set that to `""` for scripts that resume on their own.

Budget is roughly 3.5 MB — beyond that, files are skipped and the terminal warns.

Bump the suffix on `storageKey` in `config.js` to invalidate existing saves.

## Deploying an update

Replace the **whole folder**, not individual files. `index.html`, `app.js`,
`worker.js` and `styles.css` are versioned together — a newer `app.js` against an
older `index.html` throws `TypeError: el.input is null` on the first keypress and
input silently stops working. `app.js` detects the mismatch and says so in red rather
than failing quietly.

Bump the `?v=` query on the local scripts in `index.html` whenever you deploy.

On Glitch specifically: delete the existing files before uploading, since Glitch
keeps whatever it already had. Then hard-refresh, because the service worker is
registered and the browser caches `app.js` aggressively.

## Hosting

Two hard requirements, and they are the usual cause of "it works locally but not on
the server":

1. **A secure context.** `input()` reaches Python through `SharedArrayBuffer`, which
   browsers refuse outside a secure context. `localhost` is exempt by spec — every
   other origin needs HTTPS. A self-signed certificate is fine for a LAN box.
2. **Cross-origin isolation.** The site needs `Cross-Origin-Opener-Policy:
   same-origin` and `Cross-Origin-Embedder-Policy: require-corp`. `sw.js` supplies
   these itself once registered, but service workers also require a secure context,
   so requirement 1 comes first either way.

If either is missing, the terminal prints exactly which one, along with the origin,
`isSecureContext` and `crossOriginIsolated` values.

- **static-web-server** — see `sws.toml` in this folder. TLS plus a header block.
- **Netlify / Cloudflare Pages** — `_headers` is already set up, TLS is automatic.
- **GitHub Pages** — works as-is; TLS is automatic and `sw.js` covers the headers.
  Visitors see one automatic refresh on first load.
- **Nginx** — see `nginx.conf.example`. Note that `add_header` does not
  accumulate: a location block with its own `add_header` discards every one
  inherited from `server{}`.
- **Local testing** — `python3 -m http.server` on localhost works. The same server
  reached over the LAN by IP does not, for the reason above.

## Install to home screen

`manifest.webmanifest` plus the icons make the site installable, so Android and iOS
offer "Add to Home Screen" and launch it without browser chrome (`display:
standalone`). Chrome's install prompt also needs HTTPS and a service worker with a
fetch handler — `sw.js` already qualifies, since it's registered for the isolation
headers anyway.

Rotation isn't locked: 80-column ASCII art is much easier to read in landscape.

Icons are generated from a single 512px source. To swap the artwork, drop a
transparent PNG in and regenerate — the tiles are the logo at 80% scale on `#171310`,
and the maskable one at 56% so Android can crop it to any shape without clipping.

## Troubleshooting

### Checking a deployment

```sh
curl -sI https://your.host.name/ | grep -i cross-origin
```

An empty result is **not** conclusive. `sw.js` adds those two headers in the browser,
and curl never goes through a service worker — so a perfectly working deployment can
still show nothing here. Setting them at the origin as well is worth doing (it saves
one reload on a visitor's first load), but their absence in curl does not explain a
failure on its own. The terminal's own diagnostic block is the reliable check.

### Input echoes but Enter does nothing

The terminal echoes keystrokes in the main thread, so typing works whether or not
Python is alive. If Enter appears to do nothing, the program never reached `input()`.
The boot prints each stage — fetching the runtime, starting Python, copying files,
running the entry point — so the last line tells you where it stopped, and a stall is
reported after 45 seconds.

Stopping at "fetching runtime" means the browser cannot reach the Pyodide CDN. A
proxy, firewall or DNS filter blocking `cdn.jsdelivr.net` produces exactly this: no
console error, nothing in flight. Check the Network tab. To serve the runtime
yourself, download a Pyodide release, put it somewhere on your own origin, and point
`pyodide` in `config.js` at that directory.

### The fix I deployed isn't running

Check the build first, before debugging the code: the title screen prints
`build <date> - content <hash> - browser`, and `version` in game prints the same
line at any point. If that string is not the one you just deployed, the code is not
the code you think it is and nothing else you observe means anything.

Two things used to cause this on their own, both now closed off in `worker.js`:
the snapshot persisted `__pycache__`, and `restore()` would write any `/app` path
back **after** the fresh sources were copied in. Saves live under `/app` alongside
the program, so a snapshot could shadow a deployed file. `restore()` now skips
anything in the current manifest, `.pyc` files are never persisted, and
`PYTHONDONTWRITEBYTECODE` stops them being written at all.

If the build string is stale, it is a caching problem — see below.

### Stale assets

The far more common failure. If the browser holds an old `app.js` against a new
`index.html`, you get errors that look like server or CORS problems but aren't — the
giveaway is a stack trace naming functions or line numbers that don't exist in the
file you just deployed.

Three defences are in place: `sw.js` revalidates every same-origin GET, `sws.toml`
sends `Cache-Control: no-cache` for html/js/css/json, and `index.html` carries a
`?v=` query on its local scripts.

To clear a browser that is already stuck: DevTools → Application → Service Workers →
Unregister, then Storage → Clear site data, then hard reload. Purge the CDN too if
one is in front.

In DevTools → Application → Service Workers, the worker should be **activated and
running** with this page in its clients. If it is registered but not controlling,
reload once. If it never activates, check that `sw.js` is served from the site root
with a JavaScript content type and is not being cached by the proxy.

### Effects never appear

Run `fx` in game (after `debug on`) for the Python side's own diagnostics: whether it
detected the browser, whether the bridge was built, how many events it has sent and
the last error. If events are being sent but nothing paints, the listener is the
problem — check `thirteenEffects.active()` in the console.

## Security notes

There is no server, no account and no user data, so most of the usual web
concerns do not apply. What is left is supply chain and the Restore button.

- Python is sandboxed in WebAssembly with no filesystem or network access to
  the host.
- CSP in `index.html` restricts scripts to same-origin plus the jsDelivr CDN.
  `'unsafe-eval'` is unavoidable — Pyodide needs it.
- The two xterm files are pinned with Subresource Integrity. Run
  `python3 python/tools/sri.py` to verify them, and again after bumping a
  version in `index.html` to get the new hashes. It reads from
  registry.npmjs.org rather than the CDN on purpose: hashing the CDN's own
  response would happily pin a tampered file.
- The service worker only adds headers; it caches nothing and rewrites no
  bodies, and it only touches same-origin requests plus jsDelivr.
- Imported save files are restricted to `/data` and `/app/saves`, to an
  extension allowlist, and to the same size ceilings the snapshot side uses.
  Nothing importable can be written: `/app` is `sys.path[0]`, so a backup
  allowed to drop a `.py` there would be arbitrary code on the next load.
- Save files are validated on the Python side too — a decodable but
  wrong-shaped save raises `SaveError` and is skipped rather than crashing
  the session.

### Residual risk: Pyodide is not pinned

`worker.js` loads the Pyodide runtime with `importScripts()`, which cannot
carry an `integrity` attribute, and Pyodide then fetches its own `.asm.js` and
`.wasm` from the same base URL. The version in `config.js` is pinned, but the
*bytes* are not verified. A compromised jsDelivr could serve a different
runtime.

If that matters for your deployment, self-host it: download the Pyodide
release matching `config.js`, serve it from your own origin, and point
`pyodide` at that path. Same-origin removes both the CDN and the COEP dance
the service worker exists to handle.

### Headers

`_headers` (Netlify / Cloudflare Pages), `sws.toml` and `nginx.conf.example`
all carry the same set. COOP and COEP are load-bearing — `SharedArrayBuffer`
is how `input()` reaches Python and the browser refuses it without both. The
rest is ordinary hardening, and `frame-ancestors` in particular *has* to be a
header: a `<meta>` CSP silently ignores it, and without it the page is
framable and the Erase button is one clickjacked `confirm()` from wiping every
save.

GitHub Pages ignores all three files. `sw.js` supplies COOP/COEP itself once
registered, which is enough to run the game, but it cannot supply
`frame-ancestors` — so on Pages the page stays framable. Behind a real server,
use one of the configs.

## Upgrading Python

Change the `pyodide` URL in `config.js` to a newer version tag.
