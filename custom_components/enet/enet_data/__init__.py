"""The Enet Smart Home configuration & data."""
import asyncio

from .data import enet_data

# Initialize the Enet Smart Home data.
asyncio.run(enet_data.init_data())
