"""Utility functions for the Enet Smart Home configuration settings."""

from functools import reduce
from operator import getitem


def getitem_from_dict(data_dict, map_list):
    """Iterate nested dictionary"""
    try:
        return reduce(getitem, map_list, data_dict)
    except TypeError:
        return None
    except KeyError:
        return None


def filter_device_parameter_type_ids(data):
    """Filter device parameter type IDs from device channels."""
    filtered_ids = {}
    for channel in data:
        channel_id = channel.get("id")
        parameter_ids = channel.get("deviceParameterTypeIDs", [])
        filtered_ids[channel_id] = [
            pid
            for pid in parameter_ids
            if not any(sub in pid for sub in [".SCV1", ".SCV2", ".SNA"])
        ]
    return filtered_ids
