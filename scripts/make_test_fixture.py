#!/usr/bin/env python3
"""Build the anonymized device fixture for tests/test_home_assistant.py.

The input is one or more diagnostics files, downloaded in Home Assistant from
the Enet integration (Download diagnostics). From these, one device of every
device type is taken, plus the devices given with --include, for features
only some devices of a type have.

The devices are anonymized: device and channel names are replaced by neutral
names (Name 1, Name 2, ...), where equal names stay equal, and serial numbers
are replaced. Only the device list is written, not the config entry. The file
is not written if an original name or serial number is still found in it.

The current fixture was built from two example servers with:

    scripts/make_test_fixture.py diagnostics-1.json diagnostics-2.json \\
        --include 1fb68d33-bc85-43aa-b459-3451045004ce  # the only blind with tilt

To add the devices of a user's diagnostics file to the fixture, without
changing the devices already in it:

    scripts/make_test_fixture.py --append diagnostics-from-user.json

Afterwards, update the expected entities and check the changes:

    UPDATE_EXPECTED=1 python tests/test_home_assistant.py
    git diff tests/fixtures/expected_entities.json
"""

import argparse
import json
from pathlib import Path
import re
import sys

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "enet_devices.json"
NAME_KEYS = ("installationArea", "effectArea")


class Anonymizer:
    """Replace names and serial numbers, numbering on from the existing fixture."""

    def __init__(self, names_used=0, serials_used=0):
        self.names = {}
        self.names_used = names_used
        self.serials_used = serials_used
        self.originals = set()

    def name(self, name):
        # The same name always maps to the same neutral name, so equal names stay equal
        if name not in self.names:
            self.names_used += 1
            self.names[name] = f"Name {self.names_used}"
        self.originals.add(name)
        return self.names[name]

    def serial(self, serial):
        self.originals.add(serial)
        serial = f"0000000{self.serials_used:05x}"
        self.serials_used += 1
        return serial

    def device(self, raw):
        device = self._names(raw)
        # An empty serial number marks a catalog device, it is kept empty
        if device["metaData"].get("serialNumber"):
            device["metaData"]["serialNumber"] = self.serial(
                device["metaData"]["serialNumber"]
            )
        return device

    def _names(self, value):
        if isinstance(value, dict):
            return {
                key: self.name(item)
                if key in NAME_KEYS and isinstance(item, str)
                else self._names(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._names(item) for item in value]
        return value


def read_devices(path):
    """Return the device list of a diagnostics file."""
    data = json.loads(Path(path).read_text())
    try:
        return data["data"]["enet_data"]
    except (KeyError, TypeError):
        sys.exit(f"{path}: not a diagnostics file of the Enet integration")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("diagnostics", nargs="+", help="diagnostics files")
    parser.add_argument(
        "--append",
        action="store_true",
        help="keep the devices of the fixture and add devices of new types",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="UID",
        help="also add the device with this uid, even if its type is in the fixture",
    )
    parser.add_argument("--output", type=Path, default=FIXTURE, help="fixture file")
    args = parser.parse_args()

    devices = []
    if args.append:
        devices = json.loads(args.output.read_text())["data"]["enet_data"]
    known_uids = {device["uid"] for device in devices}
    known_types = {device["typeID"] for device in devices}
    names_used = max(
        (int(n) for n in re.findall(r'"Name (\d+)"', json.dumps(devices))), default=0
    )
    anonymizer = Anonymizer(names_used, serials_used=len(devices))
    include = set(args.include)

    added = []
    for path in args.diagnostics:
        for raw in read_devices(path):
            if raw["uid"] in known_uids:
                continue
            if raw["typeID"] in known_types and raw["uid"] not in include:
                continue
            known_uids.add(raw["uid"])
            known_types.add(raw["typeID"])
            devices.append(anonymizer.device(raw))
            added.append(raw["typeID"])

    missing = include - known_uids
    if missing:
        sys.exit(f"Devices given with --include not found: {', '.join(sorted(missing))}")

    output = json.dumps(
        {"data": {"enet_data": devices}}, separators=(",", ":"), ensure_ascii=False
    )
    leaks = sorted(
        value
        for value in anonymizer.originals
        if json.dumps(value, ensure_ascii=False) in output
    )
    if leaks:
        sys.exit(
            "Not written, original names or serial numbers are still in the "
            f"fixture: {leaks}"
        )

    args.output.write_text(output)
    print(f"{args.output}: {len(devices)} devices, added {len(added)}: {sorted(added)}")
    if added:
        print("Update the expected entities: UPDATE_EXPECTED=1 python tests/test_home_assistant.py")


if __name__ == "__main__":
    main()
