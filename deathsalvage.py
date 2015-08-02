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
- class Inventory(object)
    def init(player)
    def stack_item(item, partial=True, armor=True)
        return remainder(int), counts(list of ints), slots(list of ints)
    def add_item(item, armor=True)
        return slot

- for i, entity in enumerate(list(entities)):
    if item:
        remainder, counts, _ = inventory.stack_item(item...)
        if counts:
            dirty = True
        if remainder == 0:
            entities.pop(i)
        else:
            fail[item.key] = fail.setdefault(item.key, 0) + remainder
    if mob:
        for j, equip in enumerate(equipment)
        slot = invetory.add_item(item...)
        if slot:
            equipment[j] = nbt.tag_compund()  # empty
        else:
            fail[item.key] = fail.setdefault(item.key, 0) + 1

- No such thing as full inventory: may fail for one item and succeed the next

- Final report:
    - Inventory after salvage
    - Failed items count

- XPOrb absorb?
- XPOrb as death location: set.add(chunk +0+0, -1-1, +1+1, -1+0, ...) 9 chunks

"""

import sys
import os
import os.path as osp
import logging
from xdg.BaseDirectory import xdg_cache_home
import copy

import pymctoolslib as mc


if __name__ == '__main__':
    myname = osp.basename(osp.splitext(__file__)[0])
else:
    myname = __name__

log = logging.getLogger(myname)


def setuplogging(level):
    # Console output
    for logger, lvl in [(log, level),
                        # pymclevel is too verbose
                        (logging.getLogger("pymclevel"), logging.WARNING)]:
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
                            help="Death %s coordinate"
                                "to search for dropped items." % c.upper())

    parser.add_argument('--radius', '-r', dest='radius', default=250, type=int,
                        help="Radius of the search for items, centered on (X,Z)."
                            " Ignored if both --xpos and --zpos are not specified."
                            " [Default: %(default)s]")

    return parser.parse_args(args)


def stack_item(item, stacks):
    '''Append an item to a list, trying to stack with other items
        respecting item's max stack size
        Raises ValueError if item count >= max stack size
    '''
    key = mc.get_itemkey(item)
    size = mc.item_type(item).stacksize
    count = item["Count"].value

    # Assertion
    if count > size:
        raise ValueError("Item count is greater than max stack size (%d/%d): %s"
                         % (count, size, item))

    # Shortcut for fully stacked items (and 1-stack items like tools, armor, weapons)
    if count == size:
        stacks.append(copy.deepcopy(item))
        return

    for stack in stacks:
        if mc.get_itemkey(stack) == key and stack["Count"].value < size:
            total = stack["Count"].value + count

            # Stack item onto another, fully absorbing it
            if total <= size:
                stack["Count"].value = total
                return

            # Stack item onto another, max stack
            stack["Count"].value = size
            count = total - size
            break

    if count > 0:
        item = copy.deepcopy(item)
        item["Count"].value = count
        stacks.append(item)


def main(argv=None):

    args = parseargs(argv)
    setuplogging(args.loglevel)
    log.debug(args)

    from pymctoolslib.pymclevel import nbt

    try:
        world = mc.load_world(args.world)
        player = mc.get_player(world, args.player)
        if not player["Dimension"].value == 0:  # 0 = Overworld
            world = world.getDimension(player["Dimension"].value)

    except mc.MCError as e:
        log.error(e)
        return

    items  = []
    equips = []
    xporbs = []

    xavg = zavg = 0

    log.info("Reading '%s' chunk data from '%s'",
             world.LevelName, world.filename)
    log.debug("(%5s, %5s, %3s)\t%4s\t%3s %s", "X", "Z", "Y", "Age", "Qtd", "Item")

    for chunk in mc.iter_chunks(world, args.xpos, args.zpos, args.radius,
                                args.loglevel == logging.INFO):
        dirtychunk = False
        for entity in chunk.Entities:
            if entity["id"].value == "Item" and entity["Age"].value < 6000:
                log.debug("(%5d, %5d, %3d)\t%4d\t%3d %s" % (
                   entity["Pos"][0].value,
                   entity["Pos"][2].value,
                   entity["Pos"][1].value,
                   entity["Age"].value,
                   entity["Item"]["Count"].value,
                   mc.item_name(entity["Item"]),
                ))
                items.append(entity)

                # group with the list
                stack_item(entity["Item"], items)

                # Destroy the item
                entity["Age"].value = 6000
                entity["Health"].value = 0
                dirtychunk = True

            elif entity["id"].value == "XPOrb":
                log.debug("(%5d, %5d, %3d)\t%4d\t%3d XP Orb" % (
                   entity["Pos"][0].value,
                   entity["Pos"][2].value,
                   entity["Pos"][1].value,
                   entity["Age"].value,
                   entity["Value"].value,
                ))
                xavg += entity["Pos"][0].value
                zavg += entity["Pos"][2].value
                xporbs.append(entity)

            # For mobs that can pick up loot, assume their equipment is *your* loot ;)
            elif ("Equipment" in entity
                  and "CanPickUpLoot" in entity
                  and entity["CanPickUpLoot"].value == 1):
                firstequip = True
                for i, equip in enumerate(entity["Equipment"]):
                    if len(equip) > 0 and not (entity["id"].value == "PigZombie" and
                                               equip["id"].value == 283):  # Golden Sword:
                        if firstequip:
                            firstequip = False
                            log.debug("(%5d, %5d, %3d) %s equipped with:",
                                      entity["Pos"][0].value,
                                      entity["Pos"][2].value,
                                      entity["Pos"][1].value,
                                      entity["id"].value)

                        log.debug("%s%s", 37 * ' ', mc.item_name(equip))
                        stack_item(equip, items)

                        # Remove the equipment
                        entity["Equipment"][i] = nbt.TAG_Compound()
                        dirtychunk = True

        if dirtychunk:
            chunk.chunkChanged(calcLighting=False)

    for _ in sorted(items, key=mc.get_itemkey):
        pass

    #world.saveInPlace()


def free_slots(inventory, armor=False):
    if len(inventory) == 40:  # shortcut for full inventory
        return []

    slots = range(36) + (range(100, 104) if armor else [])
    for item in inventory:
        slot = item["Slot"].value
        if slot in slots:
            slots.remove(slot)
    return slots




if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        log.critical(e, exc_info=True)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
