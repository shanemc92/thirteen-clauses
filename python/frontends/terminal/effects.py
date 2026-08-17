"""Small terminal animations. No dependencies, no curses, no library.

Everything here is plain ANSI plus `time.sleep`, and every effect degrades to
printing the text and moving on:

  * `pace fast` skips all animation
  * a dumb terminal (no ANSI) skips all animation
  * Ctrl-C during an effect skips the rest of it, not the game

Effects are named in floor JSON (`"effect": "storm"`), so a retheme can move
or remove them without touching code. Inspired by terminaltexteffects, but
written from scratch to keep the project dependency-free.
"""

import os
import random
import shutil
import sys
import time

def _in_browser():
    """True under Pyodide/Emscripten.

    Animation cannot work there for two reasons, neither of them fixable
    from this side:

      * `time.sleep` blocks the single JS thread, so the page freezes and
        nothing repaints until the whole call returns;
      * stdout is collected and flushed once the call returns, so cursor-up
        redraws arrive all at once as a pile of garbage rather than frames.

    So in the browser every effect falls back to one still frame, which is
    the same beat without the movement. A browser renderer can implement the
    real thing later with timers, driven by the same Effect events.
    """
    return (sys.platform == "emscripten"
            or "pyodide" in sys.modules
            or "js" in sys.modules)


IN_BROWSER = _in_browser()
NO_ANIM = bool(os.environ.get("THIRTEEN_NO_ANIM"))


# -- raw ANSI ------------------------------------------------------------
# The animated effects draw by hand: hide the cursor, walk it back up over
# the frame they just printed, and overwrite each row. Only ever reached
# when Effects.enabled is true, which already requires a real ANSI terminal.
HIDE = "\033[?25l"          # hide cursor
SHOW = "\033[?25h"          # show cursor
UP = "\033[{}A"             # move cursor up N rows, column unchanged
CLEAR_LINE = "\r\033[2K"    # column 0, then wipe the row


def _write(text):
    """Unbuffered write to stdout.

    Frames have to land as they are drawn, so every one of these flushes.
    Never raises: these are also called from the `finally` that tears an
    effect down, and a failure there would take the run with it.
    """
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except (ValueError, OSError):
        pass


# -- raw ANSI ------------------------------------------------------------
# The frame-based effects below (storm, party) drive the cursor
# directly rather than going through the renderer: they repaint the same few
# rows many times a second, and wrap/style would add newlines and reset codes
# in the middle of a frame.
#
# UP is a format string on purpose - every caller moves by a variable number
# of rows and then redraws that many lines.
HIDE = "\033[?25l"        # hide cursor for the duration of the animation
SHOW = "\033[?25h"        # and put it back, including on the error path
UP = "\033[{}A"           # move the cursor up N rows
CLEAR_LINE = "\r\033[2K"  # column 0, then erase the row


def _write(text):
    """Unbuffered write straight to the terminal.

    Animation depends on each frame reaching the screen before the next
    sleep, and print() through a pipe is block-buffered, so the flush is not
    optional. Errors are swallowed: a closed or dumb stdout should lose the
    animation, not the run.
    """
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        pass


# Diagnostics, so a failure to reach the page is visible instead of silent.
BUILD = "2026-08-15a"   # keep in step with main.BUILD and the VERSION file
LAST_ERROR = None
LAST_TARGET = None
DISPATCHED = 0

_BRIDGE = """
(function () {
  var g = (typeof window !== "undefined") ? window : self;
  g.__thirteenFire = function (kind, json) {
    var detail = JSON.parse(json);
    var evt;
    try {
      evt = new CustomEvent(kind, { detail: detail });
    } catch (e) {                       // very old engines
      evt = document.createEvent("CustomEvent");
      evt.initCustomEvent(kind, false, false, detail);
    }
    g.dispatchEvent(evt);
    // If Python is running in a Web Worker, the page's listeners are on a
    // different global and dispatchEvent above cannot reach them. Relay.
    // Relay whenever there is no document (i.e. not the page) and posting is
    // available. Checking `instanceof WorkerGlobalScope` is stricter and can
    // miss non-standard worker environments, so this tests capability.
    if (typeof g.document === "undefined" && typeof g.postMessage === "function") {
      try {
        g.postMessage({ __thirteen: true, kind: kind, detail: detail });
      } catch (e) { /* protocol may reject unknown shapes; dispatch already ran */ }
    }
    return true;
  };
  return true;
})()
"""

