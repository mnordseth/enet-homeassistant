"""Example script for using aioenet connecting to a Enet Smart Home Server."""
import argparse
import asyncio
import logging
from os.path import abspath, dirname
from sys import path

path.insert(1, dirname(dirname(abspath(__file__)))+"/custom_components/")

from enet import aioenet

parser = argparse.ArgumentParser(description="AIO Enet Smart Home command line example")
parser.add_argument("host", help="URI of Enet Smart Home Server, ie http://enet-server")
parser.add_argument("username", help="Username")
parser.add_argument("password", help="Password")
parser.add_argument("--debug", help="enable debug logging", action="store_true")
args = parser.parse_args()

async def main():
    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)-15s %(levelname)-5s %(name)s -- %(message)s",
        )

    async with aioenet.EnetClient(args.host, args.username, args.password) as enet:
        print(f"Connected to {args.host}")
        for device in enet.devices:
            print(device)
            print("\n".join(["  " + str(c) for c in device.channels]))

        def print_event(*args):
            print("Got event: ", *args)

        enet.subscribe(print_event)
        print("\n\nWaiting for events...")
        await asyncio.sleep(600)
        return enet

try:
    e = asyncio.run(main())
except KeyboardInterrupt:
    pass
