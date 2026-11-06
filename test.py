#!/usr/bin/env python3

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx2

from mcp.client.auth import (
    AuthorizationCodeResult,
    OAuthClientProvider,
    TokenStorage,
)
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)


# ============================================================================
# PlainID configuration
# ============================================================================

MCP_URL = "https://mcp-oauth.us1.plainid.io/fanniemae/mcp"

CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 3030
REDIRECT_URI = "http://localhost:3030/callback"


# ============================================================================
# Token / OAuth client storage
# ============================================================================

class PlainIDTokenStorage(TokenStorage):

    def __init__(self):
        self._tokens: OAuthToken | None = None

        # This client registration was successfully returned
        # by the PlainID /oauth/register endpoint.
        self._client_info = OAuthClientInformationFull.model_validate(
            {
                "client_id": "plainid",
                "client_id_issued_at": 1793985413,
                "redirect_uris": [
                    "http://127.0.0.1:27523/callback",
                    "http://localhost:27523/callback",
                    "http://127.0.0.1:8721/callback",
                    "http://localhost:8721/callback",
                    "https://mcp-oauth.us1.plainid.io/oauth/callback",
                    "http://localhost:3030/callback",
                ],
                "grant_types": [
                    "authorization_code",
                    "refresh_token",
                ],
                "response_types": [
                    "code",
                ],
                "scope": "openid profile email",
                "token_endpoint_auth_method": "none",
            }
        )

    async def get_tokens(self) -> OAuthToken | None:
        return self._tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self._client_info

    async def set_client_info(
        self,
        client_info: OAuthClientInformationFull,
    ) -> None:
        self._client_info = client_info


# ============================================================================
# OAuth callback HTTP server
# ============================================================================

class CallbackServer:

    def __init__(self, port: int = CALLBACK_PORT):
        self.port = port

        self.authorization_code = None
        self.state = None
        self.iss = None
        self.error = None

        self.server = None
        self.thread = None

    def start(self):

        parent = self

        class CallbackHandler(BaseHTTPRequestHandler):

            def do_GET(self):

                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)

                if "code" in params:

                    parent.authorization_code = params["code"][0]
                    parent.state = params.get("state", [None])[0]
                    parent.iss = params.get("iss", [None])[0]

                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()

                    self.wfile.write(
                        b"""
                        <html>
                            <body>
                                <h2>PlainID authentication successful</h2>
                                <p>You can close this browser tab and return to the terminal.</p>
                            </body>
                        </html>
                        """
                    )

                elif "error" in params:

                    parent.error = params["error"][0]

                    self.send_response(400)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()

                    self.wfile.write(
                        f"""
                        <html>
                            <body>
                                <h2>PlainID authentication failed</h2>
                                <p>{parent.error}</p>
                            </body>
                        </html>
                        """.encode()
                    )

                else:

                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format: str, *args: Any):
                # Suppress HTTP server logging
                return

        self.server = HTTPServer(
            (CALLBACK_HOST, self.port),
            CallbackHandler,
        )

        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )

        self.thread.start()

        print(
            "OAuth callback listener running on "
            f"http://localhost:{self.port}/callback"
        )

    def wait_for_callback(self, timeout: int = 300):

        start = time.time()

        while time.time() - start < timeout:

            if self.authorization_code:
                return

            if self.error:
                raise RuntimeError(
                    f"OAuth authorization failed: {self.error}"
                )

            time.sleep(1)

        raise TimeoutError(
            "Timed out waiting for the PlainID OAuth callback."
        )

    def stop(self):

        if self.server:
            self.server.shutdown()
            self.server.server_close()


# ============================================================================
# OAuth browser handlers
# ============================================================================

callback_server = CallbackServer()


async def redirect_handler(authorization_url: str) -> None:

    print()
    print("=" * 80)
    print("PLAINID AUTHORIZATION REQUIRED")
    print("=" * 80)
    print()
    print("Open the following URL in your laptop browser:")
    print()
    print(authorization_url)
    print()
    print("=" * 80)
    print()
    print(
        "After FMSSO / MFA authentication completes, "
        "the browser should redirect to:"
    )
    print()
    print(REDIRECT_URI)
    print()
    print(
        "The SSM port-forwarding session must be running "
        "so localhost:3030 on your laptop reaches this EC2 instance."
    )
    print()


async def callback_handler() -> AuthorizationCodeResult:

    print("Waiting for PlainID OAuth callback...")

    await asyncio.to_thread(
        callback_server.wait_for_callback,
        300,
    )

    print("OAuth callback received.")

    return AuthorizationCodeResult(
        code=callback_server.authorization_code,
        state=callback_server.state,
        iss=callback_server.iss,
    )


# ============================================================================
# MCP client
# ============================================================================

async def main():

    callback_server.start()

    storage = PlainIDTokenStorage()

    client_metadata = OAuthClientMetadata.model_validate(
        {
            "client_name": "PlainID MCP AWS Test Client",
            "redirect_uris": [
                REDIRECT_URI,
            ],
            "grant_types": [
                "authorization_code",
                "refresh_token",
            ],
            "response_types": [
                "code",
            ],
            "scope": "openid profile email",
            "token_endpoint_auth_method": "none",
        }
    )

    oauth_auth = OAuthClientProvider(
        server_url=MCP_URL,
        client_metadata=client_metadata,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )

    try:

        print()
        print("Connecting to PlainID MCP Server...")
        print(MCP_URL)
        print()

        async with httpx2.AsyncClient(
            auth=oauth_auth,
            timeout=httpx2.Timeout(
                30.0,
                read=300.0,
            ),
        ) as http_client:

            async with streamable_http_client(
                url=MCP_URL,
                http_client=http_client,
            ) as (
                read_stream,
                write_stream,
            ):

                async with ClientSession(
                    read_stream,
                    write_stream,
                ) as session:

                    print("Initializing MCP session...")

                    result = await session.initialize()

                    print()
                    print("=" * 80)
                    print("CONNECTED TO PLAINID MCP SERVER")
                    print("=" * 80)

                    print()
                    print("Server information:")
                    print(result.serverInfo)

                    print()
                    print("Discovering available MCP tools...")

                    tools_result = await session.list_tools()

                    print()
                    print("=" * 80)
                    print("AVAILABLE PLAINID MCP TOOLS")
                    print("=" * 80)
                    print()

                    if not tools_result.tools:

                        print("No MCP tools were returned.")

                    else:

                        for number, tool in enumerate(
                            tools_result.tools,
                            start=1,
                        ):

                            print(
                                f"{number}. {tool.name}"
                            )

                            if tool.description:
                                print(
                                    f"   {tool.description}"
                                )

                            print()

                    print("=" * 80)
                    print(
                        f"Total tools discovered: "
                        f"{len(tools_result.tools)}"
                    )
                    print("=" * 80)

    except Exception as exc:

        print()
        print("=" * 80)
        print("ERROR")
        print("=" * 80)
        print()
        print(type(exc).__name__)
        print(str(exc))
        print()

        raise

    finally:

        callback_server.stop()


# ============================================================================
# Entry point
# ============================================================================

if __name__ == "__main__":
    asyncio.run(main())
