'''Utility functions for the Enet Smart Home configuration settings.'''
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
