"""Load a debug file downloaded from Home Assistant"""

import argparse
import asyncio
import json
import logging
from os.path import abspath, dirname
from sys import path

path.insert(1, dirname(dirname(abspath(__file__))) + "/custom_components/")

from enet import aioenet

parser = argparse.ArgumentParser(
    description="Load Home Assistant debug file to view enet devices and channels"
)
parser.add_argument("filename", help="/path/to/file to load")
parser.add_argument("--debug", help="enable debug logging", action="store_true")
args = parser.parse_args()


async def main():
    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)-15s %(levelname)-5s %(name)s -- %(message)s",
        )
    with open(args.filename) as fp:
        config = json.load(fp)
    enet_devices_raw = config["data"]["enet_data"]
    enet = aioenet.EnetClient("http://localhost", "", "")
    print(f"Found {len(enet_devices_raw)} raw devices")
    devices = []
    for e in enet_devices_raw:
        device = aioenet.create_device(enet, e)
        devices.append(device)

    for d in devices:
        print(d)
        for c in d.channels:
            print("  " + str(c))


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
