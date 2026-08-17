"""The travelling salesman.

He turns up in exactly one room per floor from Floor 2 onward, and he is
always already there when you arrive, which nobody has ever explained.

Stock is generated once per floor from that floor's stock table and then
frozen into state, so browsing, leaving and coming back shows the same shelf.
Prices come from the item definitions; he buys junk back at a third, which is
the only pressure valve on the inventory cap.
"""

from . import events as ev

SELL_DIVISOR = 3

# Bag upgrades. He sells space, escalating, because he is the only person in
# the building who understands that the cap is the real problem.
SLOTS_PER_UPGRADE = 2
UPGRADE_PRICES = [120, 220, 380, 600]


def haggle(state, price, selling=False):
    """Charisma moves the price, and the franchise improves what you are paid.

    Before this, CHA did nothing outside the Advocate's weapon stat and one
    rock-paper-scissors callout. Capped at a third either way, so a low-CHA
    class is never priced out of the shop. The sell multiplier is applied
    after that clamp on purpose: the franchise bonus is meant to beat the
    ordinary ceiling.
    """
    mod = max(-3, min(6, state.player.mod("cha")))
    shift = 1 + (mod * 0.05) * (1 if selling else -1)
    shift = max(0.67, min(1.33, shift))
    if selling:
        shift *= state.flags.get("sell_multiplier", 1.0)
    return max(1, int(round(price * shift)))


def currency_name(content, amount=1):
    key = "currency.one" if amount == 1 else "currency.many"
    return content.t(key)


def price_of(content, item_id):
    return content.item(item_id).get("price", 10)


