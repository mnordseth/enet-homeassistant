# /// script
# requires-python = ">=3.14"
# dependencies = ["aiohttp"]
# ///
"""Exercise the installed eNet client without importing Home Assistant."""

import asyncio
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock

source = Path(__file__).resolve().parents[1] / "custom_components" / "enet"
package = types.ModuleType("enet_under_test")
package.__path__ = [str(source.resolve())]
sys.modules[package.__name__] = package
enet = importlib.import_module("enet_under_test.aioenet")


class SessionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = enet.EnetClient(
            "http://test.invalid", "test", "test", noconnect=True
        )
        self.client._offline = False

    async def test_success_does_not_login(self):
        self.client._do_request = AsyncMock(return_value={"ok": True})
        self.client.simple_login = AsyncMock()
        self.assertEqual(await self.client.ping(), {"ok": True})
        self.client.simple_login.assert_not_awaited()
        self.client._do_request.assert_awaited_once()

    async def test_expired_session_logs_in_then_retries_same_request(self):
        self.client._do_request = AsyncMock(
            side_effect=[enet.AuthError(), None, None, {"ok": True}]
        )
        self.assertEqual(
            await self.client.request(
                enet.URL.VISUALIZATION, "callInputDeviceFunction", {"value": False}
            ),
            {"ok": True},
        )
        calls = self.client._do_request.await_args_list
        self.assertEqual(
            [call.args[1] for call in calls],
            [
                "callInputDeviceFunction",
                "userLogin",
                "setClientRole",
                "callInputDeviceFunction",
            ],
        )
        self.assertEqual(calls[0], calls[-1])

    async def test_invalid_credentials_do_not_recurse(self):
        self.client._do_request = AsyncMock(side_effect=enet.AuthError())
        with self.assertRaises(enet.AuthError):
            await self.client.ping()
        self.assertEqual(
            [call.args[1] for call in self.client._do_request.await_args_list],
            ["ping", "userLogin"],
        )

    async def test_role_failure_does_not_recurse(self):
        self.client._do_request = AsyncMock(
            side_effect=[enet.AuthError(), None, enet.AuthError()]
        )
        with self.assertRaises(enet.AuthError):
            await self.client.ping()
        self.assertEqual(
            [call.args[1] for call in self.client._do_request.await_args_list],
            ["ping", "userLogin", "setClientRole"],
        )

    async def test_second_auth_failure_stops_after_one_retry(self):
        self.client._do_request = AsyncMock(
            side_effect=[enet.AuthError(), None, None, enet.AuthError()]
        )
        with self.assertRaises(enet.AuthError):
            await self.client.ping()
        self.assertEqual(self.client._do_request.await_count, 4)

    async def test_other_errors_are_not_retried(self):
        self.client._do_request = AsyncMock(
            side_effect=RuntimeError("connection failed")
        )
        self.client.simple_login = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, "connection failed"):
            await self.client.ping()
        self.client.simple_login.assert_not_awaited()
        self.client._do_request.assert_awaited_once()

    async def test_subscriptions_restored_before_retry(self):
        self.client._do_request = AsyncMock(return_value=None)
        await self.client.setup_event_subscription("stone-wall-output")
        await self.client.setup_event_subscription_battery_state()
        self.client._do_request.reset_mock()
        self.client._do_request.side_effect = [
            enet.AuthError(),
            None,
            None,
            None,
            None,
            {"events": []},
        ]
        self.assertEqual(await self.client.get_events(), {"events": []})
        calls = self.client._do_request.await_args_list
        self.assertEqual(
            [call.args[1] for call in calls],
            [
                "requestEvents",
                "userLogin",
                "setClientRole",
                "registerEventDeviceBatteryStateChanged",
                "registerEventOutputDeviceFunctionCalled",
                "requestEvents",
            ],
        )
        self.assertEqual(calls[4].args[2], {"deviceFunctionUID": "stone-wall-output"})

    async def test_concurrent_failures_share_one_login(self):
        both_started = asyncio.Event()
        initial_requests = 0
        login_count = 0

        async def fake_request(url, method, params, *args, **kwargs):
            nonlocal initial_requests, login_count
            if method == "userLogin":
                login_count += 1
                await asyncio.sleep(0)
                return None
            if method == "setClientRole":
                return None
            if method == "ping" and initial_requests < 2:
                initial_requests += 1
                if initial_requests == 2:
                    both_started.set()
                await both_started.wait()
                raise enet.AuthError()
            return {"ok": True}

        self.client._do_request = AsyncMock(side_effect=fake_request)
        results = await asyncio.wait_for(
            asyncio.gather(self.client.ping(), self.client.ping()), timeout=2
        )
        self.assertEqual(results, [{"ok": True}, {"ok": True}])
        self.assertEqual(login_count, 1)

    async def test_json_unauthorized_response_triggers_recovery(self):
        class Response:
            status = 200

            def __init__(self, body):
                self.body = body

            async def json(self):
                return self.body

        self.client._session = types.SimpleNamespace(
            post=AsyncMock(
                side_effect=[
                    Response({"error": {"code": -29998, "message": "Unauthorized"}}),
                    Response({"result": None}),
                    Response({"result": None}),
                    Response({"result": "pong"}),
                ]
            )
        )
        self.assertEqual(await self.client.ping(), "pong")
        self.assertEqual(
            [
                call.kwargs["json"]["method"]
                for call in self.client._session.post.await_args_list
            ],
            ["ping", "userLogin", "setClientRole", "ping"],
        )

    async def test_offline_login_remains_noop(self):
        self.client._offline = True
        self.client._do_request = AsyncMock()
        self.assertIsNone(await self.client.simple_login())
        self.client._do_request.assert_not_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
