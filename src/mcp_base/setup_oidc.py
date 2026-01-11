#!/usr/bin/env python3
"""
MCP Server - OIDC Provider Setup

Sets up an OIDC provider for MCP authentication.

Currently supported providers:
- auth0: Auth0 (https://auth0.com) - full automated setup
- dex: Dex (https://dexidp.io) - pre-configured client credentials
- generic: Any pre-configured OIDC provider (Keycloak, Okta, etc.)

Usage:
    # Auth0 (automated setup)
    mcp-base setup-oidc --provider auth0 --domain your-tenant.auth0.com

    # Dex or other pre-configured provider
    mcp-base setup-oidc --provider dex --issuer https://dex.example.com \\
        --audience https://mcp-server.example.com/mcp \\
        --client-id YOUR_CLIENT_ID --client-secret YOUR_CLIENT_SECRET

    # Generic OIDC provider
    mcp-base setup-oidc --provider generic --issuer https://your-idp.com \\
        --audience https://mcp-server.example.com/mcp \\
        --client-id YOUR_CLIENT_ID --client-secret YOUR_CLIENT_SECRET
"""

import sys


SUPPORTED_PROVIDERS = ["auth0", "dex", "generic", "keycloak", "okta"]


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

    elif provider in ("dex", "generic", "keycloak", "okta"):
        from mcp_base.setup_generic import main as generic_main
        # Set provider name for generic module
        if "--provider-name" not in sys.argv:
            sys.argv.extend(["--provider-name", provider])
        generic_main()


if __name__ == "__main__":
    main()
