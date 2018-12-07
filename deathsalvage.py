#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#    Copyright (C) 2014 Rodrigo Silva (MestreLion) <linux@rodrigosilva.com>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program. See <http://www.gnu.org/licenses/gpl.html>

# Installing requirements in Debian/Ubuntu:
# ln -s /PATH/TO/pymctoolslib /PATH/TO/THIS/SCRIPT

"""Salvages dropped items after death back to Inventory

Ideas:

- Add items to category lists: Items, XPOrb, Mob Equip.
    log.info() only at final report, log.debug() in loop

- Add failed items to a dict, also for final report:
    fail[item.id] = fail.setdefault(item.id, 0) + remainder

- Add to Ender Chest (if --ender-chest/-e) when no space in regular inventory

- Final report:
    - Inventory after salvage
    - Failed items count

- XP Absorb: 1 level every 7 XP, rounded up, as it happens on death

- Update setuplogging(), move to pymctoolslib
"""

import sys
import os
import os.path as osp
import logging
from xdg.BaseDirectory import xdg_cache_home
import operator
import itertools
import math

import pymctoolslib as mc


if __name__ == '__main__':
    myname = osp.basename(osp.splitext(__file__)[0])
else:
    myname = __name__

log = logging.getLogger(myname)


# Sword, Tools (including Hoe), Armor
DIAMOND_ITEMS = set(_.fullstrid for _ in mc.ItemTypes.searchItems('diamond'))
IRON_ITEMS    = set(_.fullstrid for _ in mc.ItemTypes.searchItems('iron'))

XP_IDS = set((
    "XPOrb",                    # up to 1.9
    "minecraft:xp_orb",         # 1.11+
    "minecraft:experience_orb"  # 1.13+
))


def setuplogging(level):
    # Console output
    for logger, lvl in [
            (log, level),
            (logging.getLogger("pymctoolslib.pymctoolslib"), level),
            # pymclevel is too verbose
            (logging.getLogger("pymctoolslib.pymclevel"), logging.WARNING),
    ]:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        sh.setLevel(lvl)
        logger.addHandler(sh)

    # File output
    logger = logging.getLogger()  # root logger, so it also applies to pymclevel
    logger.setLevel(logging.DEBUG)  # set to minimum so it doesn't discard file output
    try:
        logdir = osp.join(xdg_cache_home, 'minecraft')
        if not osp.exists(logdir):
            os.makedirs(logdir)
        fh = logging.FileHandler(osp.join(logdir, "%s.log" % myname))
        fh.setFormatter(logging.Formatter('%(asctime)s\t%(levelname)s\t%(name)s\t%(message)s'))
        fh.setLevel(logging.DEBUG)
        logger.addHandler(fh)
    except IOError as e:  # Probably access denied
        logger.warn("%s\nLogging will not work.", e)


def parseargs(args=None):
    parser = mc.basic_parser(
        description="Recover all items dropped after death"
                    " back to the player's inventory")

    for c in ("x", "z"):
        parser.add_argument('--%spos' % c, '-%s' % c, dest='%spos' %c,
                            default=None, type=int,
                            help="Approximate death %s coordinate"
                                " to search for death location." % c.upper())

    parser.add_argument('--radius', '-r', dest='radius', default=250, type=int,
                        help="Radius of the search for items, centered on (X,Z)."
                            " Ignored if both --xpos and --zpos are not specified."
                            " [Default: %(default)s]")

    parser.add_argument('--death-xz', '-d', dest='deathpos', metavar='COORD',
                        default=None, type=int, nargs=2,
                        help="Exact death X and Z coordinates"
                            " to salvage dropped items.")

    parser.add_argument('--apply', '-a', dest='apply',
                        default=False,
                        action="store_true",
                        help="Apply changes.")

    return parser.parse_args(args)