_bridge_ready = False


def _install_bridge():
    """Build the dispatcher in JS itself.

    Doing the CustomEvent construction on the JS side means Python only ever
    passes strings, which sidesteps every `to_js` conversion question: no
    Map-vs-object, no version differences in where `to_js` lives, no proxy
    lifetime issues.
    """
    global _bridge_ready, LAST_TARGET
    if _bridge_ready:
        return True
    import js
    js.eval(_BRIDGE)
    LAST_TARGET = "worker" if _in_worker() else "window"
    _bridge_ready = True
    return True


def _in_worker():
    try:
        import js
        return not hasattr(js, "document")
    except Exception:
        return False


def _fire(kind, payload):
    """Dispatch to the page. Records the failure rather than hiding it."""
    global LAST_ERROR, DISPATCHED
    try:
        import json as _json
        import js
        _install_bridge()
        js.__thirteenFire(kind, _json.dumps(payload))
        DISPATCHED += 1
        LAST_ERROR = None
        return True
    except Exception as exc:                      # noqa: BLE001 - reported below
        LAST_ERROR = f"{type(exc).__name__}: {exc}"
        return False


def notify_browser(name, **payload):
    """Tell the JS host an effect fired, so it can run its own overlay.

    Fires a CustomEvent on the page called `thirteen:effect`, detail
    `{name, ...payload}`:

        window.addEventListener("thirteen:effect", (e) => {
          if (e.detail.name === "storm") showStormOverlay();
        });

    The overlay is a DOM layer, not terminal output, so it can cover the whole
    page including a mobile layout where the terminal sits above a keyboard.

    A matching `thirteen:effect:end` fires when the effect finishes, so a
    wrapper can time a transition rather than guess a duration.

    If Python is running in a Web Worker the same payload is also posted with
    `postMessage` as `{__thirteen: true, kind, detail}`, because a worker
    cannot dispatch onto the page's window.

    Outside Pyodide this is a no-op. Run FX in game to see what happened.
    """
    return _fire("thirteen:effect", {"name": name, **payload})


def notify_vocab(payload):
    """Publish the tab-completion vocabulary to the page.

    Fires `thirteen:vocab` with the candidate sets from inputline's
    Completer. The browser has no tty, so readline never sees stdin there
    and the page has to complete for itself; this stops it from having to
    keep its own copy of every item and ability name.

    Outside Pyodide this is a no-op, exactly like notify_browser.
    """
    return _fire("thirteen:vocab", payload)


def _notify_end(name):
    return _fire("thirteen:effect:end", {"name": name})


def diagnostics():
    """Human-readable state, for the in-game FX command."""
    lines = [
        f"build:           {BUILD}",
        f"in browser:      {IN_BROWSER}",
        f"animations:      {'off (browser/still frames)' if IN_BROWSER else 'on'}",
        f"js module:       {'yes' if 'js' in sys.modules or _has_js() else 'no'}",
        f"running in:      {LAST_TARGET or ('worker' if _in_worker() else 'unknown')}",
        f"bridge built:    {_bridge_ready}",
        f"events sent:     {DISPATCHED}",
        f"relay:           {'postMessage (worker) + dispatchEvent'
                            if _in_worker() else 'dispatchEvent only'}",
        f"last error:      {LAST_ERROR or 'none'}",
    ]
    return "\n".join(lines)


def _has_js():
    try:
        import js                                  # noqa: F401
        return True
    except Exception:
        return False


