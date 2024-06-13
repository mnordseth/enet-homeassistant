"""The Enet Smart Home configuration & data."""
import asyncio
import logging

from .data import enet_data

# Initialize the Enet Smart Home data.
asyncio.run(enet_data.init_data())
