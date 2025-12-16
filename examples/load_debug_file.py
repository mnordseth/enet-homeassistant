"Load a debug file downloaded from Home Assistant"

import argparse
import asyncio
import logging
from os.path import abspath, dirname
from sys import path
import json

path.insert(1, dirname(dirname(abspath(__file__)))+"/custom_components/")

from enet import aioenet

parser = argparse.ArgumentParser(description="Load Home Assistant debug file to view enet devices and channels")
parser.add_argument("filename", help="/path/to/file to load")
parser.add_argument("--debug", help="enable debug logging", action="store_true")
args = parser.parse_args()

async def main():
    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)-15s %(levelname)-5s %(name)s -- %(message)s",
        )

    enet = aioenet.EnetClient("http://localhost", "", "", True, args.filename)

    async with aioenet.EnetClient("http://localhost", "", "", True, args.filename) as enet:
        for device in enet.devices:
            print(device)
            print("\n".join(["  " + str(c) for c in device.channels]))




try:
    e = asyncio.run(main())
except KeyboardInterrupt:
    pass

