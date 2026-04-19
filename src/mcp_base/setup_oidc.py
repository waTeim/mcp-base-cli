#!/usr/bin/env python3
"""
MCP Server - OIDC Provider Setup

Sets up an OIDC provider for MCP authentication.

Currently supported providers:
- auth0: Auth0 (https://auth0.com) - full automated setup
- dex: Dex (https://dexidp.io) - pre-configured client credentials
- keycloak: Keycloak - pre-configured client credentials
- okta: Okta - pre-configured client credentials
- generic: Any pre-configured OIDC provider

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
import argparse


SUPPORTED_PROVIDERS = ["auth0", "dex", "generic", "keycloak", "okta"]


def show_help():
    """Show comprehensive help for all providers."""
    help_text = """
usage: mcp-base setup-oidc --provider <provider> [provider-specific-options]

Setup OIDC provider for MCP authentication

PROVIDER OPTION (required):
  --provider, -p {auth0,dex,keycloak,okta,generic}
                        Choose OIDC provider type

SUPPORTED PROVIDERS:

  auth0                 Auth0 - Automated tenant configuration
                        • Automatically creates/updates OIDC applications
                        • Configures Resource Server (API)
                        • Sets up Dynamic Client Registration (DCR)
                        • Manages client grants and permissions

                        Usage:
                          mcp-base setup-oidc --provider auth0 \\
                            --domain your-tenant.auth0.com \\
                            --api-identifier https://mcp-server.example.com/mcp \\
                            --token YOUR_MGMT_TOKEN

                        For Auth0-specific options, run:
                          mcp-base setup-oidc --provider auth0 --help

  dex                   Dex - Pre-configured client credentials
                        • Uses existing Dex client configuration
                        • Validates OIDC discovery endpoints
                        • Displays required redirect URLs

                        Usage:
                          mcp-base setup-oidc --provider dex \\
                            --issuer https://dex.example.com \\
                            --audience https://mcp-server.example.com/mcp \\
                            --client-id YOUR_CLIENT_ID \\
                            --client-secret YOUR_CLIENT_SECRET

                        For Dex-specific options, run:
                          mcp-base setup-oidc --provider dex --help

  keycloak              Keycloak - Pre-configured client credentials
                        Same as 'dex' but optimized for Keycloak naming

                        For Keycloak-specific options, run:
                          mcp-base setup-oidc --provider keycloak --help

  okta                  Okta - Pre-configured client credentials
                        Same as 'dex' but optimized for Okta naming

                        For Okta-specific options, run:
                          mcp-base setup-oidc --provider okta --help

  generic               Generic OIDC - Any standard OIDC provider
                        Works with any OIDC-compliant identity provider

                        For generic OIDC options, run:
                          mcp-base setup-oidc --provider generic --help

REQUIRED REDIRECT URLs:
  All providers require these redirect URLs to be configured in your IdP:

  • MCP Server: https://mcp-server.example.com/auth/callback
    (Replace with your actual MCP server URL)

  • Claude Desktop: https://claude.ai/api/mcp/auth_callback

  • Local Testing (optional):
    - http://localhost:8888/callback
    - http://localhost:8889/callback

EXAMPLES:

  # Get help for specific provider
  mcp-base setup-oidc --provider auth0 --help
  mcp-base setup-oidc --provider dex --help

  # Setup Auth0 (automated)
  mcp-base setup-oidc --provider auth0 \\
    --domain your-tenant.auth0.com \\
    --api-identifier https://mcp-server.example.com/mcp \\
    --token YOUR_MGMT_TOKEN

  # Setup Dex (pre-configured client)
  mcp-base setup-oidc --provider dex \\
    --issuer https://dex.example.com \\
    --audience https://mcp-server.example.com/mcp \\
    --client-id my-client-id \\
    --client-secret my-client-secret

  # Setup Keycloak (pre-configured client)
  mcp-base setup-oidc --provider keycloak \\
    --issuer https://keycloak.example.com/realms/myrealm \\
    --audience https://mcp-server.example.com/mcp \\
    --client-id my-client-id \\
    --client-secret my-client-secret

NEXT STEPS:
  After setup, create Kubernetes secrets:
    mcp-base create-secrets --namespace <namespace> --release-name <release-name>

For more information, see the README.md or visit:
  https://github.com/your-org/mcp-base
"""
    print(help_text)


def main():
    # Check if help is requested without a provider
    args = sys.argv[1:]

    # If --help or -h is present and no --provider, show comprehensive help
    if ("--help" in args or "-h" in args) and not any(a.startswith("--provider") or a == "-p" for a in args):
        show_help()
        sys.exit(0)

    # Parse --provider argument manually
    provider = None
    remaining = []

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

    # If no provider specified, show comprehensive help
    if provider is None:
        if "--help" in remaining or "-h" in remaining:
            show_help()
        else:
            print("Error: --provider option is required")
            print()
            print("Supported providers: " + ", ".join(SUPPORTED_PROVIDERS))
            print()
            print("Usage: mcp-base setup-oidc --provider <provider> [options]")
            print("Run 'mcp-base setup-oidc --help' for detailed information")
        sys.exit(1)

    # Validate provider
    if provider not in SUPPORTED_PROVIDERS:
        print(f"Error: Unknown provider '{provider}'")
        print(f"Supported providers: {', '.join(SUPPORTED_PROVIDERS)}")
        print()
        print("Run 'mcp-base setup-oidc --help' for detailed information")
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
