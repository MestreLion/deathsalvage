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
# ln -s /PATH/TO/pymclevel /PATH/TO/THIS/SCRIPT

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
import argparse
import logging
from xdg.BaseDirectory import xdg_cache_home
import copy


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
    parser = argparse.ArgumentParser(
        description="Creates a Chest of items dropped after death",)

    parser.add_argument('--quiet', '-q', dest='loglevel',
                        const=logging.WARNING, default=logging.INFO,
                        action="store_const",
                        help="Suppress informative messages.")

    parser.add_argument('--verbose', '-v', dest='loglevel',
                        const=logging.DEBUG,
                        action="store_const",
                        help="Verbose mode, output extra info.")

    parser.add_argument('--world', '-w', default="newworld",
                        help="Minecraft world, either its 'level.dat' file"
                            " or a name under '~/.minecraft/saves' folder."
                            " [Default: %(default)s]")

    parser.add_argument('--player', '-p', default="Player",
                        help="Player name."
                            " [Default: %(default)s]")

    return parser.parse_args(args)


def load_world(name):
    import pymclevel  # takes a long time, so only imported after argparse
    if isinstance(name, pymclevel.MCLevel):
        return name

    try:
        if osp.isfile(name):
            return pymclevel.fromFile(name)
        else:
            return pymclevel.loadWorld(name)
    except IOError as e:
        raise PyMCLevelError(e)
    except pymclevel.mclevel.LoadingError:
        raise PyMCLevelError("Not a valid Minecraft world: '%s'" % name)


def get_player(world, playername=None):
    import pymclevel
    if playername is None:
        playername = "Player"
    try:
        return world.getPlayerTag(playername)
    except pymclevel.PlayerNotFound:
        raise PyMCLevelError("Player not found in world '%s': %s" % (world.LevelName, playername))


def get_itemtypes():
    from pymclevel.items import items as ItemTypes
    for id, stacksize in (( 58, 64),  # Workbench (Crafting Table)
                          (116, 64),  # Enchantment Table
                          (281, 64),  # Bowl
                          (282,  1),  # Mushroom Stew
                          (324,  1),  # Wooden Door
                          (337, 64),  # Clay (Ball)
                          (344, 16),  # Egg
                          (345, 64),  # Compass
                          (347, 64),  # Clock
                          (379, 64),  # Brewing Stand
                          (380, 64),  # Cauldron
                          (395, 64),  # Empty Map
                          ):
        ItemTypes.findItem(id).stacksize = stacksize
    for _, item in sorted(ItemTypes.itemtypes.iteritems()):
        if item.maxdamage is not None:
            item.stacksize = 1
    return ItemTypes


def get_itemkey(item):
    return (item["id"].value,
            item["Damage"].value)







def stack_item(item, stacks, itemtypes=None):
    '''Append an item to a list, trying to stack with other items
        respecting item's max stack size
        Raises ValueError if item count <= max stack size
    '''
    if itemtypes is None:
        itemtypes = get_itemtypes()

    key = get_itemkey(item)
    size = itemtypes.findItem(*key).stacksize
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
        if get_itemkey(stack) == key and stack["Count"].value < size:
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

    from pymclevel import nbt

    try:
        world = load_world(args.world)
        player = get_player(world, args.player)

        if not player["Dimension"].value == 0:  # 0 = Overworld
            world = world.getDimension(player["Dimension"].value)

        itemtypes = get_itemtypes()
        inventory = player["Inventory"]
        slots = free_slots(inventory, armor=True)
        armorslots = {i: 103 - ((i - 298) % 4) for i in xrange(298, 318)}

        items = []

        for chunk in world.getChunks():
            dirtychunk = False
            for entity in chunk.Entities:
                if entity["id"].value == "Item" and entity["Age"].value < 6000:
                    stack_item(entity["Item"], items, itemtypes)
                    # Destroy the item
                    entity["Age"].value = 6000
                    entity["Health"].value = 0
                    dirtychunk = True

                # For mobs that can pick up loot, assume their equipment is *your* loot ;)
                elif ("Equipment" in entity
                      and "CanPickUpLoot" in entity
                      and entity["CanPickUpLoot"].value == 1):
                    printed = False
                    for i, equip in enumerate(entity["Equipment"]):
                        if len(equip) > 0 and not (entity["id"].value == "PigZombie" and
                                                   equip["id"].value == 283):  # Golden Sword:
                            if not printed:
                                print entity["id"].value
                                printed = True
                            print "\t", itemtypes.findItem(equip["id"].value)
                            stack_item(equip, items, itemtypes)
                            # Remove the equipment
                            entity["Equipment"][i] = nbt.TAG_Compound()
                            dirtychunk = True

            if dirtychunk:
                chunk.chunkChanged(calcLighting=False)

        save = False
        for item in sorted(items, key=get_itemkey):
            if not slots:
                break

            slot = armorslots.get(item["id"].value, None)
            if slots is not None and slot in slots:
                slots.remove(slot)
            else:
                slot = slots.pop(0)

            item["Slot"] = nbt.TAG_Byte(slot)
            key = get_itemkey(item)
            type = itemtypes.findItem(*key)
            log.info("(%3d, %4d)\t%3d (%2d)\t%3d\t%s" % (
               key[0],
               key[1],
               item["Count"].value,
               type.stacksize,
               slot,
               type.name,
            ))

            inventory.append(item)
        else:
            save = True

        if not save:
            log.warn("No more free slots, aborting!")
            return

        world.saveInPlace()


    except (PyMCLevelError, LookupError, IOError) as e:
        log.error(e)
        return


class PyMCLevelError(Exception):
    pass


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
