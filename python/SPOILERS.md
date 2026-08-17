# THIRTEEN CLAUSES

Text dungeon crawler. Thirteen floors, all built and playable.

Python 3.10+, stdlib only, no dependencies.

> **This file spoils everything.** Every floor, every secret, all four endings,
> the lot. It is the design document, not a player's guide. If you have not
> played it yet, read [README.md](../README.md) instead.

The engine never does I/O and the frontend never touches `GameState`: `step()`
takes an action and returns events, and a frontend renders them. `EFFECTS.md`
documents the events an external renderer can paint.

## Run

```
python3 play.py                  # start menu: new / continue / load
python3 play.py --seed 1234      # reproducible run
python3 play.py --new            # skip the menu, straight to a new run
python3 play.py --load           # skip the menu, resume the most recent save
python3 play.py --load mysave    # load saves/mysave.13save
python3 play.py --width 46       # force screen width
```

On start you get a menu: new run, continue the most recent save, or pick from a
list with timestamps. Saves carry their own write time inside the file rather
than relying on mtime, because several Android filesystems round mtime to the
nearest second or two. With no saves on disk it goes straight to character
creation, so an empty `saves/` never costs a keypress.

`load` with no name scans the working directory and `saves/` and takes the
newest `.13save`. Finished runs are skipped when auto-selecting, so a death does
not block `load` from reaching the save behind it. Naming a dead save explicitly
refuses, with the reason.

### Phone terminals (Pydroid, Termux)

Pydroid reports 80 columns while showing far fewer, which wraps ASCII art and
makes it look broken. Three fixes, any of which works:

```
python3 play.py --width 46          # or whatever actually fits
export THIRTEEN_WIDTH=46            # persists across runs
width 46                            # in game
```

All art is capped at 44 columns and padded to a true rectangle at load, so 46 is
a safe floor. `validate.py` fails the build if any art file exceeds the budget or
is left ragged.

## Check

All of these should be clean before anything ships.

```
python3 tools/validate.py        # content schema, ids, missing strings, art widths
python3 tests/regressions.py     # targeted behavioural tests
python3 tests/smoke.py           # 60 bot playthroughs, save round-trips, crash hunt
python3 tests/smoke.py 30        # ...or fewer, when you are iterating
python3 tests/route.py 99        # fixed route with auto-combat, for eyeballing output
python3 tools/layout.py          # derives room positions from the exit graph
python3 tools/sri.py             # verifies the pinned CDN asset hashes
```