class Position(object):
    @classmethod
    def from_xz(cls, x, z):
        return cls.from_xzy(x, z, 0)

    @classmethod
    def from_xzy(cls, x, z, y):
        pos = cls()
        pos.coords = (x, z, y)
        return pos

    def __init__(self, entity=None):
        if entity is not None:
            self.coords = tuple(entity["Pos"][_].value for _ in (0, 2, 1))

    @property
    def x(self):
        return self.coords[0]

    @property
    def z(self):
        return self.coords[1]

    @property
    def y(self):
        return self.coords[2]

    @property
    def xz(self):
        return self.coords[0:2]

    def __str__(self):
        return "(%5d, %5d, %3d)" % self.coords


def centroid(points, sd_goal=10, sd_filter=1):
    x, z, y, w, _ = zip(*points)
    size = sum(w)         # sum of weights
    length = len(points)  # number of points
    center = tuple(sum(itertools.imap(operator.mul, _, w)) / size
                   for _ in (x, z, y))
    distances = tuple(math.sqrt(sum((center[_]-_p[_])**2
                                    for _ in (0, 1)))  # ignoring y
                      for _p in points)
    sd = math.sqrt(sum(_**2 for _ in distances) / length)  # not weighted

    centerpos = Position.from_xzy(*center)

    log.debug("Centroid of %2d items: %s, StdDev: %4.1f", length, centerpos, sd)

    if sd > sd_goal:
        points = [_p for _p, _d in zip(points, distances) if _d/sd < sd_filter]
        if len(points) < length:
            return centroid(points)

    return centerpos


class Inventory(object):
    def __init__(self, player):
        self.inventory = player["Inventory"]

        if len(self.inventory) == 40:  # shortcut for full inventory
            self.free_slots = []
            self.free_armor = []
        else:
            slots = set(_["Slot"].value for _ in self.inventory)
            self.free_slots = sorted(set(range(36))       - slots)
            self.free_armor = sorted(set(range(100, 104)) - slots)

    def stack_item(self, item, wear_armor=True):
        '''Add an item clone to the inventory, trying to stack it with other
            items according to item's max stack size. Original item is never
            changed.
            Raises ValueError if item count is zero or is than max stack size.
            Return a 3-tuple (count_remaining, [slots, ...], [counts, ...])
        '''
        item = item.clone()

        size = item.type.stacksize
        count = item["Count"]  # item.count will not be changed until fully stacked

        # Assertions
        if count == 0:
            raise ValueError("Item count is zero: %s" % item)

        if count > size:
            raise ValueError(
                "Item count is greater than max stack size (%d/%d): %s" %
                (count, size, item))

        # Shortcut 1-stack items like tools, armor, weapons, etc
        if size == 1:
            try:
                return 0, [(self.add_item(item, wear_armor, clone=True), 1)]
            except mc.MCError:
                return count, []

        # Loop each inventory slot, stacking the item onto similar items
        # that are not maximized until item count is 0
        slots  = []
        for stack in self.inventory:
            stack = mc.Item(stack)
            if (stack.key == item.key and
                stack.name == item.name and  # avoid stacking named items
                stack["Count"] < size):

                total = stack["Count"] + count
                diff = min(size, total) - stack["Count"]
                stack["Count"] += diff
                count          -= diff

                slots.append((stack["Slot"], diff))

                if count == 0:
                    break

        if count > 0:
            item["Count"] = count
            try:
                slots.append((self.add_item(item, wear_armor, clone=False),
                              count))
                count = 0
            except mc.MCError:
                pass

        return count, slots

    def add_item(self, item, wear_armor=True, clone=True):
        """Add an item (or a clone) to a free inventory slot.
            Return the used slot space, if any, or raise mc.MCError
        """
        from pymctoolslib.pymclevel import nbt
        e = mc.MCError("No suitable free inventory slot to add %s" %
                       item.description)

        # shortcut for no free slots
        if not self.free_slots and not self.free_armor:
            raise e

        # Get a free slot suitable for the item
        # For armor, try to wear in its corresponding slot
        slot = None
        if wear_armor and item.type.is_armor:
            slot = item.type.armorslot
            if slot in self.free_armor:
                self.free_armor.remove(slot)
            else:
                # Corresponding armor slot is not free
                slot = None

        if slot is None:
            if not self.free_slots:
                raise e

            slot = self.free_slots.pop(0)

        # Add the item
        itemnbt = item.get_nbt()
        if clone:
            itemnbt = item.copy()
        # TODO: check with nbt/NbtObject how to properly add a new tag
        itemnbt["Slot"] = nbt.TAG_Byte(slot)
        self.inventory.append(itemnbt)

        return slot


