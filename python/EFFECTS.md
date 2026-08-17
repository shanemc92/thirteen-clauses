# Browser effects — hook spec

Every effect below is already fired by the game. Nothing else needs adding on the
Python side; the JS is the only missing half.

All arrive as `CustomEvent` on `window`:

```js
window.addEventListener("thirteen:effect",     (e) => show(e.detail));
window.addEventListener("thirteen:effect:end", (e) => hide(e.detail.name));
```

`detail` is always `{ name, ... }`. If Python is in a Web Worker the same payload is
also posted as `{ __thirteen: true, kind, detail }` — relay it onto `window`.

## Held vs burst

- **Held** effects arrive with `persist: true` and `seconds: 86400`. They run until a
  matching `thirteen:effect:end` with that `name`. The long `seconds` is a safety net
  for a wrapper that only reads duration — do not rely on it. In the shipped game this
  is now only `static` (the secret final boss fight) and `lowhp` (under 25% health) —
  both scoped to a single fight, not a floor. Every other held effect below can still
  be produced this way from the `fx <name> hold` debug command, but the engine itself
  no longer fires one.
- **Burst** effects carry a real `seconds` and end themselves. No `:end` is sent.

`thirteen:effect:end` with no name ends all timed effects and leaves the palette alone.

### Floor effects are bursts, not floor-long holds

`storm` (Floor 12), `cold` (Floor 5), `colour_gag` (Floor 8), `sever` (Floor 9),
`session` (Floor 10) and `blank` (Floor 13) used to fire once on arrival and hold
until the floor was left — which, a few rooms in, just reads as the overlay being
stuck on. Each is now a timed burst, 30 seconds by default
(`step.ENTRANCE_EFFECT_SECONDS`), and a floor fires it at three moments and
nowhere else:

- **on arrival** — a new game or a descent, so the floor announces itself;
- **on a load** — `step.resume()` fires it from whatever room the save was in,
  so a session picked up mid-floor gets the same scene-setting;
- **at the boss threshold** — the first time you enter a room whose exits include
  the floor's `boss_room`, so the last stretch is set again before the fight.

A floor can shorten or lengthen its own burst with `effect_seconds` in its JSON,
read by `step.floor_effect_seconds()`. Floor 13 sets it to 10: `blank` drains the
whole page toward white, and thirty seconds of that is a long time to read
through. Values outside 0-3600 fall back to the default, matching the bound
`fire()` in `effects.js` already applies.

The threshold burst fires once per floor visit (`effect_threshold.<floor>` in
`state.flags`), so pacing in and out of that room does not re-trigger it. See
`_effect_should_fire` and `boss_threshold_rooms` in `engine/step.py`.

## The effects

| `name` | Type | Payload | Fires when | Suggested treatment |
|---|---|---|---|---|
| `palette` | burst | `palette: rainbow \| mono \| full`, `seconds` | Floor 7 reveal (`rainbow`, held), Floor 8 gag, Floor 5 (`mono`) | tint `.console__screen`, self-clears after `seconds` |
| `storm` | burst | `seconds: 30` | Floor 12 arrival, load, boss threshold | canvas rain, lightning as slow fade |
| `storm` | burst | `seconds: 7` | Floor 7 passing downpours | same, shorter, lighter |
| `ember` | burst | `seconds: 30` | Floor 12, alongside `storm` | grey ash drifting down through the rain |
| `ember` | burst | `seconds: 8` | the Reaper of Records dies | slow grey paper-snow, drifting down |
| `cold` | — | sends **two** notifies: `palette: mono` and `cold`, both `seconds: 30` | Floor 5 arrival, load, boss threshold | desaturate the whole page, and frost it |
| `colour_gag` | — | sends `palette: rainbow`, `seconds: 30` | Floor 8 arrival, load, boss threshold | the browser half of the Floor 7 joke |
| `sever` | burst | `seconds: 30` | Floor 9 arrival, load, boss threshold | a vertical cut across the viewport, halves offset a few px |
| `session` | burst | `seconds: 30` | Floor 10 arrival, load, boss threshold | slow dark vignette; court is sitting |
| `blank` | burst | `seconds: 30` | Floor 13 arrival, load, boss threshold | everything drains toward white |
| `amend` | burst | `seconds: 1.6` | every time a Floor 11 room rewrites itself | a redline sweep top to bottom |
| `static` | held | `persist` | the secret final boss starts | interference; ends when it is beaten |
| `signature` | burst | `seconds: 6` | the withdrawal ending | ink spreading across the page |
| `lowhp` | held | `persist` | under 25% health, Floor 7+ | red vignette; ends when you heal above 25% |
| `party` | burst | `seconds: 12` | the SING easter egg, on the third break room | drifting coloured lights |
| `floor_cleared` | burst | `floor: n` | any floor boss beaten | expanding ring and warm wash |