class Effects:
    """Bound to a renderer so it can respect width, colour and pacing."""

    def __init__(self, renderer):
        self.r = renderer

    # -- guards ----------------------------------------------------------
    @property
    def enabled(self):
        """Animation is only safe on a real terminal that is not in a hurry."""
        return (self.r.ansi and self.r.pace != "fast"
                and not IN_BROWSER and not NO_ANIM)

    def _still(self, name, key, **payload):
        """One frame instead of the animation, plus the JS hook.

        Deliberately does NOT send `:end`. The still frame prints instantly,
        so ending here would tell the wrapper to tear the overlay down in the
        same tick it was raised. The payload carries `seconds`; the overlay
        owns its own lifetime and self-ends, exactly as a manual
        `thirteenEffects.fire("storm", {seconds: 8})` does.

        Notifies even when the fallback art is missing, so a wrapper's overlay
        never depends on the ASCII frame existing.
        """
        notify_browser(name, **payload)
        art = self.r.content_art(key) if hasattr(self.r, "content_art") else ""
        if art:
            self.r.art(art)

    def _colour(self, code, text):
        if not (self.r.colour and self.r.ansi):
            return text
        return f"\033[38;5;{code}m{text}\033[0m"

    def _rows(self, wanted):
        height = shutil.get_terminal_size((80, 24)).lines
        return max(4, min(wanted, height - 6))

    # -- effects ---------------------------------------------------------
    def storm(self, seconds=3.2, rows=9, persist=False):
        """Rain with the occasional flash. Used where weather is the point."""
        if not self.enabled:
            self._still("storm", "fx_storm",
                        seconds=(86400.0 if persist else max(seconds, 6.0)),
                        persist=persist)
            return
        notify_browser("storm", seconds=seconds)
        width = min(self.r.width, 78)
        rows = self._rows(rows)
        drops = [[random.randrange(width), random.randrange(rows)]
                 for _ in range(max(12, width // 3))]
        rng = random.Random()
        end = time.time() + seconds
        _write(HIDE + "\n" * rows)
        try:
            while time.time() < end:
                flash = rng.random() < 0.09
                grid = [[" "] * width for _ in range(rows)]
                for drop in drops:
                    x, y = drop
                    if 0 <= y < rows:
                        grid[y][x] = "|"
                    if 0 <= y - 1 < rows:
                        grid[y - 1][x] = "."
                    drop[1] += 2
                    if drop[1] >= rows:
                        drop[0] = rng.randrange(width)
                        drop[1] = rng.randrange(-4, 0)
                if flash:
                    bolt_x = rng.randrange(4, max(5, width - 4))
                    for y in range(rows):
                        grid[y][bolt_x] = "\\" if y % 2 else "/"
                        bolt_x = max(0, min(width - 1, bolt_x + rng.choice((-1, 0, 1))))

                _write(UP.format(rows))
                for row in grid:
                    line = "".join(row)
                    if flash:
                        line = self._colour(231, line)
                    else:
                        line = self._colour(39, line)
                    _write(CLEAR_LINE + line + "\n")
                time.sleep(0.16 if not flash else 0.05)
        except KeyboardInterrupt:
            pass
        finally:
            _write(UP.format(rows))
            for _ in range(rows):
                _write(CLEAR_LINE + "\n")
            _write(UP.format(rows) + SHOW)
            _notify_end("storm")

    def party(self, seconds=2.6, rows=7, persist=False):
        """Flashing lights. Used when the building sings back."""
        if not self.enabled:
            self._still("party", "fx_party",
                        seconds=(86400.0 if persist else max(seconds, 12.0)),
                        persist=persist)
            return
        notify_browser("party", seconds=seconds)
        width = min(self.r.width, 70)
        rows = self._rows(rows)
        hues = [196, 208, 226, 46, 51, 33, 129, 201]
        glyphs = "*+.oO*+."
        rng = random.Random()
        end = time.time() + seconds
        frame = 0
        _write(HIDE + "\n" * rows)
        try:
            while time.time() < end:
                _write(UP.format(rows))
                for y in range(rows):
                    line = []
                    for x in range(width):
                        if (x + y * 2 + frame) % 5 == 0:
                            glyph = glyphs[(x + frame) % len(glyphs)]
                            hue = hues[(x // 3 + y + frame) % len(hues)]
                            line.append(self._colour(hue, glyph))
                        else:
                            line.append(" ")
                    _write(CLEAR_LINE + "".join(line) + "\n")
                frame += 1
                time.sleep(0.11)
                if rng.random() < 0.2:
                    frame += 2
        except KeyboardInterrupt:
            pass
        finally:
            _write(UP.format(rows))
            for _ in range(rows):
                _write(CLEAR_LINE + "\n")
            _write(UP.format(rows) + SHOW)
            _notify_end("party")

    def colour_gag(self, seconds=6.0, persist=False):
        """Floor 8's joke: it is still in colour, and the page knows it.

        Browser-only. The terminal already made this joke on Floor 7 with the
        spectrum, so there is nothing to draw here. A burst by default;
        `persist` is only for `fx colour_gag hold` while eyeballing it.
        """
        if persist:
            notify_browser("palette", palette="rainbow", persist=True,
                           seconds=86400.0)
        else:
            notify_browser("palette", palette="rainbow", seconds=seconds)

    def cold(self, seconds=15.0, persist=False):
        """The catacomb: desaturated *and* frosted.

        Two notifies, not one. The palette drop is what the floor reads as
        in text, but `buildCold` in effects.js draws the frost, and sending
        only the palette meant that builder was never reached — Floor 5 went
        monochrome and nothing else happened. A burst timed to the
        entrance-room trigger in step.py; `persist` is only for `fx cold
        hold`.
        """
        if persist:
            notify_browser("palette", palette="mono", persist=True,
                           seconds=86400.0)
            notify_browser("cold", persist=True, seconds=86400.0)
        else:
            notify_browser("palette", palette="mono", seconds=seconds)
            notify_browser("cold", seconds=seconds)

    # ---- browser-only effects -------------------------------------------
    # These have no terminal animation: the terminal already tells the story
    # in text, and these are the page's version of the same beat. Each is a
    # single notify, so a wrapper with no listener is completely unaffected.

    def sever(self, seconds=15.0, persist=False):
        """Clause 9. The page should look cut, the way the floor is.
        A burst by default; `persist` is only for `fx sever hold`."""
        if persist:
            notify_browser("sever", persist=True, seconds=86400.0)
        else:
            notify_browser("sever", seconds=seconds)

    def session(self, seconds=15.0, persist=False):
        """Clause 10. Court is sitting: a slow vignette while the text
        crawls. A burst by default; `persist` is only for `fx session hold`."""
        if persist:
            notify_browser("session", persist=True, seconds=86400.0)
        else:
            notify_browser("session", seconds=seconds)

    def amend(self, seconds=1.6, persist=False):
        """Clause 11. A redline sweep when a room rewrites itself."""
        notify_browser("amend", seconds=seconds)

    def ember(self, seconds=8.0, persist=False):
        """The Reaper's death: forty years of records coming down as ash."""
        notify_browser("ember", seconds=seconds)

    def blank(self, seconds=15.0, persist=False):
        """Clause 13. Everything drains toward white.
        A burst by default; `persist` is only for `fx blank hold`."""
        if persist:
            notify_browser("blank", persist=True, seconds=86400.0)
        else:
            notify_browser("blank", seconds=seconds)

    def static(self, seconds=0, persist=True):
        """The Commentary. Interference, held for the length of the fight."""
        notify_browser("static", persist=True, seconds=86400.0)

    def signature(self, seconds=6.0, persist=False):
        """The withdrawal: ink spreading across the page."""
        notify_browser("signature", seconds=seconds)

    def lowhp(self, seconds=0, persist=True):
        """Held while you are under a quarter health. Ends when you recover."""
        notify_browser("lowhp", persist=True, seconds=86400.0)