def mob_name(entity):
    eid = entity["id"].value

    if eid == "Zombie":
        if "IsVillager" in entity and entity["IsVillager"].value == 1:
            return "Zombie Villager"

    if eid == "PigZombie":
        return "Zombie Pigman"

    if eid == "Skeleton":
        if entity["SkeletonType"].value == 1:
            return "Wither Skeleton"

    return eid


def iter_mob_loot(entity, ordinary=False):
    if not ("Equipment" in entity
            and "CanPickUpLoot" in entity
            and entity["CanPickUpLoot"].value == 1):
        return

    for i, equip in enumerate(entity["Equipment"]):
        if len(equip) == 0:  # blank equipment slot
            continue

        # Do not list ordinary equipment unless requested
        if not (ordinary or 'tag' in equip):

            if (entity["id"].value == "Zombie" and
                equip["id"].value not in DIAMOND_ITEMS):
                continue

            if (entity["id"].value == "PigZombie" and
                equip["id"].value == 283):  # Golden Sword
                continue

            if (entity["id"].value == "Skeleton" and
                equip["id"].value in (261,    # Bow
                                      272)):  # Stone Sword (Wither Skeleton)
                continue

        yield i, mc.Item(equip)


def xp_next(level, version=(1,11,2)):
    """Return the amount of XP needed to go from a level to the next one"""
    if version >= (1, 8):
        consts = ((31, 9, -158), (16, 5, -38), (0, 2,  7))
    else:
        consts = ((31, 7, -148), (16, 3, -28), (0, 0, 17))

    for c in consts:
        if level >= c[0]: return c[1] * level + c[2]


def add_xp(player, xp):
    """Add an experience amount to a player, also affecting his score and
        possibly gaining levels
        Return the updated level and the percentage towards the next level
    """
    level, xpp = (player[_].value for _ in ("XpLevel", "XpP"))

    xpp += float(xp) / xp_next(level)
    while xpp >= 1:
        xpp = (xpp - 1) * xp_next(level)
        level += 1
        xpp /= xp_next(level)

    player["XpTotal"].value += xp
    player["Score"  ].value += xp
    player["XpLevel"].value  = level
    player["XpP"    ].value  = xpp

    return level, xpp


def add_item_weight(points, item, pos):
    # Weight named and enchanted items as large size XP Orb
    if 'tag' in item:
        points.append(pos.coords + (37, item))

    # Diamond items as medium size
    elif item["id"] in DIAMOND_ITEMS:
        points.append(pos.coords + (17, item))

    # Iron items as small size
    elif item["id"] in IRON_ITEMS:
        points.append(pos.coords + (11, item))