def sell_value(content, item_id):
    item = content.item(item_id)
    if item.get("key_item"):
        return 0
    return max(1, item.get("sell", price_of(content, item_id) // SELL_DIVISOR))


def open_shop(state, content, rng, config):
    """Build (or restore) this stall's stock and enter shop mode.

    Keyed by seller as well as floor. It used to be floor alone, which was
    fine while the merchant was the only stall in the building — but a
    vending machine on the same floor then shared his stock and his sold-out
    flags, and whichever you reached first decided what the other one had.
    """
    npc = config.get("npc", "merchant")
    key = f"shop.{state.floor}" if npc == "merchant" else f"shop.{state.floor}.{npc}"
    stock = state.flags.get(key)
    if stock is None:
        stock = _generate(state, content, rng, config)
        state.flags[key] = stock
    state.shop = {"stock": stock, "npc": npc, "floor": state.floor,
                  # A machine sells; it does not haggle, buy your things or
                  # fit you a bigger bag.
                  "machine": bool(config.get("machine"))}
    return stock


def already_have(content, state, iid):
    """Would this be a dead purchase?

    Two ways an item can be spent money for nothing: it grants a flag you
    already carry, or it reveals a map tier you have already unlocked. The
    second was not checked, so `audit_trail` and `extended_log` kept turning
    up on the late stalls long after they were bought - and because they
    count as kit, an owned map upgrade could also satisfy the "one thing that
    is not a potion" guarantee, which is how a shelf ended up as heals and
    spare armour.
    """
    item = content.item(iid)
    flag = item.get("grants_flag")
    if flag and state.flags.get(flag):
        return True
    use = item.get("use") or {}
    return bool(use.get("op") == "reveal" and state.flags.get(use.get("flag")))


def _generate(state, content, rng, config):
    table = content.loot.get(config.get("stock_table", ""), {})
    picked = [i for i in table.get("always", [])
              if not already_have(content, state, i)]
    entries = [(e["item"], e["weight"]) for e in table.get("entries", [])]
    slots = config.get("slots", 5)
    # Retry rather than skip. A duplicate draw used to consume the slot, so a
    # table whose weight sits mostly on two or three healing items handed you
    # a stall of nothing but potions and left half the shelf empty.
    for _ in range(slots * 8):
        if len(picked) >= slots + len(table.get("always", [])):
            break
        item = rng.weighted(entries)
        if not item or item == "nothing" or item in picked:
            continue
        if already_have(content, state, item):
            continue          # you already have what this would give you
        picked.append(item)

    def _is_kit(iid):
        """Something other than a potion or a spare of what you are wearing:
        a buff, a map upgrade, a stat, a trick."""
        item = content.item(iid)
        use = item.get("use") or {}
        return (not item.get("slot")
                and use.get("op") not in (None, "heal", "heal_full"))

    # No armour guarantee, and no armour in the tables either. Every floor's
    # themed set is in its own chests and comes off its senior, so a stall
    # selling it too spent three of six slots on a thing you can wear one of.
    # A stall is for what runs out: potions and kit.
    def _is_heal(iid):
        return (content.item(iid).get("use") or {}).get("op") in (
            "heal", "heal_full")

    # Two healing options at least, so a stall is worth stopping at when the
    # bag is empty, which is the state you usually reach one in.
    while sum(1 for i in picked if _is_heal(i)) < 2:
        options = [item for item, _weight in entries
                   if _is_heal(item) and item not in picked]
        if not options:
            break
        picked.append(rng.choice(options))

    # And guarantee something that is not a potion. The late stalls carry
    # plenty of kit, but weight kept it off the shelf behind the heals.
    if not any(_is_kit(i) for i in picked):
        options = [item for item, _weight in entries
                   if _is_kit(item) and item not in picked
                   and not already_have(content, state, item)]
        if options:
            picked.append(rng.choice(options))
    stock = []
    for iid in picked:
        markup = rng.randint(90, 130) / 100.0
        stock.append({"item": iid,
                      "price": haggle(state, max(
                          1, int(price_of(content, iid) * markup))),
                      "sold": False})
    return stock


def upgrade_offer(state, content):
    """The next bag upgrade, or None once he is out of them."""
    bought = state.flags.get("bag_upgrades", 0)
    if bought >= len(UPGRADE_PRICES):
        return None
    return {
        "price": UPGRADE_PRICES[bought],
        "slots": SLOTS_PER_UPGRADE,
        "bought": bought,
        "name": content.t("shop.upgrade_name"),
        "desc": content.t("shop.upgrade_desc", slots=SLOTS_PER_UPGRADE,
                          cap=state.cap() + SLOTS_PER_UPGRADE),
        "affordable": state.currency >= UPGRADE_PRICES[bought],
    }


def buy_upgrade(state, content, rng, out):
    offer = upgrade_offer(state, content)
    if offer is None:
        out.append(ev.error(content.t("shop.upgrade_maxed")))
        return
    if state.currency < offer["price"]:
        out.append(ev.error(content.t("shop.too_dear",
                                      short=offer["price"] - state.currency,
                                      unit=currency_name(content, 2))))
        return
    state.currency -= offer["price"]
    state.flags["bag_upgrades"] = offer["bought"] + 1
    state.inventory_bonus += offer["slots"]
    out.append(ev.speech(seller_name(state, content),
                         content.t("shop.upgrade_line", rng)))
    out.append(ev.plain(content.t("shop.upgrade_done", slots=offer["slots"],
                                  cap=state.cap())))
    out.append(ev.currency_changed(-offer["price"], state.currency, "upgrade",
                                   currency_name(content, 2)))


def seller_name(state, content):
    """Whoever is actually running this stall.

    The lines used to be hardcoded to the merchant, which was true until
    there was a vending machine, and then the machine started talking like
    a man with a folding table. Falls back to the merchant when there is no
    open stall — sell() is reachable with state.shop still None.
    """
    npc = (state.shop or {}).get("npc") or "merchant"
    return content.t(f"npcs.{npc}.name")


def payload(state, content):
    lines = []
    for i, row in enumerate(state.shop["stock"], 1):
        item = content.item(row["item"])
        lines.append({
            "n": i,
            "id": row["item"],
            "name": content.t(item["name_key"]),
            "desc": content.t(item["desc_key"]),
            "price": row["price"],
            "sold": row["sold"],
            "affordable": state.currency >= row["price"],
        })
    sellable = []
    for entry in (() if state.shop.get("machine") else state.inventory):
        value = haggle(state, sell_value(content, entry["id"]), selling=True)
        if value <= 0:
            continue
        sellable.append({
            "id": entry["id"],
            "name": content.t(content.item(entry["id"])["name_key"]),
            "qty": entry["qty"],
            "value": value,
        })
    machine = state.shop.get("machine")
    return {
        "stock": lines,
        "sellable": sellable,
        "machine": bool(machine),
        "upgrade": None if machine else upgrade_offer(state, content),
        "slots_used": len(state.inventory),
        "slots_total": state.cap(),
        "currency": state.currency,
        "currency_name": currency_name(content, state.currency),
        "hint": content.t("shop.machine_hint" if machine else "shop.hint"),
        # Charisma has been moving every price on this shelf since the first
        # stall, and nothing ever said so. An invisible stat is not a stat.
        "haggle": None if machine else haggle_note(state, content),
    }


def haggle_note(state, content):
    """One line saying what charisma is doing to these prices, or nothing.

    Not a command — haggling is passive and always on. This exists because a
    player with CHA 16 pays a third less than one with CHA 8 and had no way
    to know it was happening, while the vending machine's own hint said it
    "does not haggle", which read as a promise that somewhere you could.
    """
    mod = max(-3, min(6, state.player.mod("cha")))
    if mod == 0:
        return None
    # Same arithmetic as haggle(), reported rather than applied.
    shift = max(0.67, min(1.33, 1 - mod * 0.05))
    percent = int(round(abs(1 - shift) * 100))
    if percent == 0:
        return None
    key = "shop.haggle_good" if mod > 0 else "shop.haggle_bad"
    return content.t(key, percent=percent)


def buy(state, content, rng, arg, out):
    if (arg or "").strip().lower() in ("bag", "slots", "space", "upgrade"):
        buy_upgrade(state, content, rng, out)
        return
    stock = state.shop["stock"]
    row = _match(stock, content, arg)
    if row is None:
        out.append(ev.error(content.t("shop.no_such")))
        return
    if row["sold"]:
        out.append(ev.error(content.t(
            "shop.machine_already_sold" if (state.shop or {}).get("machine")
            else "shop.already_sold")))
        return
    if state.currency < row["price"]:
        out.append(ev.error(content.t("shop.too_dear",
                                      short=row["price"] - state.currency,
                                      unit=currency_name(content, 2))))
        return
    item = content.item(row["item"])
    # A full bag is a full bag of *slots*. Buying a second healing draught
    # when you already carry one stacks onto that entry and costs no slot,
    # so refusing it was wrong: it locked you out of the one purchase that
    # a full bag most often needs.
    # Deliberately not has_item(): that also matches keepsakes, which live
    # outside the bag and would not stack.
    stacks = any(e["id"] == row["item"] for e in state.inventory)
    if state.inventory_full() and not item.get("key_item") and not stacks:
        out.append(ev.error(content.t("shop.no_room")))
        return

    state.currency -= row["price"]
    row["sold"] = True
    state.add_item(row["item"])
    if item.get("grants_flag"):
        state.flags[item["grants_flag"]] = True
    name = content.t(item["name_key"])
    out.append(ev.speech(seller_name(state, content),
                         content.t("shop.machine_sold_line"
                                   if (state.shop or {}).get("machine")
                                   else "shop.sold_line", rng, name=name)))
    out.append(ev.currency_changed(-row["price"], state.currency, "bought",
                              currency_name(content, 2)))


def sell(state, content, rng, arg, out):
    if (state.shop or {}).get("machine"):
        # The machine's own hint says it does not want your things, so it
        # had better not take them.
        out.append(ev.error(content.t("shop.machine_wont_buy")))
        return
    match = None
    for entry in state.inventory:
        item = content.item(entry["id"])
        name = content.t(item["name_key"]).lower()
        if arg.lower() in (entry["id"], name) or (arg and arg.lower() in name):
            match = entry
            break
    if match is None:
        out.append(ev.error(content.t("errors.no_item")))
        return
    value = haggle(state, sell_value(content, match["id"]), selling=True)
    if value <= 0:
        out.append(ev.error(content.t("shop.wont_buy")))
        return
    name = content.t(content.item(match["id"])["name_key"])
    state.remove_item(match["id"])
    state.currency += value
    # No equipped-slot bookkeeping here on purpose. Equipping removes the item
    # from the bag, so what `sell` matched can never BE the equipped one - and
    # the old "unequip if the last copy is gone" check did exactly the wrong
    # thing: selling a spare slag plate stripped the slag plate you were
    # wearing, because after removing the spare `has_item` went False.
    # `unequip` is how gear comes off.
    out.append(ev.speech(seller_name(state, content),
                         content.t("shop.bought_line", rng, name=name)))
    out.append(ev.currency_changed(value, state.currency, "sold",
                              currency_name(content, 2)))


def _match(stock, content, arg):
    arg = (arg or "").strip().lower()
    if arg.isdigit():
        idx = int(arg) - 1
        return stock[idx] if 0 <= idx < len(stock) else None
    for row in stock:
        name = content.t(content.item(row["item"])["name_key"]).lower()
        if arg == row["item"] or arg == name:
            return row
    for row in stock:
        name = content.t(content.item(row["item"])["name_key"]).lower()
        if arg and arg in name:
            return row
    return None


def award(state, content, rng, low, high, out, reason="loot"):
    amount = rng.randint(low, high)
    if amount <= 0:
        return
    state.currency += amount
    out.append(ev.currency_changed(amount, state.currency, reason,
                                   currency_name(content, 2)))
