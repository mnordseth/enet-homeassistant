"""Repairs: the notice about the scenes left by the removed scenes.yaml sync"""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Only a notice: confirming it dismisses it, nothing is changed"""
    return ConfirmRepairFlow()