def main(argv=None):
    args = parseargs(argv)
    setuplogging(args.loglevel)
    log.debug(args)

    from pymctoolslib.pymclevel import nbt

    world, player = mc.load_player_dimension(args.world, args.player)

    log.info("Determining '%s' death coordinates in world '%s' [%s]",
             args.player, world.LevelName, world.filename)

    if player["Health"].value == 0 and player["DeathTime"].value > 0:
        deathpos = Position(player)
        log.warn("Player is currently dead at %s", deathpos)
        log.warn("Not salvaging items, as inventory is cleared after respawn")
        log.warn("Enter the game, respawn, save and quit, then run this again"
                 " with argument '--death-xz %d %d'",
                 deathpos.x, deathpos.z)
        return

    elif args.deathpos:
        deathpos = Position.from_xz(*args.deathpos)
        log.info("Death coordinates specified at %s", deathpos.xz)

    else: # XP Orbs center, named item, etc...
        searchpos = Position.from_xz(args.xpos, args.zpos)
        if args.xpos and args.zpos:
            msg = ("Searching entities around %s with radius %d",
                   searchpos.xz, args.radius)
        else:
            msg = ("Searching entities on the entire world",)
        log.info(*msg)
        log.debug("(%5s, %5s, %3s)\t%4s\t%2s %s",
                  "X", "Z", "Y", "Age", "Qt", "Item")

        points = []

        for chunk in mc.iter_chunks(world, searchpos.x, searchpos.z, args.radius,
                                    progress = args.loglevel==logging.INFO):
            for entity in chunk.Entities:
                pos = Position(entity)

                if entity["id"].value in ("Item", "minecraft:item"):
                    item = mc.Item(entity["Item"])
                    log.debug("%s\t%4d\t%s",
                       pos,
                       entity["Age"].value,
                       item.description,
                    )
                    add_item_weight(points, item, pos)

                elif entity["id"].value in XP_IDS:
                    log.debug("%s\t%4d\t   XP Orb worth %3d XP",
                       pos,
                       entity["Age"].value,
                       entity["Value"].value,
                    )
                    points.append(pos.coords + (entity["Value"].value, mc.XpOrb(entity)))

                for i, (idx, equip) in enumerate(iter_mob_loot(entity)):
                    if i == 0:  # first "interesting" equipment item
                        log.debug("%s %s equipped with:",
                                  pos, mob_name(entity))
                    log.debug("%s%s", 33 * ' ', equip.description)
                    add_item_weight(points, equip, pos)

        if points:
            log.info("Interesting entities and weights to find death location:")
            [log.info("%s - Weight %3d - %s", Position.from_xzy(*_[:3]), _[3], _[4])
             for _ in points]
            deathpos = centroid(points)
            log.info("Estimated death location is %s",
                     deathpos)
        else:
            log.error("Could not determine player death coordinates")
            return

    inventory = Inventory(player)

    for chunk in mc.iter_chunks(world, deathpos.x, deathpos.z, 10,
                                progress=False):
        dirtychunk = False
        removal = set()

        for idx, entity in enumerate(chunk.Entities):
            pos = Position(entity)

            if entity["id"].value in ("Item", "minecraft:item"):
                item = mc.Item(entity["Item"])

                # Stack the item to inventory
                remaining, slots = inventory.stack_item(item)

                for slot, count in slots:
                    log.info("%s %4d Added to inventory [slot %3d]: %2d %s",
                             pos, entity["Age"].value, slot, count, item.fullname)

                if remaining == 0:
                    # Fully added, mark the entity for removal
                    # Must not pop the entity while iterating over the list
                    removal.add(idx)

                else:
                    log.warn("%s %4d No suitable free inventory slot to add: %2d %s",
                             pos, entity["Age"].value, remaining, item.fullname)

                    if not slots:
                        # Partially added
                        dirtychunk = True
                        item["Count"] = remaining

            elif entity["id"].value in XP_IDS:
                log.info("%s %4d Absorbed XP Orb worth %3d XP, level %.2f",
                         pos, entity["Age"].value, entity["Value"].value,
                         sum(add_xp(player, entity["Value"].value)))
                removal.add(idx)

            # For mobs that can pick up loot,
            # assume their non-ordinary equipment is *your* loot ;)
            for i, equip in iter_mob_loot(entity):
                try:
                    slot = inventory.add_item(equip)
                except mc.MCError as e:
                    log.warn(e)
                    continue

                log.info("%s      Added to inventory [slot %3d], from %s: %s",
                         pos, slot, mob_name(entity), equip.fullname)

                # Remove the equipment
                entity["Equipment"][i] = nbt.TAG_Compound()
                dirtychunk = True

        if removal:
            dirtychunk = True
            chunk.Entities[:] = [entity
                                 for idx, entity in enumerate(chunk.Entities)
                                 if idx not in removal]

        if dirtychunk:
            chunk.chunkChanged(calcLighting=False)

    if args.apply:
        log.info("Applying changes and saving world...")
        world.saveInPlace()
    else:
        log.warn("Not saving world, use --apply to apply changes")




if __name__ == '__main__':
    try:
        sys.exit(main())
    except mc.MCError as e:
        log.error(e)
        sys.exit(1)
    except Exception as e:
        log.critical(e, exc_info=True)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
