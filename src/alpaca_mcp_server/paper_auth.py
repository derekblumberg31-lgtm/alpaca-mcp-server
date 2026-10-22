"""Authenticated entry point for Derek's Alpaca paper service.

Only a public verification key is shipped with the service. The private signing
key stays outside this repository and is never sent to Render.
"""

import base64
import os

from fastmcp.server.auth.providers.jwt import JWTVerifier

from .server import build_server

PUBLIC_KEY_B64 = "LS0tLS1CRUdJTiBQVUJMSUMgS0VZLS0tLS0KTUlJQm9qQU5CZ2txaGtpRzl3MEJBUUVGQUFPQ0FZOEFNSUlCaWdLQ0FZRUFzRWRTaU1YdUs5QmRXL04vVU1WcwpteDFPVkNLNjRYRU85QkJhYlBrSEUvOEFpaXZZcFpQSm5oTk4xeEpnZFFyYzNOYWhNVDNzd0tsQ1pSUnZtdzhZClZCUHNuRDBwR2hKTzFVenNnby9ZSStYZVNIaDZaa0pvN1JzTTNKUEFSWGw4UHB5L3BmQnB3djNnWXBHWm5FRnQKWEYxM0prOTFsamlDQ0liMlcrYS9sYzZ4TUZBZmZjRjFtRTdyQzB4cmsrNExBbGVCUk1aeVhkZkUzR3FpMTUregpBOEt5cjJTQVRoNUpLZ0FPZnJBbWxEMS9YN3hyZHVnbGZXei8xZCs1a0YrNXpCdnJQMjNJTC9QTnhXcE5VclhQCnFFMUIzRGpvSWkySWtBbTlFNUd2dWtNK0JaNmRydnVwb2FRd2d2Q050aEg3Z0RNNEk4UHByc1ZuSDNCWm5QeHgKYjcra2g1bHM1Qm1xTFBaSXAvZTg1MjJTb2d5Qy9ESG5UeUJUcEM5cFA2ZEVuMEh4ZFVHTWo2aXh3ZTNNRGNrbQpORmNFUjVjMllrY2F1S2lqOHpUSGEvYkpyU2JtbFJkaE9BV2xLOTRSTENsTmpjWnJ3N0ZTNFd2SzUyUGxWblBtCnhFVEpROUNueStDa3h0b3VHNDVRZzBySTQySGdtSjNoaWJNYTAxVjNWVjdaQWdNQkFBRT0KLS0tLS1FTkQgUFVCTElDIEtFWS0tLS0tCg=="
AUDIENCE = "https://alpaca-mcp-paper-oj7u.onrender.com/mcp"


def main() -> None:
    if os.environ.get("ALPACA_PAPER_TRADE", "true").lower() not in {"true", "1", "yes"}:
        raise ValueError("this authenticated service is paper-trading only")
    server = build_server()
    server.auth = JWTVerifier(
        public_key=base64.b64decode(PUBLIC_KEY_B64),
        issuer="alpaca-paper-owner",
        audience=AUDIENCE,
        algorithm="RS256",
        required_scopes=["mcp:access"],
    )
    server.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
        host_origin_protection=True,
        allowed_hosts=["alpaca-mcp-paper-oj7u.onrender.com"],
    )


if __name__ == "__main__":
    main()
