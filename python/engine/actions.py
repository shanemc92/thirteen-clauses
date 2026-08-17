"""Actions are everything a frontend is allowed to submit."""

from dataclasses import dataclass, field


@dataclass
class Action:
    kind: str
    arg: str = ""
    extra: dict = field(default_factory=dict)


def move(direction):        return Action("Move", direction)
def look():                 return Action("Look")
def show_map():             return Action("Map")
def sheet():                return Action("Sheet")
def portrait():             return Action("Portrait")
def record():               return Action("Record")
def buy(what):              return Action("Buy", what)
def sell(what):             return Action("Sell", what)
def leave_shop():           return Action("LeaveShop")
def inventory():            return Action("Inventory")
def take(target):           return Action("Take", target)
def read(target=""):        return Action("Read", target)
def use(item_id):           return Action("Use", item_id)
def drop(item_id):          return Action("Drop", item_id)
def equip(item_id):         return Action("Equip", item_id)
def unequip(what=""):       return Action("Unequip", what)
def talk():                 return Action("Talk")
def rest():                 return Action("Rest")
def attack(target=""):      return Action("Attack", target)
def ability(aid, target=""):return Action("Ability", aid, {"target": target})
def flee():                 return Action("Flee")
def minigame(choice):       return Action("Minigame", choice)
def choose(option):         return Action("Choose", option)
def spend_continue():       return Action("Continue")
def wait():                 return Action("Wait")
def withdraw():             return Action("Withdraw")
def sing():                 return Action("Sing")
def photo():                return Action("Photo")
def explain(topic):         return Action("Explain", topic)
def abilities(want=""):     return Action("Abilities", want)
def memos(want=""):         return Action("Memos", want)
def setting(key, value):    return Action("Setting", key, {"value": value})