## Where each effect is used

Every trigger in the game, by floor. Anything not listed here does not raise
an effect at all.

| Floor | Raises | When |
|---|---|---|
| 1-4 | — | nothing; the building has not started on you yet |
| 5 | `cold` | arrival, load, boss threshold |
| 6 | — | — |
| 7 | `palette: rainbow` (held), `storm` (7s) | the colour reveal, then passing downpours |
| 8 | `colour_gag` | arrival, load, boss threshold |
| 9 | `sever` | arrival, load, boss threshold |
| 10 | `session` | arrival, load, boss threshold |
| 11 | (`amend`, per room) | not a floor effect: it fires whenever a room rewrites itself, so it is listed under "not tied to a floor" below |
| 12 | `storm` **and** `ember` | arrival, load, boss threshold, both together |
| 13 | `blank` | arrival, load, boss threshold |

Floor 12 is the only floor that raises two. `floor["effect"]` accepts a string
or a list; `step.floor_effects()` normalises it, and every trigger point —
arrival, load, threshold, teardown on descent, and the `effects on`/`off`
toggle — walks the list. The pairing is deliberate: Floor 12's own random
events are about ash coming down as well as the weather, and one overlay could
only say half of that.

Not tied to a floor:

| Effect | Trigger | Code |
|---|---|---|
| `lowhp` | dropping under 25% health on Floor 7 or below | `step._check_low_hp` |
| `floor_cleared` | any floor boss beaten | `render.py` |
| `ember` | the Reaper of Records dies | `combat.ON_DEATH_EFFECT` |
| `static` | the secret final boss appears | `step._withdraw_ending` |
| `signature` | signing, at the withdrawal ending | `step._withdraw_ending` |
| `party` | singing in the third different break room | `step._sing` |
| `amend` | a Floor 11 room shifting under you | `step.enter_room` via `quirks.maybe_shift` |

A floor's own effect is a **burst at three moments only** — arrival, load, and
the first entry to the room outside the boss door — never a hold. The
non-floor effects above are the exceptions: `static` and `lowhp` are held and
end explicitly.


`storm` at Floor 12 and the Floor 7 downpour are told apart by duration alone now
that both are bursts (`seconds >= 10` renders the heavier rain) — see `buildStorm`
in `effects.js`.

## Notes

`amend` fires often on Floor 11 — roughly 45% of revisits — so keep it cheap and short
or it will be exhausting.

`lowhp` and `static` can both be up at once during the last fight. They should
compose rather than replace each other.

Nothing should strobe. The 3–30 Hz band triggers photosensitive seizures, and several
of these fire during combat when the player cannot look away. `prefers-reduced-motion`
should drop `storm` lightning, freeze `party`, and make `sever`, `static` and `lowhp`
static rather than animated.

## Testing without playing there

```js
thirteenEffects.fire("sever",     { persist: true })
thirteenEffects.fire("ember",     { seconds: 8 })
thirteenEffects.fire("lowhp",     { persist: true })
thirteenEffects.end("lowhp")
```

In game, `fx` prints bridge diagnostics and `fx <name>` fires one through the real
path. Both are undocumented in `help` on purpose.


## Note on `cold`

`cold` is the one effect that sends two notifies rather than one. The palette
drop is what the floor reads as in text; the `cold` notify is what a wrapper
draws frost from. Sending only the palette — which is what it used to do —
meant a wrapper's `cold` builder was never reached, so Floor 5 went monochrome
and nothing else happened.

A wrapper that only handles `palette` still gets the desaturation and can
ignore the second notify entirely.
