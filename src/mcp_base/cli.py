#!/usr/bin/env python3
"""
MCP Base - Unified CLI

Usage:
    mcp-base <command> [options]

Commands:
    add-user        Add users to allowed clients
    create-secrets  Create Kubernetes secrets for MCP deployment
    make-config     Generate configuration files
    setup-oidc      Set up OIDC provider (Auth0, etc.)
    setup-rbac      Set up Kubernetes RBAC resources
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="mcp-base",
        description="CLI tools for MCP server setup and management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  add-user        Add users to allowed clients in your OIDC provider
  create-secrets  Create Kubernetes secrets for MCP deployment
  make-config     Generate configuration files (auth0-config.json, helm-values.yaml, .env)
  setup-oidc      Set up OIDC provider for MCP authentication
  setup-rbac      Set up Kubernetes RBAC resources

Examples:
  mcp-base setup-oidc --provider auth0 --domain your-tenant.auth0.com
  mcp-base create-secrets --namespace default --release-name my-server
  mcp-base add-user --email user@example.com
        """
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # Add subcommand parsers (but delegate actual argument parsing to the modules)
    subparsers.add_parser(
        "add-user",
        help="Add users to allowed clients",
        add_help=False
    )

    subparsers.add_parser(
        "create-secrets",
        help="Create Kubernetes secrets for MCP deployment",
        add_help=False
    )

    subparsers.add_parser(
        "make-config",
        help="Generate configuration files",
        add_help=False
    )

    subparsers.add_parser(
        "setup-oidc",
        help="Set up OIDC provider (e.g., Auth0)",
        add_help=False
    )

    subparsers.add_parser(
        "setup-rbac",
        help="Set up Kubernetes RBAC resources",
        add_help=False
    )

    # Parse only the command, leave rest for submodules
    args, remaining = parser.parse_known_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Restore remaining args for submodule parsing
    sys.argv = [f"mcp-base {args.command}"] + remaining

    if args.command == "add-user":
        from mcp_base.add_user import main as cmd_main
        cmd_main()

    elif args.command == "create-secrets":
        from mcp_base.create_secrets import main as cmd_main
        cmd_main()

    elif args.command == "make-config":
        from mcp_base.make_config import main as cmd_main
        cmd_main()

    elif args.command == "setup-oidc":
        from mcp_base.setup_oidc import main as cmd_main
        cmd_main()

    elif args.command == "setup-rbac":
        from mcp_base.setup_rbac import main as cmd_main
        cmd_main()

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
