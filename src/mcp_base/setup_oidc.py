#!/usr/bin/env python3
"""
MCP Server - OIDC Provider Setup

Sets up an OIDC provider for MCP authentication.

Currently supported providers:
- auth0: Auth0 (https://auth0.com)

Usage:
    mcp-base setup-oidc --provider auth0 --domain your-tenant.auth0.com
    mcp-base setup-oidc --provider auth0 --token YOUR_TOKEN
"""

import sys


SUPPORTED_PROVIDERS = ["auth0"]


def main():
    # Check for --provider/-p argument manually to avoid consuming --help
    provider = "auth0"  # default
    remaining = []

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--provider", "-p"):
            if i + 1 < len(args):
                provider = args[i + 1]
                i += 2
                continue
        elif arg.startswith("--provider="):
            provider = arg.split("=", 1)[1]
            i += 1
            continue
        remaining.append(arg)
        i += 1

    if provider not in SUPPORTED_PROVIDERS:
        print(f"Error: Unknown provider '{provider}'")
        print(f"Supported providers: {', '.join(SUPPORTED_PROVIDERS)}")
        sys.exit(1)

    # Restore args for provider-specific module
    sys.argv = ["mcp-base setup-oidc"] + remaining

    if provider == "auth0":
        from mcp_base.setup_auth0 import main as auth0_main
        auth0_main()


if __name__ == "__main__":
    main()
