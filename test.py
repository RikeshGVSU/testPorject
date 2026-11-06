import asyncio
from urllib.parse import urlparse, parse_qs

import httpx

from mcp import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata, OAuthToken, OAuthClientInformationFull
from mcp.client.auth import AuthorizationCodeResult

MCP_URL = "https://mcp-oauth.us1.plainid.io/fanniemae/mcp"
REDIRECT_URI = "http://localhost:8765/callback"

class MemoryStorage:
    def __init__(self):
        self.tokens = None
        self.client_info = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info

async def redirect_handler(url: str):
    print("\nOPEN THIS URL IN YOUR LAPTOP BROWSER:\n")
    print(url)
    print("\nComplete the PlainID / FMSSO login.\n")

async def callback_handler() -> AuthorizationCodeResult:
    callback_url = input(
        "After login, copy the full callback URL from your browser and paste it here:\n"
    ).strip()

    parsed = urlparse(callback_url)
    params = parse_qs(parsed.query)

    return AuthorizationCodeResult(
        code=params["code"][0],
        state=params["state"][0],
        iss=params.get("iss", [None])[0],
    )

async def main():
    storage = MemoryStorage()

    metadata = OAuthClientMetadata(
        client_name="PlainID MCP AWS Test Client",
        redirect_uris=[REDIRECT_URI],
        scope="openid profile email",
    )

    auth = OAuthClientProvider(
        server_url=MCP_URL,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )

    timeout = httpx.Timeout(
        30.0,
        read=300.0,
    )

    async with httpx.AsyncClient(
        auth=auth,
        timeout=timeout,
    ) as http_client:

        transport = streamable_http_client(
            MCP_URL,
            http_client=http_client,
        )

        async with Client(transport) as client:
            print("\nConnected to PlainID MCP Server")

            print("\nServer info:")
            print(client.server_info)

            print("\nServer capabilities:")
            print(client.server_capabilities)

            tools = await client.list_tools()

            print("\nAvailable tools:")
            for tool in tools.tools:
                print(f"- {tool.name}")
                if tool.description:
                    print(f"  {tool.description}")

if __name__ == "__main__":
    asyncio.run(main())