`validate.py` exits 1 on error, so it drops straight into CI. What each one is
for is in [Checks](#checks) further down.

## Commands

```
n s e w         move                 take            open or pick up
look            describe again       talk            speak to whoever is here
map             your path            rest            recover, safe rooms only
sheet           character page       use <item>      use an item
char            your portrait and your companion's
inv             inventory            equip <item>    equip weapon or armour
record          keepsakes and proofs unequip <slot>  put gear back in the bag
memos           what you have read   memos <n>       read one of them again
withdraw        state your position  drop <item>     put something down for good
buy <n> / sell <item> / leave        trade with the salesman

attack          attack nearest       flee            escape (survivors follow you)
attack 2        attack a specific enemy when several share a name
ability         list what you can do and how many uses are left
ability <name>  use one; a partial is enough ("ability hit", "ability blind")
<ability name>  shortcut, if you type the whole name ("objection", "write-off")
combat          how the dice, turn order and death saves work

pace            which pacing you are on, and what each one does
pace fast       print each round at once, and skip every press-Enter break
pace slow       pause briefly between turns (default)
pace manual     press Enter between turns
effects         whether the floor effects are on

width <n>       force screen width       save [name]
load            resume the most recent save
load <name>     load a specific save
help             quit
effects on|off  text and overlay effects
```

Pacing is stored in the save, so it survives a reload.

### Why ABILITY exists

Ability names share the command space with the verb table, and two of them lose
on an exact match: "hit and run" parses as `attack("and run")` and "no case to
answer" as a yes/no answer. Bare names work, but only on an exact match.
`ability <partial>` takes the ability out of that contest entirely and is the
route that always works. An ambiguous partial lists the candidates rather than
guessing — `ability run` offers Cut and Run and Hit and Run.

## Starting a run

Character creation opens with a briefing in the building's own voice: what the
five stat abbreviations stand for and how modifiers work, the difference between
HP and AC and why armour is the stat you actually control, what abilities are and
why their limited uses are the real economy of the descent, and what a companion
does — including that most of them will pick you up once per fight, and what it
costs you when one is out of service. Each section is gated, then it hands off to
class and companion selection. Each class card leads with a labelled PLAYSTYLE
section explaining what Absorb, Reposition, Convert or Argue means in play, and
the at-a-glance summary labels the same verb alongside the starting HP, AC and
stats.

It lives in `theme.json` under `help.briefing`, so a retheme rewrites the whole
onboarding without touching code.

## Input

Tab completes, and it is context-aware: verbs at the start of a line, then
whatever that verb takes. `use <tab>` lists what you are carrying, `pace <tab>`
lists the modes, `attack <tab>` lists the enemies in front of you. Item names
complete on substrings, so `use coff<tab>` finds vending machine coffee, and
`ability <tab>` lists what you know.

Up and down arrows walk your command history, which persists between sessions in
`saves/.history`. Backspace, Ctrl-A, Ctrl-E and Ctrl-W all work.

In a terminal all of this comes from the stdlib `readline`. Some minimal Android
Python builds ship without it; the game detects that and falls back to plain
input rather than failing. `Input.refresh` publishes the candidate sets every
prompt whether or not readline is available, because that is exactly the case
where an external renderer needs them.

## Layout

```
engine/          pure logic. No input(), print(), open(), time, or random. Ever.
  rng.py         seeded, serialisable. (seed, counter) reproduces any stream.
  dice.py        2d6+3 notation
  state.py       all mutable run state, JSON round-trips
  step.py        single dispatch entry point
  combat.py      d20, initiative, abilities, death saves
  progression.py XP, levels, ability grants
  shop.py        stalls, stock generation, haggling
  quirks.py      the per-floor standing rules
  stalker.py     pursuit and pounce
  mapview.py     map payload (renderer owns the glyphs)
  minigames/     rps, dice, blackjack, tictactoe, hangman, amended
content/         every word the player reads
  theme.json     all strings. Swap this file to retheme the game.
  floors/01.json 22 rooms, the onboarding corridor
  floors/02.json 24 rooms, the sewer under the fee structure
  floors/03.json 26 rooms, the flooded actuarial archive
  floors/04.json 28 rooms, a polite suburb that is wrong
  floors/05.json 30 rooms, a server catacomb
  floors/06.json 32 rooms, a hall of your own merchandise
  floors/07.json 33 rooms, the floor where the colour comes back
  floors/08.json 35 rooms, a debtors' foundry
  floors/09.json 36 rooms, a floor cut into pieces
  floors/10.json 37 rooms, an impossible courthouse
  floors/11.json 38 rooms, rooms that rewrite between visits
  floors/12.json 39 rooms, ash and shutdown notices
  floors/13.json 40 rooms, a blank white room
frontends/
  terminal/      ANSI renderer + readline loop
tools/
  validate.py    content schema, missing strings, art widths
  layout.py      derives room positions from the exit graph
  make_saves.py  per-floor test saves
  sri.py         verifies pinned CDN asset hashes
tests/
```

## The rules that matter

The engine is a pure state machine: `step(state, action, content) -> (state,
events)`. It never does I/O and never decides how anything looks. The renderer
consumes events and never reads state.

Every string the player sees is a key into `theme.json`. The engine holds no
English.

## Companions

A companion is a second body in the fight, not decoration. Roughly a quarter of
enemy attacks go at it rather than you, and the Vanguard's Interpose covers it.

It mends a few HP for every room you walk while it is still standing. If it drops
it goes **out of service** and stays that way — walking does nothing — until you
REST in a safe room, which brings it back to full. Recovery rate and armour class
are per companion in `companions.json`: Grunk mends fastest and is easiest to hit,
the Cube is the hardest to land on.

| Companion | Passive |
|---|---|
| Pip | inspects a chest before you open it and says whether it is jammed, rigged or not a chest at all |
| Grunk | takes the first stalker pounce on each floor, so you are not surprised |
| The Cube | shows you one of the opponent's dice in liar's dice, and the hole card in blackjack |
| Bartleby | halves fire damage to you, and breathes on everything every third round |

Fire is a real damage type: the foundry crews on Floor 8 and the burning floors
below deal it.

### Where the companion comes from

It is **clause 4(b)** of the same agreement — *parties in dispute may be
accompanied, the arbitration will provide* — which makes the companion a line
item rather than a game mechanic that wandered in.

The form on the desk has the box ticked, with a line for a name; after you pick,
that name is on it in your handwriting, and it is the only part of the form you
wrote, which is the point. Through the frosted panel in the door, something is
sitting in the corridor and has been for a while: the arbitration provided some
time ago and then did nothing further about it, so they have been out there
longer than you have been in the building, and nobody ever asked whether they
wanted the job.

Ordering carries the joke: the room description prints **after** character
creation, so the form reads as already signed, and the handover puts them outside
the door rather than beside you, because you have not gone through it yet.

## Test saves

```
python3 tools/make_saves.py --name Bud
python3 play.py --load floor-07
```

Writes `saves/floor-01.13save` through `floor-13.13save`. Each drops you in the
start room of that floor with everything above it done — every chest, boss,
miniboss and elite, all key items, the right map tier, level, gear and purse —
and the floor itself completely untouched.

`--class`, `--companion` and `--no-eggs` are available; pass floor numbers to
build only some. Saves are stamped in reverse so floor 1 is the newest and sits
at the top of a newest-first load screen, with floor 13 at the bottom. They are
generated by running the real engine, so nothing the engine would refuse to
produce can end up in one, and each is loaded back and checked before it is
reported as written.

These are debug fixtures and are not committed — `.gitignore` excludes
`python/saves/`, and `build.py` skips the whole tree.

## The story, and who is where

- **Eleven disputed before you.** Ten of them sat down in the white room and
  signed. Their belongings are beside the worn benches on Floor 13.
- **The eleventh got to Floor 9, turned round, and went back up.** They are still
  in the building. They leave the kettles on, put the beach photograph up in the
  break rooms, wrote the biro on the walls, propped the top doors open with a
  fire extinguisher, and always make two mugs of tea.
- **You are the twelfth.** There is no twelfth bench; nobody ordered one.
- **A thirteenth is already inside, four floors back and gaining.** The gaps
  between disputes are shortening, which is the only good news in the building:
  somebody out there is telling people not to sign.
- **No time passes inside.** The acquisition was forty years ago outside; in here
  it has been the same afternoon since. That is why the tea is warm, why a
  forty-year retrieval is still warm, why the bosses have not moved in years and
  are still mid-sentence, and why ten signatures spread over decades are all in
  one room. It is not a mercy — it is a filing convenience, and it means nothing
  ever has to be concluded.

`test_the_disputants_add_up` holds the content to all of it: the count, the
placements, the frozen afternoon being explained in the memos and not only in the
finale, and the fled ending not duplicating the eleventh's work.

### What is actually in the agreement

Five memos quote real terms of service, near enough verbatim, because the real
ones are funnier than anything worth inventing: an option on the customer's
immortal soul, surrenderable within five working days (a games retailer, April
2010, 7,500 accepted in one afternoon); a ban on using a music player to run
nuclear facilities, air traffic control or weapons systems; a clause suspending
the life-critical-systems restriction in the event of a zombie apocalypse; 1,000
hours of community service including scraping gum and unblocking sewers, accepted
by 22 people on a free wifi signup; and the firstborn child, listed as an asset.

None of the companies are named — a games shop, a music player, a wireless
network — which keeps the joke about the absurdity rather than about anybody in
particular. Each is delivered flat, as filing, with somebody's biro underneath
doing the reacting. The Floor 13 exhibit room sets them against what was given in
exchange: a discount meal-kit subscription, twelve weeks, first box half price.

A regression test pins the distinctive wording of each, so a content edit cannot
quietly turn a real clause into an invented one.

The frozen afternoon has an object attached to it. The intake room on Floor 1 has
a second sign at knee height — NO FOOD OR DRINK BEYOND THIS POINT — and your
coffee on the carpet under it, still warm, which you leave because the sign says
to and because everybody does. Every ending comes back to it: warm on the step
outside, warm on the carpet thirteen floors down, warm for as long as that
building stands. In the fled ending you put it back on the intake desk on your
way past, next to the form with your name already on it, where the next one will
see it before they see the sign.

The fled ending leaves something of its own: thirteen hand-copied cards, one per
break room, telling whoever is next to sing in here, out loud, badly. It is the
only thing you learn in there that nobody wrote down.

## The Signatory

The finale is a conversation, not a fight — ASK, SIGN, REFUSE, and WITHDRAW if
you are carrying the unamended term off the Floor 11 boss.

Like a minigame, he owns the command space while he is talking: in `MODE_CHOICE`
with a `finale` pending, the whole line goes to him. The prompt names the actual
options and narrows to `[yes/no] >` at the confirm steps, which really are
yes/no.

The scene is staged rather than dumped. The room description prints and stops;
then it opens like an encounter, because after twelve floors that is what you are
braced for; then the narrator has his say; and only then does it turn out to be a
man at a desk. Five beats, each a press. His longer speeches carry `<pause>`
breaks like the NPC intros, split in `_finale_show`.

The press-gate arming lives in the **printing primitives** — `_emit`, `art`,
`rule` — not in `render()`, because the whole intro prints without going near an
event: the briefing, the class picker and the companion picker all call `wrap()`
and `art()` directly.

A press also lands between an NPC's intro and whatever they open — the stall's
shelf, a minigame board, a boss phase board, or a chit retry. The intro carries
the rules, the challenge and the stakes, and without a gate it scrolls off under
the stock list the instant it prints. It is skipped when the intro already ended
on a `<pause>`, so nobody gates twice.

Measured at 50 columns, presses land about one per 20 lines: briefing 6, floor
clear 3, boss intro 3, NPC intro 2, finale 4. `pace fast` skips all of them.
`GATE_LINES` is 22, so automatic stops catch a genuine wall of text and
deliberate beats come from an explicit `Pause`. `test_one_press_per_beat`
measures the gaps between presses at two widths rather than counting them, which
is the property that actually matters.

### Four endings, and one of them is running away

SIGN, fight and win, withdraw and beat the Narrator, or walk out of the Narrator
fight. The fourth is the eleventh disputant's answer: get to the end, turn round,
and go back up, leaving the kettle on and putting the photograph up in every
break room on the way. Nobody gets out. The point is that the next one gets
further.

`finale_fight` holds whichever boss is still standing and the room restarts it on
re-entry, so a fled finale is never unfinishable.

### What the thirteen refusals buy

The unamended term comes off The Amendment automatically — `boss_amendment` has
`"draws": 0` and an `always` list, and `loot.give()` routes a `key_item` to
keepsakes before it ever checks bag space, so it cannot be missed even with a
full pack. On its own that makes the withdrawal the default rather than
something earned, and the thirteen WITHDRAWs the memos build up on every floor
bought nothing but a keepsake.

The refusals are one per clause in the literal sense — the count is of distinct
floors, so all thirteen have to be walked and spoken on, Floor 1 to Floor 13,
with nothing spare. Miss one and the notice is never filed.

So the egg is a **discount, not a gate**. The option is offered to anyone
holding the term, because a player locked out of three endings would have
nothing on screen telling them why. What filing changes is the weight of the
fight behind it: without `notice_of_withdrawal` the Narrator carries an extra
`step.UNFILED_NARRATOR_HP` (100), applied in `start_combat` via `bonus_hp` and
re-applied on a restart so walking out and back in cannot shed it.

Measured on a geared level 24 character attacking every round, the penalty is
worth about four rounds and a couple of deaths in twenty-five. He is a hard
fight either way — attack-spam alone is near enough a coin flip against him at
his written weight, which is intended for the secret final boss — but the
filing is felt.

He is `The Narrator`, not `The Commentary` — the reveal is that the voice you
have had for thirteen floors is the thing in the room, and calling him something
else at that exact moment hides the one fact the scene is built on. His turn
prints in the narrator's own framing and colour for the same reason. The `static`
overlay is raised persistently for the whole fight and cleared on every exit from
it: beaten, fled, or restarted.

### Choice prompts

The parser keys on a prompt **having options at all**, not on which prompt it is,
so a new choice prompt cannot be added without it working. `prompt_for` builds the
prompt line from the same list, which is why the death prompt reads `[yes/no]`
while the finale reads `[ask/withdraw/sign/refuse]`.

Every ending is the end of Clause 13, so every ending raises the banner and the
`floor_cleared` effect. The banner takes a verb, because four of the five are not
disputes:

| Ending | Banner |
|---|---|
| signed | CLAUSE 13: ENTIRE AGREEMENT SIGNED |
| fought | ...DISPUTED |
| free_leave / free_take | ...CLOSED |
| upstairs | ...ADJOURNED |

It prints after the closing scene rather than before it, so it does not announce
the outcome over the top of the writing that explains it.

`test_every_choice_prompt_accepts_its_own_options` walks all five endings to
completion — signed, fought, free_leave, free_take, upstairs — plus the death
reprieve, checking at each prompt that it names its options and accepts them.
`test_the_signatory_accepts_his_own_options` walks the whole tree, checks all
three of his endings are reachable, checks the withdrawal appears only when you
hold the term, and checks WITHDRAW still means the global one everywhere else.

## Minigames

While one is running it owns the whole command space: every line typed goes to
the game and nothing else is reachable. That is necessary rather than tidy —
eleven of the twenty-six letters are verb aliases ("a" attacks, "e"/"n"/"s"/"w"
move, "i" is inventory, "y" answers a prompt), so hangman is unplayable if the
verb table gets first refusal. The check sits at the top of `parse()`. HELP,
QUIT, VERSION and WIDTH still work, because the frontend handles those before
`parse()` ever sees them; HELP shows the game's own prompt rather than a list of
commands that will not work, and tab completion goes quiet.

| Game | Tests | Hosts |
|---|---|---|
| `rps` | chance | the Vending Ghost (1), the Postman (4), the Colourist (7) |
| `dice` | bluff | the Coin Diver (2), the Ossuary Clerk (5) |
| `blackjack` | nerve | the Reckoner (6) |
| `tictactoe` | a solved puzzle | the Shift Foreman (8), the Usher (10) |
| `hangman` | vocabulary | the Lexicographer (9), the Last Typist (13) |
| `amended` | judgement | the Draftsman (11), the Weather Clerk (12) |

Every floor except 3 has a host. Justice Vorn on Floor 10 also runs a full game
of `dice` as a boss phase at 55% health — see the Floors table.

`blackjack` survives being visible, which is the point — everything is face up
except the hole card and all you have is the decision. Best of three hands, a
real 52-card shoe so what has gone matters, dealer stands on 17. A reshuffle
excludes the cards currently on the table, so counting means something. The
Cube's edge is a peek at the hole card, the same shape of help it gives
everywhere else.

`tictactoe` is deliberately deterministic, which makes it a puzzle rather than a
gamble. Measured, a sensible player draws **100%** of hands against it: it never
misses a win or a block, and noughts and crosses going second is a draw against
anything except one exact fork. The fork is `1, 8, 7, 9`, it works every round,
and it is written on a locker door on Floor 8 — the memo and the opponent are
pinned together by a regression test, so changing one without the other fails the
suite. Draws count towards `MAX_ROUNDS`, so a player who simply blocks is not in
a match that cannot end.

Opponents are set per host with `config.name_key`, so a game can be reused
against a new character without touching code. The validator checks that key
resolves.

### Memos that are worth reading

About half the memos carry something you can act on, and between them they hint
at **every** undocumented thing in the game.

Mechanics: the noughts and crosses fork, the blackjack dealer standing on 17, the
dice opponent leaning high and raising rather than calling, the Floor 9 trapdoor,
break rooms shaking pursuit, keepsakes not taking bag space, the merchant being
the same man on every floor, consumables refusing to be wasted.

Secrets: hidden stashes (Floor 1), the walls that give on the third try (Floor
1), the voices that answer an empty room (Floor 2), singing in three break rooms
(Floor 3), the beach photograph on five floors (Floor 4), withdrawing thirteen
times (Floor 11), and what happens if you give your name as Carl (Floor 13,
written for the next run rather than this one).

The habit-forming ones sit on Floors 1 to 4 on purpose: a hint about searching
empty rooms is worth nothing if you find it on Floor 12.
`test_every_secret_is_hinted_somewhere` checks both that each secret is hinted and
that the number in the hint matches the constant in the code.
`test_memo_tips_are_true` pins each claim to the code it describes — a wrong tip
is worse than no tip.

Every floor carries a memo naming **I WITHDRAW** and telling you to say it out
loud, because no player will ever guess it. Saying it once on each of the
thirteen floors files a notice of withdrawal, which is what the Narrator fight
is weighed against — see [The Signatory](#the-signatory). One per clause is
literal: the count is of distinct floors, so a memo you only find on Floor 9 is
already too late for a clean sheet. Thirteen different framings — a scratched-out
complaints procedure, a crew saying it at the end of every shift as a joke,
somebody who lost count at eleven when the floor came apart. A regression test
requires one per floor and requires it to **name the word**: "say it, thirteen
times" only helps somebody who already read the memo that said what "it" is.

### Stashes

Two per floor, and they announce **nothing** — no room text, no hint on entry, no
mark on the map. `TAKE` in a room that looks empty is the only way one is ever
found, which is the point: the Floor 1 memo teaches the habit and the stashes pay
for it, scaling from about 15 paperclips on Floor 1 to about 100 on Floor 13. The
validator rejects a stash that carries a hint, because then it is just a chest.

## Standing

Every level from 2 to 25 has its own line, tracking how the building's view of
you changes: unnoticed, then a matter, then cited by the things downstairs as a
warning, then unaccounted for. They live under `standing.<level>` in
`theme.json`.

## Progression

Twenty-five levels across thirteen floors, topping out at 70,000 XP
(`progression.XP_TABLE`). Gaps rise monotonically from 200 to 5,900. Measured
against bot playthroughs, level 25 lands on Floor 12-13 for a thorough run and
stays just out of reach of a lean one, so progression runs the whole game rather
than stopping halfway.

The opening levels are not cheap. They were once, so Floor 1 still handed
something out, and it overshot: Floor 1 pays a median of ~800 XP, which sat on
the old level 4 threshold, so the tutorial floor was worth three levels and left
the player far above its own monsters. A median Floor 1 clear now finishes at
level 3, a thorough one at 4. Level 10 up is untouched, so the change
redistributes the early game rather than slowing the run.

Measured level on arriving at each floor: 1, 3, 5, 7, 9, 10, 11, 14, 16, 18, 20,
22, 25.

Hit points cap at 350 (`progression.HP_CAP`). Per-level gain is the hit die plus
the CON modifier plus 3, stepping up by 3 at levels 7, 12, 17 and 22. That
cadence puts the four classes at 95-119% of the cap at level 25, so the ceiling
bites at the ceiling rather than four levels early. A level up also restores you
to full.

## What each stat is for

Every stat is read by the engine, not just the one your weapon scales off.

| Stat | Used for |
|---|---|
| STR | weapon damage and to-hit; forcing jammed chests |
| DEX | armour class, initiative, fleeing, escaping stalkers |
| CON | health per level |
| INT | weapon damage and to-hit; spotting a rigged chest before you open it |
| CHA | weapon damage and to-hit; merchant prices, both ways; the Ghost callout |

About one chest in four is jammed and one in five is rigged, decided per chest
from the run seed so a reload cannot reroll it. On even floors, one in twenty is
not a chest at all.

## The two starting trinkets

The Skirmisher and the Hexwright open with a working consumable; the Vanguard
and the Advocate opened with a trinket that had no `use` block and no code
behind it, so their second item was worth 6 and 2 paperclips at the stall and
nothing else. Both now do something, and neither is class-locked — anyone who
picks one up gets the same effect.

**The settlement chit** — *"redeemable against a claim you have not yet made"* —
is a free retry on a lost minigame. It is offered rather than spent for you,
because a one-off item that vanishes without being asked about is worse than no
item, and the offer arrives in place of the loss, so taking it costs no health.
Decline and you keep the chit and take the penalty. The retry is a genuine
second sitting: `minigame_done` is not set on the way in, the same opponent and
config are rebuilt, and a second loss is a second loss.

**The laminated notice** is inert until Floor 6. The Licensing Office off the
retail floor holds a die-cutter, still plugged in, and walking into that room
holding a laminate gives it an edge: `cut_laminate`, 2d8+4 on STR, which is
Floor 7 tier and lands exactly as you are about to need it. Walk in without one
and the machine is simply a machine for punching toy swords out of flat card —
the joke, and the clue for the next run for anyone who sold theirs for two
paperclips. The cutting is once per run (`laminate_cut`); the comment is a
fixture of the room. Hooked in as an `on_enter` script on `06-r09`, so the
placement is content rather than code.

**The business card** presses at Level 6 (`progression.CARD_PRESSES_AT`) if it
is still in the bag: it stops being your old job and becomes a weapon, 2d6+3
scaling on CHA. That is slightly ahead of the tier a Level 6 player is otherwise
holding — the actuary's pen at 2d6+2 — and well short of the signature pen on
Floor 6, so it rewards patience without being a reason to stop looking. It goes
into the bag rather than straight onto you, because swapping a player's weapon
out from under them is not a gift. Sell it before Level 6 and it is gone.



## Money

Paperclips. Enemies drop them, chests hold them, and the merchant takes them. The
name and plural live in `theme.json` under `currency`, so a retheme renames the
economy without touching code.

Selling merchandise and spare gear leaves you with more paperclips than there is
anything to spend them on, so the merchant stocks four things worth saving for
from Floor 8 down: a second opinion (a spare reprieve), the franchise (everything
you sell is worth half again), a silent partner (+1 to every stat, permanently)
and the deed to one floor of the building.

Carrying five separate pieces of your own merchandise pays off. It is not a sale.

Combat buffs (`root_token`, `grease`, `smoke_canister` and the like) cost no turn
to drink and refuse to be used outside a fight, so there is no reason not to use
them.

The inventory cap is twelve slots (`state.INVENTORY_CAP`), extendable to twenty.
Identical items stack, and a stack costs no extra slot — including at the shop,
so a full bag never blocks the one purchase a full bag most often needs.

## Walking around

Floors 5 to 13 carry, on top of what is in the floor JSON:

- **three medical chests**, drawing on `chest_fN_medical`, where every entry
  heals. A regression test holds density at 0.15 chests a room or better.
- **a vending machine**, in a dead end, so walking to the end of one pays. It is
  a shop NPC with a healing-only stock table, and it does not haggle, buy your
  things or fit you a bigger bag.
- **three memos** — graffiti, payslips, safety notices. Reading one keeps it
  under the `MEMOS` command permanently, so the walls accumulate into a document
  you are carrying. `READ` is the verb, because `TAKE` is a strange thing to say
  to graffiti at knee height — `TAKE` still works, since a note should not be the
  one thing it refuses in a room you are looting, but `READ` only ever reads and
  will not quietly open a chest. `MEMOS` lists them, `MEMOS <n>` reads one again.
  They are deliberately not in the record: memos would bury the keepsakes it
  exists to show.

  Memo text is prose with no hand-made line breaks, so it reflows to whatever
  width the terminal actually is. A newly found one prints green; a re-read
  prints plain. The `MEMOS` index trims each label to one line, and that trimming
  happens in the renderer, because the engine does not know the screen width.

**A random drop prints a found line, so it has to be true where you are
standing.** Every healing item with a `found_key` drops on one floor only: the
floor whose common, good and hoard tables all carry it, which is the floor it was
written for.

| Floor | Its own drop | Floor | Its own drop |
|---|---|---|---|
| 2 | sump water | 8 | quench |
| 3 | cold tea | 9 | the whole thing |
| 4 | casserole | 10 | stay of execution |
| 5 | cold backup | 11 | clean draft |
| 6 | royalty cheque | 12 | the last kettle |
| 7 | the eye of the storm | 13 | good tea |

`chest_fN_medical` draws that floor's item at weight 55, plus the two floors
above it at 28 and 17 — variety, without the floor stopping reading as itself.

The text follows the item rather than the table. `loot.found_line` checks the
item's `found_floor` against where you actually are: on its own floor you get its
own line, and anywhere else the carried-down line, because somebody brought it
with them and did not get any further. So a Floor 4 casserole can turn up on
Floor 6 without claiming Floor 6 has doorsteps.

Rations and adrenaline spread freely and keep their own lines throughout: they
describe a cereal bar and a first aid bracket, neither of which belongs anywhere
in particular, so they carry no `found_floor`.

**Buying is not finding.** No found line is printed for a purchase, so `stock_fN`
and `vending_fN` are free to carry whatever the seller could plausibly have — a
machine on Floor 9 stocks the Floor 9 item plus leftovers from further up, which
is what a vending machine is. `check_floor_local_drops` enforces the distinction:
it rejects a located item dropping more than two floors below where its line was
written, or anywhere at all outside a medical chest, and ignores stock entirely.
It also keeps one-off prizes such as the deed (a 6,500 paperclip full heal that
grants a flag) out of a common chest.

Shop stock is keyed by seller rather than by floor, so two sellers on one floor
do not share stock and sold-out flags. A machine has `shop.machine_*` strings
throughout, attributed through `shop.seller_name`, and
`test_a_machine_never_speaks_as_the_merchant` checks that none of the merchant's
lines can come out of it — otherwise a vending machine says "I'll be one floor
down. I usually am."

TALK is the verb that opens a stall, which is fine for a man behind a table and
absurd for a machine, so the machine explains itself: a brass plate reading VOICE
OPERATED - PLEASE SPEAK CLEARLY, and under it, scratched by hand, TALK TO IT. IT
IS NOT DEAF, IT IS JUST SLOW. The room hint says it is waiting to be spoken to.

## Trapdoors

`on_enter` supports a `teleport` event, used once, on Floor 9, whose whole theme
is a floor severed into pieces that do not join up. Walking into `09-r07` — a
dead end whose own description is about staring across an unbridgeable gap —
drops you to `09-r29` in the far corner. No damage, no encounter: it is the
floor's own logic, not a punishment, and it is a genuine shortcut.

It re-enters `enter_room`, so two rules keep it safe and the validator enforces
both: it must not land on another teleport, and the room it fires in can hold
nothing, because you are never standing in it long enough to reach anything.

## The narrator, and the other voice

One narrator, always on. `content.voice()` is a plain lookup and `narrate()` has
no gate.

The thing that answers empty rooms is a **separate** speaker and looks like it:
deep purple (`#BD93F9`, with a 256-colour fallback where truecolour is not
advertised) against the narrator's grey, so the player never has to work out
which one just spoke. Both carry the same dim `[** ... **]` label, so neither
reads as louder than the other — only the body colour tells them apart. It draws on 28 replies and 20 ambient lines,
enough that it does not repeat inside a single floor.

It is deliberately **not** a hint system — the memos do hints — and a test
enforces that by rejecting any voice line mentioning the withdrawal. It is the
building being strange at you: cheerful, bored, several people at once, keeping
an eye on a sandwich somebody left on Floor Six in 1991. It starts answering
after `VOICES_THRESHOLD` (5) empty-room TALKs and pays off at `VOICES_PAYOFF`
(8).

## The record

Keepsakes, proofs and the things bosses leave behind live outside the inventory
and outside the sheet. `RECORD` shows them in full; the sheet only counts them,
because inline they crowd everything else off a phone screen.

## Naming and labels

Item names are stored in `theme.json` in sentence case, because they mostly
appear inside prose ("You ready the courier jacket."). Anywhere a name is a
**label** rather than a sentence — the record, the inventory, the shop, the
equipped lines on the sheet — it goes through `content.title_case` instead, which
survives apostrophes and acronyms that `str.title()` mangles ("Janitor's Mop", "A
Head from RAID-6") and keeps small words lower mid-name ("Notice of Chargeback").
One implementation, in the engine, so the four displays cannot drift apart.

## Gear

Equipping removes the item from the bag and puts it in `state.equipped`; the
piece it replaces goes back in. So anything still in the bag is never the
equipped one — a second copy of what you are wearing is a **spare**, and is
labelled that way.

A swap into a full bag is refused rather than silently eating the old gear.
`remove_item` only decrements a stack, so equipping one of two identical pieces
frees no slot, and the swapped-out item would have nowhere to go.

Nothing outside `equip` and `unequip` touches `state.equipped` — in particular
`sell` does not. Selling worn gear means `unequip` first, which is also what the
inventory tells you by not listing it.

## Checks

`tests/regressions.py` holds targeted checks for behaviour that is wrong but
perfectly stable, which bot runs cannot catch: the Floor 2 entry toll, consumables
at full health, loot against a full pack, bag upgrades surviving a save, the
companion lifecycle, the CHAR screen, save selection when every mtime ties,
ability names against the verb table, error text wrapping, gear labelling, floor
effect burst timing and duration, senior scaling, break room spacing, and the
build strings agreeing across the VERSION file and both modules.

`tools/validate.py` walks the content and resolves every key something
references, including the handful built at runtime — `companions.<id>.chest_<quirk>`
is checked explicitly, because a companion who inspects chests can meet a mimic.

`tests/smoke.py` scans every event string for unresolved `<<key>>` markers, which
is the backstop for keys the validator cannot know to look for. `<<rainbow>>` is
excluded: it is `RAINBOW_FROM`, the deliberate marker `render.py` splits Floor 7's
reveal on, not a missing key.

## The undocumented things

Nine of them, all hinted in memos, none in `help`.

Walk into the same wall three times in a row and it gives (`WALL_BUMPS_FOR_SECRET`).
Once per run.

Type WITHDRAW. It does nothing. Say it **once on every floor** — thirteen
clauses, thirteen refusals (`WITHDRAW_TARGET`) — and something files it, which
takes 100 health off the secret final boss. The count is of distinct floors
(`withdrew.<floor>` in `state.flags`), so saying it thirteen times in one room
counts once; a repeat on a floor already done gets its own answer and does not
advance anything. Miss a single floor and it is not filed.

Type SING in three different safe rooms and the building joins in, with lights
(`SING_TARGET`).

Type PHOTO in a safe room to look at the beach photograph. Do it on five
different floors and you find out who has been putting them up (`PHOTO_TARGET`).

Carry five separate pieces of your own merchandise (`MERCH_TARGET`).

Name your character Carl at creation. It tells you, and says so on the sheet.

Using a consumable that would do nothing — healing at full health, re-applying a
status you already have — is refused rather than silently wasted. A misclick
should not cost you a potion.

Loot you cannot carry — from a chest or from a kill — waits in the room rather
than vanishing. Drop something and TAKE again.

And if you TALK in a room with nobody in it, repeatedly, something eventually
answers. It does not stop answering. You can talk back or ignore it; both are
supported, and one of them is rewarded.

`debug on` unlocks `fx`, which fires a named effect for testing. It is not in
`help` either. `debug off` puts it away. A bad argument prints a usage line.

## Pauses and alignment

`<pause>` in an NPC intro splits it into press-enter beats: the speaker is named
once and the rest are continuations, and a marker at the very end is a beat
before whatever follows. The Draftsman's intro uses two.

`gate()` is a no-op until something has been printed since the last press. The
flag is set before each non-silent event is handled — before, not after — so an
event that gates itself still consumes its own press and the `Pause` following it
does not fire a second time.

`wrap()` carries a line's own leading indent onto its wrapped continuations,
which textwrap does not do on its own. Combat lines are indented by convention
and wrap aligned.

## Reading the output

Everything is flush left. Wrapped lines look like the lines above them, and ASCII
art is dedented at load and printed at the margin, so nothing shifts depending on
whether it happened to fit.

Combat reveals in two beats: an approach line, a stop, then the art and the name.
Press-Enter gates also fire after long room descriptions, boss death scenes,
level ups, floor clears, and each class and companion on the selection screen.
All of them are skipped by `pace fast`.

Narration renders under a `[** Narrator **]` marker in grey, and the voices that
answer empty rooms under `[** Voice **]` a step dimmer again, so neither can be
mistaken for room description and the voices stay the quietest thing on screen.

Hard stops fire before every boss and miniboss fight, after a floor is cleared,
and before the next floor begins, so the big text beats do not scroll past.

## Animations

`frontends/terminal/effects.py` is a small self-contained animation layer: plain
ANSI and `time.sleep`, no curses and no dependency. Inspired by
terminaltexteffects but written from scratch so the project stays
dependency-free.

Every effect degrades safely: to nothing on a dumb terminal, on `pace fast`, or
on Ctrl-C (which skips the rest of that effect, not the game), and to a single
still frame under Pyodide, where `time.sleep` blocks the one thread and cursor-up
redraws would arrive as a pile of escape codes rather than as frames.
`IN_BROWSER` swaps each effect for a still frame from `content/art/fx_*.txt` and
turns off Floor 10's typewriter text. `THIRTEEN_NO_ANIM=1` forces the same
fallback anywhere.

The engine is unaffected: it emits `Effect` events either way. `EFFECTS.md` has
the full list, when each fires, and what the payload looks like — that is the
document to hand to whoever writes an external renderer.

### Floor effects

Each floor effect is a timed burst — 30 seconds by default
(`step.ENTRANCE_EFFECT_SECONDS`) — fired at three moments and nowhere else:

- **on arrival** — a new game or a descent, so the floor announces itself;
- **on a load** — `step.resume()` fires it from whatever room the save was in;
- **at the boss threshold** — the first time you enter a room whose exits include
  the floor's `boss_room`, so the last stretch is set again before the fight.

A floor sets its own length with `effect_seconds` in its JSON, read by
`step.floor_effect_seconds()`. Floor 13 uses 10 rather than 30: `blank` drains
the screen toward white, and half a minute of that is a long time to read
through. Anything outside 0-3600 falls back to the default.

The threshold burst fires once per floor visit (`effect_threshold.<floor>` in
`state.flags`), so pacing in and out of that room does not re-trigger it. See
`_effect_should_fire` and `boss_threshold_rooms` in `engine/step.py`.

| Effect | Where |
|---|---|
| `storm` | Floor 12 (30s), and passing downpours on Floor 7 (7s) |
| `ember` | Floor 12 alongside the storm (30s), and the Reaper of Records dying (8s) |
| `cold` | Floor 5, the catacomb — a 30s burst, desaturating *and* frosting |
| `colour_gag` | Floor 8 — the far half of the Floor 7 joke, 30s |
| `sever` / `session` | Floors 9 and 10 — overlay only, 30s |
| `blank` | Floor 13 — overlay only, 10s |
| `party` | The SING easter egg, when the building joins in |

Floor 12 raises **two**: `storm` and `ember` together, because its own random
events are about ash coming down as well as the weather and one overlay could
only say half of that. `floor["effect"]` takes a string or a list,
`step.floor_effects()` normalises it, and every trigger point walks the list —
arrival, load, threshold, teardown on descent and the `effects on`/`off` toggle.
`EFFECTS.md` lists every place every effect is raised, and a regression test
checks that list against the content so it cannot drift.

`storm` and `party` are the only two with a terminal fallback frame, so on Floors
7 and 12 the arrival burst prints ASCII into the entrance room's text. The rest
are overlay-only and invisible in a plain terminal.

Floor 7's own spectrum — the terminal-side ANSI rainbow — paints only the
entrance room's text, then reverts to normal for the rest of the floor. It is
terminal-only: the palette change is emitted with `notify=False`, so the overlay
is saved for Floor 8, where the narration makes the "still in colour" joke.

Floor 10 does not animate — it **slows the text down**, arriving a character at a
time, because the court does everything at the speed of procedure. Ctrl-C during
a passage stops it for the rest of the run.

## Combat presentation

Every round opens with the whole board: your health and AC, your companion's
health and whether it is out of service, and every living enemy with its number
and health. The layout adapts to the terminal width, dropping notes onto their
own line rather than truncating them. You should never need to open the sheet
mid-fight.

Combat is turn-stepped rather than dumped. The engine emits `RoundStarted` and
`TurnStarted` markers; the renderer pauses on them according to `pace`. The
engine itself never sleeps and never blocks, so a renderer can pace with a timer
instead without any engine change.

Kills get a `Defeated` event with a per-monster death line drawn from
`mon.<id>.death` in `theme.json`, four or five per monster, framed in a banner.
It fires after the hit line so the kill reads as the consequence of the blow.
Death flavour uses a separate throwaway RNG, deliberately: picking it from the
combat stream would make a mid-fight reload diverge.

## Floors

**1. Acceptance of Terms** — onboarding corridor, 22 rooms. Wandering signage,
interns and a mascot suit; Middle Management guards the corner office; The
Greeter is the boss. Rock-paper-scissors against the Vending Ghost, who cheats on
round three. The Audit Trail map is in the dispatch cage.

Floor 1 is monochrome by design — no colour codes are emitted at all. The
renderer flips capability on `PaletteChanged`, a per-floor JSON field, and Floor
7 is the first to set it. Death on Floor 1 grants a free continue as a tutorial
reprieve (`grants_continue_on_death`), using the same `continue_available` path as
the real one from Clause 9. Path history records from move one whether or not you
have the map, so finding it later retroactively shows the last ten rooms.

**2. Fees and Charges** — flooded sewer, 24 rooms. Coin slicks, surcharges and
Collections, which stalks patiently and for a long way. The Deductible holds the
only crossing as a miniboss, The Auditor guards the count room as an elite, and
Ledgermaw is the boss. Liar's dice against the Coin Diver, who does not cheat and
does not need to.

**3. Limitation of Liability** — flooded actuarial archive, 26 rooms. The Loss
Adjuster is the elite. The boss, The Cap, limits every single blow to 6 damage
until you break it — the Actuary in her dry office off the stacks has been
holding the exception for nine years and will hand it over if anyone stops long
enough to talk to her. Fight The Cap without it and it is a very long night.

**4. Acceptable Use** — a housing estate, 28 rooms. The Committee is the
miniboss, Neighbourhood Watch the elite, and Mrs Hensley the boss. She has
already called someone: every other round she summons a neighbour, up to three,
capped at four enemies on the field. Kill her fast or fight the street. The
Postman plays rock-paper-scissors for The Extended Log, the tier-2 map.

**5. Data Retention** — server catacomb, 30 rooms. Resting does not restore
ability uses here. The Backup is the miniboss and drops the parity disk; RAID-6
rebuilds itself between rounds and without the disk you are fighting six arrays
that repair faster than most builds can cut. Both regen bosses have a finite
number of rebuilds — six, for the six heads — so the fight always ends.

**6. Intellectual Property** — a shop that sells you, 32 rooms. Nothing crits on
this floor, including the Skirmisher's 19-20. The Focus Group is the miniboss and
calls in more participants; the Brand Guardian holds the vault; The Likeness is
built from your own sheet at fight time — your AC plus two, three times your
health, and your weapon.

**7. Force Majeure** — 33 rooms, and **the floor comes apart into spectrum**.
This floor alone uses the `rainbow` palette, where room names, descriptions, art,
speech, combat headers and boss death scenes run a spectrum one step per
character, flowing down the block. Dice rolls, the round status and the narrator
stay legible.

The reveal is placed precisely: the arrival room's text carries a `<<rainbow>>`
marker and the spectrum begins on the line *"And it is in colour."* — everything
above it, including the room's own header, renders normally. Several earlier
floors have a rare `flicker` event that flashes the spectrum for half a second,
so it reads as a fault before it reads as a floor. Nothing before it emits a
colour code at all, including menus and the character sheet, so the flip is the
whole point. Kaleidon reconfigures its armour class every round; there is no
pattern to learn, only enough health to outlast it. The Exception drops the
prism.

**8. Indemnification** — a debtors' foundry, 35 rooms. Six paperclips a room,
escape two harder. The Guarantor redirects a third of your damage onto your
companion until they are nearly down. Full Disclosure, the tier-3 map, is in the
survey office.

**9. Severability** — 36 rooms in four fragments joined by two single doorways.
The map shows gaps that look like bugs and are not. Hemikin is two half-bosses in
separate rooms: fight the right half while the left still stands and it has 50%
more health and +2 AC. Clearing this floor grants the one free continue.

**10. Governing Law** — an impossible courthouse, 37 rooms, exits unlisted until
you LOOK. Justice Vorn stops mid-fight at 55% health and takes the middle of the
matter **on the dice** — a full game of liar's dice in chambers, then straight
back into the fight. Win and Vorn loses a quarter of its health and a turn; lose
and it costs you 18.

**11. Modification of Terms** — 38 rooms, and 29 of them have a second
description they switch to. Revisit a room and it has a 45% chance of having been
amended. The geometry never changes, so you cannot be stranded — your map is
right about the shape and wrong about everything else. The Amendment rerolls its
own AC every round and reissues itself five times.

**12. Termination** — 39 rooms under a slow fall of burnt paper. The Reaper of
Records is an actual dragon made of index cards, and it has eaten every file the
building ever kept, including yours.

**13. Entire Agreement** — 40 rooms of white, and almost nothing in them. The
last room is **not a fight**. The Signatory will not fight you and says so. You
can ask questions, sign, refuse — or, if you took the original Clause 1 off The
Amendment on Floor 11, produce it and withdraw.

Withdrawing does not end the run. There is a **secret final boss** past it, and
it is the one character who has been with you since Intake. Four endings in
total.

Beating a floor boss descends rather than ending the run. Stalkers do not follow
you down; they are floor staff. Ability uses do **not** refresh on descent, only
on REST, so arriving on a new floor spent is a real problem.

## Abilities

Nine per class, granted automatically on levelling, in three tiers:

| Tier | Levels | What it is |
|---|---|---|
| One | 1, 2, 3 | the starting three |
| Two | 5, 7, 9 | roughly doubles what the class can do, keeping its verb |
| Three | 12, 15, 18 | the late kit |

The Vanguard's tier two is **Immovable** (force every enemy onto you), **Load
Bearing** (halve damage and heal) and **Redundancy Notice** (4d10 and a lost
turn). The Hexwright's tier two costs 3, 6 and 10 HP respectively, because
spending yourself is the whole class.

All of it is `abilities.json` plus a grant list in `classes.json` — no code.

## Stalls, and where they are

Two per floor from 5 down: the merchant and a vending machine. They are placed by
distance from each other with a preferred quadrant that rotates by floor, so no
two consecutive floors put them in the same place, and they are never fewer than
five rooms apart.

`_generate` retries on a duplicate draw rather than consuming the slot, and
guarantees two healing options plus one piece of kit — something that is neither
a potion nor a spare of what you are wearing. Late shelves run about two to three
heals, three to five buffs and sometimes a weapon.

## The travelling salesman

He appears in exactly one room per floor from Floor 2 on, always already set up.
Stock is drawn from that floor's `stock_f<n>` table with a random markup,
generated once and frozen into the save so browsing and coming back shows the
same shelf. He buys anything non-key back at a third, which is the only relief
valve on the twelve-slot inventory cap.

He also sells **carrying room**: `buy bag` adds two permanent inventory slots,
four times, at 120 / 220 / 380 / 600 paperclips. Twelve slots up to twenty. It is
the best thing in the shop and he knows it.

`buy <n>`, `buy bag`, `sell <item>`, `leave`.

He is the one NPC you are meant to return to, so his room says he is there every
time you walk back into it, not just the first time. Nothing else tells you the
shop can be reopened.

Charisma moves his prices either way, capped at a third, so a low-CHA class is
never priced out. The franchise multiplier is applied after that clamp on
purpose: the bonus is meant to beat the ordinary ceiling.

There is no HAGGLE verb — it is passive and always on. The stall says so:
`shop.haggle_note` prints one dim line above the hint naming the percentage
CHA is moving prices by, in whichever direction, and nothing at all when the
modifier is zero. It was invisible before, and the vending machine's hint said
it "does not haggle", which read as a promise that somewhere you could; that
line has been reworded and a regression test checks the percentage the line
prints against the price actually charged.

### He does not sell armour

Every floor has one themed set with AC matching the floor number, and it is in
that floor's own chests and on its senior. Armour is absent from every `stock_fN`
table — a stall is for what runs out: potions, and kit you cannot find lying
about. He still *buys* armour off you, which is where a spare set goes.

`test_stalls_sell_what_runs_out` pins it, and also checks each floor still hands
its armour out some other way, so this cannot quietly make a set unobtainable.

## Floor quirks

Each floor from 2 on has a standing rule that changes how it plays, announced
once on arrival. The whole vocabulary is data — see the docstring in
`engine/quirks.py`.

| Floor | Quirk |
|---|---|
| 2 | Every new room costs 2 paperclips on entry |
| 3 | Too dark to see exits from the doorway — LOOK first |
| 4 | Escape checks are 4 harder, stalkers move faster |
| 5 | Resting restores health but not ability uses |
| 6 | Nothing crits, including the Skirmisher's 19-20 |
| 7 | More of everything, arriving unpredictably; the prose runs a spectrum |
| 8 | 6 paperclips per room, escape 2 harder |
| 9 | The floor is in fragments; escape 2 easier |
| 10 | Text arrives at the speed of procedure |
| 11 | Rooms rewrite themselves between visits |
| 12 | Stalkers move twice as fast under the ash |
| 13 | Escape is 4 easier; nothing here wants to keep you |

Every floor also has a weighted random-event table that fires on room entry —
found paperclips, a hedge that bites, a console that logs an access four minutes
ago that was not yours. Several floors include a `flicker` event: half a second
of colour, then gone. Floor 7 is where that stops being a glitch.

## The map

Three tiers, all on the same MAP command:

| Tier | Item | Where | Shows |
|---|---|---|---|
| 1 | The Audit Trail | Floor 1, dispatch cage | last 10 steps |
| 2 | The Extended Log | Floor 4, from the Postman | last 20 steps |
| 3 | Full Disclosure | Floor 8, survey office | the whole floor |

Miss one and the merchant stocks it on later floors; he never stocks something
whose benefit you already have.

At every tier, the rooms beyond anywhere you have stood are drawn as well, marked
`o` explored or `?` not yet — standing in a junction tells you what the junction
connects to, because it does. Safe rooms show `+` in preference to their step
number: where the break room is matters more than how long ago you left it, and a
stall you have found shows `$` for the same reason — where you can spend
paperclips is one room out of forty and impossible to hold in your head.

Only the last nine steps are numbered (`TRAIL_LABELS`); anything further back is
just `o`. Stalkers render for free, because `Stalker.distance` means "rooms behind
you", which is a direct index into path history.

## Balance

### The early floors

Floor 1 is the gentlest floor and is meant to be, but it was far gentler than
that. Expressed as a safety ratio — rounds for the player to kill a common
monster against rounds for it to kill them, at the level the floor is actually
fought at — it sat at 7.1x against Floor 2's 5.2x, and signage alone was 13.3x.
Every class one-shot the three commonest monsters on a maximum damage roll from
level 1, before any crit.

Floor 1's seven monsters carry 1.30x health and +1 to hit, which brings the
floor to 5.0x. It stays the gentlest floor in the building and keeps its free
continue on death, but it no longer sits off the end of the curve.

### Seniors

An elite is `hp x ELITE_HP` and nothing else — a senior is bigger, not harder to
hit. `ELITE_HP` is 1.3, and `spawn()` leaves AC alone; a regression test asserts
that through `spawn()` rather than through a constant, so an AC bonus cannot come
back unnoticed.

The lever that matters here is accuracy, not damage. At AC 22 a geared player
connects about 40% of the time, which turns a fight into a potion treadmill: a
late heal restores about 31 HP against 20-plus incoming, so each potion buys
roughly a round and a long fight eats the whole bag. Tuning damage alone is
unstable, because the two damage rates are nearly equal — dropping the Rider's
damage 15% gives a median of 17 potions, dropping it 25% gives 2.

Measured on a geared level 17 vanguard, the deep seniors now run:

| Floor | Result |
|---|---|
| 10 | 3 potions, 16 rounds |
| 11 | 2-3 potions, 15 rounds |
| 12 | 5 potions, 19 rounds |
| 13 | 9 potions, 26 rounds |

Checked across all four classes.
`test_seniors_are_a_fight_not_a_potion_treadmill` pins it: no deaths geared, a
median under the potion cap, and under 30 rounds.

### Break rooms

Floors 6 and below run one break room per 11 to 16 rooms, which is the intended
sparseness on the short floors. Floors 7 to 13 carry four.

Counting them is not enough — positions are chosen by **walking distance**: keep
one refuge near the entrance, then repeatedly take whichever candidate room is
furthest from every break room chosen so far. That gives a minimum gap of four
steps on every floor, with nowhere more than five steps from one. Room numbers
are not a proxy for distance.

`test_the_long_floors_have_somewhere_to_stop` measures all of it by walking the
exit graph: the count, the closest pair, the worst-case walk, an early refuge and
one in the second half of the floor.

## Adding to it

Boss mechanics are data-driven and reusable: `damage_cap` + `cap_break_flag` (The
Cap), `summons` (Mrs Hensley), `regen` + `regen_max` (RAID-6, The Amendment),
`mirror` (The Likeness), `chaotic` (Kaleidon), `indemnify` (The Guarantor),
`paired_with` (Hemikin) and `phase_minigame` (Justice Vorn).

A floor needs rooms with a `pos`, monsters with `death` lines, an optional
`miniboss` and `elite`, a boss, and a minigame if you want one. Add the floor
JSON, add its strings to `theme.json`, run `validate.py`, play it.
