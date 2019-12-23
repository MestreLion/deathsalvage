#!/usr/bin/env python3
#
#    Copyright (C) 2018 Rodrigo Silva (MestreLion) <linux@rodrigosilva.com>
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

"""
Add an Item to the player's inventory
"""

import sys
import os.path as osp
import logging

import mcworldlib as mc2


log = logging.getLogger(__name__)


def parseargs(args=None):
    parser = mc2.basic_parser(description=__doc__)

    parser.add_argument('--item', '-i', dest='id', metavar='ID',
                        default='diamond',
                        help="Item ID to add. [Default: %(default)s]")

    parser.add_argument('--count', '-c', metavar='QTY', default=1, type=int,
                        help="Item quantity to add. [Default: %(default)s]")

    return parser.parse_args(args)


def main(argv=None):
    args = parseargs(argv)
    logging.basicConfig(level=args.loglevel, format='%(levelname)s: %(message)s')
    log.debug(args)

    world = mc2.load(args.world)
    inventory = world.get_player(args.player).inventory
    log.debug("Current Inventory: %s", inventory)

    try:
        item = mc.ItemTypes.findItem(args.id).to_item(args.count)
    except mc2.MCError:
        log.error("Item Type not found: %s", args.id)
        return 1

    # Stack the item to inventory
    remaining, slots = inventory.stack_item(item)

    for slot, count in slots:
        log.info("Added to inventory [slot %3d]: %2d %s",
                 slot, count, item.fullname)  # Do NOT use item.description!

    if remaining:
        log.warn("No suitable free inventory slot to add: %2d %s",
                 remaining, item.fullname)

    log.debug("Inventory afterwards: %s", inventory)
    mc2.save_world(world, args.save)



if __name__ == '__main__':
    log = logging.getLogger(osp.basename(osp.splitext(__file__)[0]))
    try:
        sys.exit(main())
    except mc2.MCError as e:
        log.error(e)
        sys.exit(1)
    except Exception as e:
        log.critical(e, exc_info=True)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
