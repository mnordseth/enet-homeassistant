"""Example script for using aioenet connecting to a Enet Smart Home Server."""
import argparse
import asyncio
import logging
from os.path import abspath, dirname
from sys import path

path.insert(1, dirname(dirname(abspath(__file__)))+"/custom_components/enet/")

#from custom_components.enet import aioenet
import aioenet

parser = argparse.ArgumentParser(description="AIO Enet Smart Home command line example")
parser.add_argument("host", help="hostname of Enet Smart Home Server")
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
        print("Connected")
        for device in enet.devices:
            print(device)
            print("\n".join(["  " + str(c) for c in device.channels]))

        def print_event(*args):
            print("Got event: ", *args)

        enet.subscribe(print_event)

        await asyncio.sleep(600)
            
try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
