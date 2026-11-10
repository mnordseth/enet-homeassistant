"""Constants of the Enet Smart Home server API"""

from enum import StrEnum


class URL(StrEnum):
    """URLs for communicating with Enet server"""

    MANAGEMENT = "/jsonrpc/management"
    VISUALIZATION = "/jsonrpc/visualization"
    COMMISSIONING = "/jsonrpc/commissioning"
    TELEGRAM = "/jsonrpc/commissioning/app_telegrams"
    SCENE = "/jsonrpc/visualization/app_scene"
