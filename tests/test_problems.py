"""
Unit tests for the Zabbix MCP Server problems and events tools
"""

from typing import Any

import pytest
from fastmcp import Client
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from zabbix_mcp.models import ZabbixConfig
from zabbix_mcp.tools.problems import register_problems_tools


class FakeEventApi:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]]):
        self.calls = calls

    async def acknowledge(self, **kwargs) -> dict[str, Any]:
        self.calls.append(("acknowledge", kwargs))
        return {"eventids": kwargs["eventids"]}

    async def get(self, **kwargs) -> Any:
        self.calls.append(("get", kwargs))
        if kwargs.get("countOutput"):
            return "1"
        return [{"eventid": "1"}]


class FakeApi:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]]):
        self.event = FakeEventApi(calls)


@pytest.fixture
def api_calls(monkeypatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeZabbixClient:
        def __init__(self, _config):
            self._api = FakeApi(calls)

        async def __aenter__(self):
            return self._api

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

    monkeypatch.setattr(
        "zabbix_mcp.tools.problems.ZabbixClient",
        FakeZabbixClient,
    )
    return calls


@pytest.fixture
def mcp() -> FastMCP:
    server: FastMCP = FastMCP("test-problems")
    register_problems_tools(
        server,
        ZabbixConfig(
            zabbix_url="https://zabbix.example.com/api_jsonrpc.php",
            token="dummy-token",
        ),
    )
    return server


async def call_tool(mcp: FastMCP, name: str, arguments: dict[str, Any]) -> Any:
    async with Client(mcp) as client:
        return (await client.call_tool(name, arguments)).data


@pytest.mark.asyncio
async def test_event_acknowledge_exposes_suppress_until(mcp):
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    assert "suppress_until" in tools["event_acknowledge"].input_schema["properties"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        # Suppress with no expiry, then an expiry with no suppress bit.
        {"eventids": ["1"], "action": 32},
        {"eventids": ["1"], "action": 36, "message": "hi"},
        {"eventids": ["1"], "action": 2, "suppress_until": 0},
    ],
)
async def test_event_acknowledge_rejects_incomplete_suppression(
    mcp, api_calls, arguments
):
    with pytest.raises(ToolError, match="suppress_until"):
        await call_tool(mcp, "event_acknowledge", arguments)

    assert api_calls == []


@pytest.mark.asyncio
async def test_event_acknowledge_forwards_suppression(mcp, api_calls):
    result = await call_tool(
        mcp,
        "event_acknowledge",
        {
            "eventids": ["411531784"],
            "action": 36,
            "message": "Suppressed for a week",
            "suppress_until": 1785742474,
        },
    )

    assert result == {"eventids": ["411531784"], "success": True}
    assert api_calls == [
        (
            "acknowledge",
            {
                "eventids": ["411531784"],
                "action": 36,
                "message": "Suppressed for a week",
                "suppress_until": 1785742474,
            },
        )
    ]


@pytest.mark.asyncio
async def test_event_acknowledge_forwards_indefinite_suppression(mcp, api_calls):
    await call_tool(
        mcp,
        "event_acknowledge",
        {"eventids": ["1"], "action": 32, "suppress_until": 0},
    )

    _, params = api_calls[0]
    assert params["suppress_until"] == 0


@pytest.mark.asyncio
async def test_event_acknowledge_unsuppress_needs_no_timestamp(mcp, api_calls):
    result = await call_tool(
        mcp, "event_acknowledge", {"eventids": ["1"], "action": 64}
    )

    assert result["success"] is True
    assert api_calls == [("acknowledge", {"eventids": ["1"], "action": 64})]


@pytest.mark.asyncio
@pytest.mark.parametrize("select_suppression_data", [True, False])
async def test_event_get_select_suppression_data(
    mcp, api_calls, select_suppression_data
):
    await call_tool(
        mcp,
        "event_get",
        {"eventids": ["1"], "select_suppression_data": select_suppression_data},
    )

    rows_call = [params for name, params in api_calls if not params.get("countOutput")]
    assert ("selectSuppressionData" in rows_call[0]) is select_suppression_data
