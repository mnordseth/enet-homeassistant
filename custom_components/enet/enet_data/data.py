'''Handle the Enet Smart Home data.'''
import json
import os
import asyncio
from typing import Dict, Any, List
import logging

from .utils import getitem_from_dict

_LOGGER = logging.getLogger(__name__)

class EnetData:
    @property
    def channel_types(self) -> Dict[str, Dict]:
        """Returns the channel types dictionary."""
        return self._channel_types

    @property
    def input_device_function_types(self) -> Dict[str, Dict]:
        """Returns the input device function types dictionary."""
        return self._input_device_function_types

    @property
    def output_device_function_types(self) -> Dict[str, Dict]:
        """Returns the output device function types dictionary."""
        return self._output_device_function_types

    @property
    def device_parameter_types(self) -> Dict[str, Dict]:
        """Returns the device parameter types dictionary."""
        return self._device_parameter_types

    @property
    def device_types(self) -> Dict[str, Dict]:
        """Returns the device types dictionary."""
        return self._device_types

    @property
    def manufacturers(self) -> Dict[str, Dict]:
        """Returns the manufacturers dictionary."""
        return self._manufacturers

    @property
    def value_type_container_types(self) -> Dict[str, Dict]:
        """Returns the value type container types dictionary."""
        return self._value_type_container_types

    @property
    def value_types(self) -> Dict[str, Dict]:
        """Returns the value types dictionary."""
        return self._value_types

    def __init__(self) -> None:
        """Initializes the EnetData instance with empty dictionaries."""
        self._channel_types: Dict[str, Dict] = {}
        self._input_device_function_types: Dict[str, Dict] = {}
        self._output_device_function_types: Dict[str, Dict] = {}
        self._device_parameter_types: Dict[str, Dict] = {}
        self._device_types: Dict[str, Dict] = {}
        self._manufacturers: Dict[str, Dict] = {}
        self._value_type_container_types: Dict[str, Dict] = {}
        self._value_types: Dict[str, Dict] = {}

    async def init_data(self) -> None:
        """Initializes the EnetData instance and loads data from JSON files."""
        _LOGGER.debug("Initializing EnetData...")
        await self.import_channel_types()
        await self.import_device_function_types()
        await self.import_device_parameter_types()
        await self.import_device_types()
        await self.import_manufacturers()
        await self.import_value_type_container_types()
        await self.import_value_types()
        _LOGGER.debug("EnetData initialization complete.")

    def get_channel_type_by_id(self, type_id: str) -> Dict:
        """Returns the channel type dictionary for the given ID."""
        return self._channel_types.get(type_id, {})

    def get_channel_meta_data_from_channel_type(self, type_id: str) -> Dict:
        channel_type = self.get_channel_type_by_id(type_id)
        return getitem_from_dict(channel_type, ["metaData"])

    def get_input_device_function_type_by_id(self, type_id: str) -> Dict:
        """Returns the input device function type dictionary for the given ID."""
        return self._input_device_function_types.get(type_id, {})

    def get_value_template_from_input_device_function(self, type_id: str) -> list:
        function_type = self.get_input_device_function_type_by_id(type_id)
        return self.get_value_template_from_value_container(function_type.get("valueTypeContainerTypeID"))

    def get_input_device_function_name(self, type_id: str) -> str:
        return self.get_input_device_function_type_by_id(type_id).get("name", "Unknown")

    def get_output_device_function_type_by_id(self, type_id: str) -> Dict:
        """Returns the output device function type dictionary for the given ID."""
        return self._output_device_function_types.get(type_id, {})

    def get_output_device_function_name(self, type_id: str) -> str:
        return self.get_output_device_function_type_by_id(type_id).get("name", "Unknown")

    def get_device_parameter_type_by_id(self, type_id: str) -> Dict:
        """Returns the device parameter type dictionary for the given ID."""
        return self._device_parameter_types.get(type_id, {})

    def get_device_parameter_name(self, type_id: str) -> str:
        return self.get_device_parameter_type_by_id(type_id).get("name", "Unknown")

    def get_value_template_from_device_parameter(self, type_id: str) -> list:
        function_type = self.get_device_parameter_type_by_id(type_id)
        return self.get_value_template_from_value_container(function_type.get("valueTypeContainerTypeID"))

    def get_device_type_by_id(self, type_id: str) -> Dict:
        """Returns the device type dictionary for the given ID."""
        return self._device_types.get(type_id, {})

    def get_device_name_from_device_type_id(self, type_id: str) -> str:
        device_type = self.get_device_type_by_id(type_id)
        return getitem_from_dict(device_type, ["metaData", "name"])

    def get_manufacturer_by_id(self, manufacturer_id: str) -> Dict:
        """Returns the manufacturer dictionary for the given ID."""
        return self._manufacturers.get(manufacturer_id, {})

    def get_manufacturer_from_device_type_id(self, type_id: str) -> Dict:
        """Returns the manufacturer dictionary for the given device type ID."""
        device_type = self.get_device_type_by_id(type_id)
        manufacturer_id = getitem_from_dict(device_type, ["metaData", "manufacturerID"])
        return self.get_manufacturer_by_id(manufacturer_id)

    def get_manufacturer_name_from_device_type_id(self, type_id: str) -> str:
        """Returns the manufacturer dictionary for the given device type ID."""
        manufacturer = self.get_manufacturer_from_device_type_id(type_id)
        return manufacturer.get("name")

    def get_value_type_container_type_by_id(self, type_id: str) -> Dict:
        """Returns the value type container type dictionary for the given ID."""
        return self._value_type_container_types.get(type_id, {})

    def get_value_template_from_value_container(self, type_id: str) -> list:
        value_container = self.get_value_type_container_type_by_id(type_id)

        value_template = []

        for value_type_id in value_container.get("valueTypeIDs", []):
            value_template.append(self.get_value_template_from_value_type(value_type_id))

        return value_template

    def get_value_type_by_id(self, type_id: str) -> Dict:
        """Returns the value type dictionary for the given ID."""
        return self._value_types.get(type_id, {})

    def get_value_template_from_value_type(self, type_id: str) -> Dict:
        value_type = self.get_value_type_by_id(type_id)
        return {
            "value": getitem_from_dict(value_type, ["data", "defaultValue"]),
            "valueTypeID": value_type.get("id"),
        }


    async def import_channel_types(self) -> None:
        """Imports channel types from a JSON file and updates the internal dictionary."""
        json_data = await self.import_json_file('channelTypes.json')
        self._channel_types = self.parse_list_to_dict(json_data['channelTypes'], "id")

    async def import_device_function_types(self) -> None:
        """Imports device function types from a JSON file and updates the internal dictionaries."""
        json_data = await self.import_json_file('deviceFunctionTypes.json')
        self._input_device_function_types = self.parse_list_to_dict(json_data['inputDeviceFunctionTypes'], "id")
        self._output_device_function_types = self.parse_list_to_dict(json_data['outputDeviceFunctionTypes'], "id")

    async def import_device_parameter_types(self) -> None:
        """Imports device parameter types from a JSON file and updates the internal dictionary."""
        json_data = await self.import_json_file('deviceParameterTypes.json')
        self._device_parameter_types = self.parse_list_to_dict(json_data['deviceParameterTypes'], "id")

    async def import_device_types(self) -> None:
        """Imports device types from a JSON file and updates the internal dictionary."""
        json_data = await self.import_json_file('deviceTypes.json')
        self._device_types = self.parse_list_to_dict(json_data['deviceTypes'], "id")

    async def import_manufacturers(self) -> None:
        """Imports manufacturers from a JSON file and updates the internal dictionary."""
        json_data = await self.import_json_file('manufacturers.json')
        self._manufacturers = self.parse_list_to_dict(json_data['manufacturers'], "id")

    async def import_value_type_container_types(self) -> None:
        """Imports value type container types from a JSON file and updates the internal dictionary."""
        json_data = await self.import_json_file('valueTypeContainerTypes.json')
        self._value_type_container_types = self.parse_list_to_dict(json_data['valueTypeContainerTypes'], "id")

    async def import_value_types(self) -> None:
        """Imports value types from a JSON file and updates the internal dictionary."""
        json_data = await self.import_json_file('valueTypes.json')
        self._value_types = self.parse_list_to_dict(json_data['valueTypes'], "id")

    async def import_json_file(self, file_name: str) -> Dict[str, Any]:
        """
        Imports a JSON file asynchronously and returns its content as a dictionary.

        Args:
            file_name (str): The name of the JSON file to import.

        Returns:
            Dict[str, Any]: The content of the JSON file as a dictionary.

        Raises:
            FileNotFoundError: If the specified file does not exist.
        """
        file_path = os.path.join(os.path.dirname(__file__), 'json_data', file_name)

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"The file {file_path} does not exist.")

        def read_file() -> Dict[str, Any]:
            try:
                with open(file_path, 'r', encoding="utf-8") as file:
                    return json.load(file)
            except FileNotFoundError as e:
                raise FileNotFoundError(f"Error reading file {file_path}: {e}") from e

        json_data = await asyncio.to_thread(read_file)
        return json_data

    def parse_list_to_dict(self, dictionary_list: List[Dict[str, Any]], key: str) -> Dict[str, Dict]:
        """
        Converts a list of dictionaries to a dictionary of dictionaries using a specified key.

        Args:
            dictionary_list (List[Dict[str, Any]]): The list of dictionaries to convert.
            key (str): The key to use for the new dictionary.

        Returns:
            Dict[str, Dict]: The converted dictionary of dictionaries.
        """
        return {list_item[key]: dict(list_item) for list_item in dictionary_list}

# Module-level variable to hold the EnetConfiguration instance
enet_data = EnetData()
