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
import time


import progressbar


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
        description="Recover all items dropped after death back to the player's inventory",)

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

    parser.add_argument('--xpos', '-x', dest='xpos', default=None, type=int,
                        help="Death X coordinate to search for dropped items.")
    parser.add_argument('--zpos', '-z', dest='zpos', default=None, type=int,
                        help="Death Z coordinate to search for dropped items.")
    parser.add_argument('--radius', '-r', dest='radius', default=250, type=int,
                        help="Radius of the search for items, centered on (X,Z)."
                            " Ignored if both --xpos and --zpos are not specified."
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
        raise PyMCLevelError("Player not found in world '%s': %s" %
                             (world.LevelName, playername))


_ItemTypes = None
def item_type(item):
    '''Wrapper to pymclevel Items.findItem() with corrected data'''
    global _ItemTypes
    if _ItemTypes is None:
        from pymclevel.items import items as ItemTypes

        for itemid, maxdamage in ((298,  56),  # Leather Cap
                                  (299,  81),  # Leather_Tunic
                                  (300,  76),  # Leather_Pants
                                  (301,  66),  # Leather_Boots
                                  (302, 166),  # Chainmail_Helmet
                                  (303, 241),  # Chainmail_Chestplate
                                  (304, 226),  # Chainmail_Leggings
                                  (305, 196),  # Chainmail_Boots
                                  (306, 166),  # Iron_Helmet
                                  (307, 241),  # Iron_Chestplate
                                  (308, 226),  # Iron_Leggings
                                  (309, 196),  # Iron_Boots
                                  (310, 364),  # Diamond_Helmet
                                  (311, 529),  # Diamond_Chestplate
                                  (312, 496),  # Diamond_Leggings
                                  (313, 430),  # Diamond_Boots
                                  (314,  78),  # Golden_Helmet
                                  (315,  87),  # Golden_Chestplate
                                  (316,  76),  # Golden_Leggings
                                  (317,  66),  # Golden_Boots
                                  ):
            ItemTypes.findItem(itemid).maxdamage = maxdamage - 1

        for itemid, stacksize in ((58,  64),  # Workbench (Crafting Table)
                                  (116, 64),  # Enchantment Table
                                  (281, 64),  # Bowl
                                  (282,  1),  # Mushroom Stew
                                  (324,  1),  # Wooden Door
                                  (337, 64),  # Clay (Ball)
                                  (344, 16),  # Egg
                                  (345, 64),  # Compass
                                  (347, 64),  # Clock
                                  (368, 16),  # Ender Pearl
                                  (379, 64),  # Brewing Stand
                                  (380, 64),  # Cauldron
                                  (395, 64),  # Empty Map
                                  ):
            ItemTypes.findItem(itemid).stacksize = stacksize
        for itemtype in ItemTypes.itemtypes.itervalues():
            if itemtype.maxdamage is not None:
                itemtype.stacksize = 1
        _ItemTypes = ItemTypes

    return _ItemTypes.findItem(item["id"].value,
                               item["Damage"].value)


def item_name(item, itemtype=None):
    itemtype = itemtype or item_type(item)
    if 'tag' in item and 'display' in item['tag']:
        return "%s [%s]" % (item['tag']['display']['Name'].value,
                            itemtype.name)
    else:
        return itemtype.name


def get_itemkey(item):
    return (item["id"].value,
            item["Damage"].value)


def get_chunks(world, x=None, z=None, radius=250):
    from pymclevel import box

    if x is None and z is None:
        return world.chunkCount, world.allChunks

    ox = world.bounds.minx if x is None else x - radius
    oz = world.bounds.minz if z is None else z - radius

    bounds = box.BoundingBox((ox, 0, oz),
                             (2 * radius, world.Height,
                              2 * radius))

    return bounds.chunkCount, bounds.chunkPositions


def iter_chunks(world, x=None, z=None, radius=250, progress=True):

    chunk_max, chunk_range = get_chunks(world, x, z, radius)

    if chunk_max <= 0:
        log.warn("No chunks found in range %d of (%d, %d)",
                 radius, x, z)
        return

    if progress:
        pbar = progressbar.ProgressBar(widgets=[' ', progressbar.Percentage(),
                                                ' Chunk ',
                                                     progressbar.SimpleProgress(),
                                                ' ', progressbar.Bar('.'),
                                                ' ', progressbar.ETA(), ' '],
                                       maxval=chunk_max).start()
    start = time.clock()
    chunk_count = 0

    for cx, cz in chunk_range:
        if not world.containsChunk(cx, cz):
            continue

        chunk = world.getChunk(cx, cz)
        chunk_count += 1

        yield chunk

        if progress:
            pbar.update(pbar.currval+1)

    if progress:
        pbar.finish()

    log.info("Data from %d chunks%s extracted in %.2f seconds",
             chunk_count,
             (" (out of %d requested)" %  chunk_max)
                if chunk_max > chunk_count else "",
             time.clock()-start)


def stack_item(item, stacks):
    '''Append an item to a list, trying to stack with other items
        respecting item's max stack size
        Raises ValueError if item count >= max stack size
    '''
    key = get_itemkey(item)
    size = item_type(item).stacksize
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

    except (PyMCLevelError, LookupError, IOError) as e:
        log.error(e)
        return

    items = []
    log.info("Reading '%s' chunk data from '%s'",
             world.LevelName, world.filename)
    log.debug("(%5s, %5s, %3s)\t%4s\t%3s %s", "X", "Z", "Y", "Age", "Qtd", "Item")

    for chunk in iter_chunks(world, args.xpos, args.zpos, args.radius,
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
                   item_name(entity["Item"]),
                ))

                # group with the list
                stack_item(entity["Item"], items)

                # Destroy the item
                entity["Age"].value = 6000
                entity["Health"].value = 0
                dirtychunk = True

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

                        log.debug("%s%s", 37 * ' ', item_name(equip))
                        stack_item(equip, items)

                        # Remove the equipment
                        entity["Equipment"][i] = nbt.TAG_Compound()
                        dirtychunk = True

        if dirtychunk:
            chunk.chunkChanged(calcLighting=False)

    for _ in sorted(items, key=get_itemkey):
        pass

    #world.saveInPlace()


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
