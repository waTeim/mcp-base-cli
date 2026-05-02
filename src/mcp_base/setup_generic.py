#!/usr/bin/env python3
"""
Generic OIDC Provider Setup for MCP

For pre-configured OIDC providers (Dex, Keycloak, Okta, etc.) where you already have
client credentials and just need to save the configuration.

Requirements:
    pip install requests (for OIDC discovery validation)

Usage:
    # Interactive mode
    mcp-base setup-oidc --provider generic

    # Non-interactive
    mcp-base setup-oidc --provider generic \
        --issuer https://dex.example.com \
        --audience https://mcp-server.example.com/mcp \
        --client-id YOUR_CLIENT_ID \
        --client-secret YOUR_CLIENT_SECRET

    # Dex shortcut (alias for generic)
    mcp-base setup-oidc --provider dex \
        --issuer https://dex.example.com \
        --audience https://mcp-server.example.com/mcp \
        --client-id YOUR_CLIENT_ID \
        --client-secret YOUR_CLIENT_SECRET
"""

import os
import sys
import json
import argparse
import requests
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

from mcp_base.project_config import (
    ProjectDefaults,
    load_project_defaults,
    resolve,
)
from mcp_base.drift import reconcile_from_command


DEFAULT_CONFIG_FILE = "oidc-config.json"


def pattern_for_provider(provider_name: str) -> str:
    """Return the DCR pattern a provider uses.

    "remote" means FastMCP talks to the IdP's native DCR (Keycloak ≥ 26.6.0);
    no local client secret, no Redis, no JWT signing key are needed.
    "proxy" means FastMCP runs its own OAuth Proxy in front of the IdP and
    requires a pre-registered confidential client, Redis, and a JWT signing key.
    Defined in imp/cli-integration-contract.md §1.
    """
    return "remote" if provider_name == "keycloak" else "proxy"


def fallback_endpoints(issuer: str, provider_name: str) -> Dict[str, str]:
    """Provider-specific endpoint paths used when OIDC discovery is unavailable.

    Only called when --skip-validation is set or discovery fails; otherwise the
    discovery document is the source of truth.
    """
    issuer = issuer.rstrip('/')
    if provider_name == "keycloak":
        return {
            "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
            "token_endpoint": f"{issuer}/protocol/openid-connect/token",
            "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
        }
    if provider_name == "okta":
        return {
            "authorization_endpoint": f"{issuer}/v1/authorize",
            "token_endpoint": f"{issuer}/v1/token",
            "jwks_uri": f"{issuer}/v1/keys",
        }
    # Dex / generic default
    return {
        "authorization_endpoint": f"{issuer}/auth",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
    }


class GenericOIDCSetup:
    """Setup for pre-configured OIDC providers."""

    def __init__(self, config_file: str = DEFAULT_CONFIG_FILE):
        self.config_file = config_file
        self.config = self.load_config()

    def load_config(self) -> Dict[str, Any]:
        """Load existing configuration."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                print(f"📄 Loaded configuration from {self.config_file}")
                return config
            except Exception as e:
                print(f"⚠️  Could not load config file: {e}")
                return {}
        return {}

    def save_config(self, config: Dict[str, Any]) -> None:
        """Save configuration to file."""
        try:
            config_dir = os.path.dirname(self.config_file)
            if config_dir:
                os.makedirs(config_dir, exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            print(f"💾 Configuration saved to {self.config_file}")
        except Exception as e:
            print(f"⚠️  Could not save config file: {e}")

    def validate_issuer(self, issuer: str) -> Optional[Dict[str, Any]]:
        """Validate OIDC issuer via discovery and return the discovery document.

        Returns the parsed discovery document on success, or None if validation
        fails (unreachable issuer, malformed JSON, or missing required fields).
        """
        print(f"\n🔍 Validating OIDC issuer: {issuer}")

        issuer = issuer.rstrip('/')
        discovery_url = f"{issuer}/.well-known/openid-configuration"

        try:
            response = requests.get(discovery_url, timeout=10)
            response.raise_for_status()
            discovery: Dict[str, Any] = response.json()

            # Check required OIDC discovery fields
            required_fields = ['issuer', 'authorization_endpoint', 'token_endpoint', 'jwks_uri']
            missing_fields = [field for field in required_fields if field not in discovery]

            if missing_fields:
                print(f"❌ Invalid OIDC discovery document. Missing fields: {', '.join(missing_fields)}")
                return None

            print(f"✅ Valid OIDC provider found")
            print(f"   Authorization endpoint: {discovery.get('authorization_endpoint')}")
            print(f"   Token endpoint: {discovery.get('token_endpoint')}")
            print(f"   JWKS URI: {discovery.get('jwks_uri')}")

            return discovery

        except requests.RequestException as e:
            print(f"⚠️  Warning: Could not validate OIDC issuer: {e}")
            print(f"   Make sure the issuer is correct and accessible")
            return None
        except ValueError as e:
            print(f"❌ Invalid OIDC discovery document (not JSON): {e}")
            return None

    def show_redirect_urls(self, audience: str) -> None:
        """Display the redirect URLs that should be configured in the IdP."""
        print("\n📋 Required Redirect URLs (Callback URLs)")
        print("=" * 70)
        print("Configure these redirect URLs in your OIDC provider:")
        print()

        # Extract base URL from audience
        parsed = urlparse(audience)
        if parsed.scheme and parsed.netloc:
            # Remove /mcp suffix if present to get base URL
            path = parsed.path.rstrip('/mcp').rstrip('/')
            mcp_base_url = f"{parsed.scheme}://{parsed.netloc}{path}"
            mcp_callback = f"{mcp_base_url}/auth/callback"
            print(f"1. MCP Server Callback (production):")
            print(f"   {mcp_callback}")
            print()

        print(f"2. Claude Desktop Callback:")
        print(f"   https://claude.ai/api/mcp/auth_callback")
        print()

        print(f"3. Local Testing Callbacks (optional):")
        print(f"   http://localhost:8888/callback")
        print(f"   http://localhost:8889/callback")
        print(f"   http://127.0.0.1:8888/callback")
        print("=" * 70)

    def setup(
        self,
        issuer: str,
        audience: str,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        provider_name: str = "generic",
        validate: bool = True,
        required_scopes: Optional[List[str]] = None,
        ingress_host: Optional[str] = None,
        ingress_path: Optional[str] = None,
        ingress_tls_enabled: Optional[bool] = None,
        public_url: Optional[str] = None,
    ) -> None:
        """
        Setup generic OIDC configuration.

        Args:
            issuer: OIDC issuer URL (e.g., https://dex.example.com)
            audience: API audience/identifier (e.g., https://mcp-server.example.com/mcp)
            client_id: OAuth client ID (Pattern A only; ignored for Keycloak)
            client_secret: OAuth client secret (Pattern A only; ignored for Keycloak)
            provider_name: Provider name (generic, dex, keycloak, etc.)
            validate: Whether to validate the issuer
            required_scopes: Optional override for oidc.requiredScopes. Used
                verbatim — ``openid`` is NOT auto-injected.
            ingress_host, ingress_path, ingress_tls_enabled, public_url:
                Optional overrides sourced from mcp-project.yaml's
                ``publicEndpoint`` block.
        """
        pattern = pattern_for_provider(provider_name)
        print(f"\n🔧 Setting up {provider_name.upper()} OIDC provider for MCP (pattern: {pattern})")
        print("=" * 70)

        # Clean and validate inputs
        issuer = issuer.rstrip('/')
        audience = audience.rstrip('/')

        # Validate issuer if requested; discovery doc is the source of truth
        # for endpoint URLs when available.
        discovery: Optional[Dict[str, Any]] = None
        if validate:
            discovery = self.validate_issuer(issuer)

        # Show required redirect URLs
        self.show_redirect_urls(audience)

        # Build configuration. Per imp/cli-integration-contract.md §3, Pattern B
        # (Keycloak) omits the server_client block entirely because FastMCP's
        # KeycloakAuthProvider uses Keycloak's native DCR.
        config: Dict[str, Any] = {
            "provider": provider_name,
            "pattern": pattern,
            "issuer": issuer,
            "audience": audience,
        }

        if pattern == "proxy":
            config["server_client"] = {
                "client_id": client_id or "",
                "client_secret": client_secret or "",
            }
        elif client_id or client_secret:
            print(
                "\n⚠️  --client-id / --client-secret are ignored for Keycloak "
                "(Pattern B uses native DCR). Not written to oidc-config.json."
            )

        if discovery:
            config["authorization_endpoint"] = discovery["authorization_endpoint"]
            config["token_endpoint"] = discovery["token_endpoint"]
            config["jwks_uri"] = discovery["jwks_uri"]
        else:
            config.update(fallback_endpoints(issuer, provider_name))

        # Save configuration
        self.save_config(config)

        # Generate Helm values file
        self.generate_helm_values(
            config,
            audience,
            required_scopes=required_scopes,
            ingress_host=ingress_host,
            ingress_path=ingress_path,
            ingress_tls_enabled=ingress_tls_enabled,
            public_url=public_url,
        )

        print("\n✅ Setup complete!")
        print(f"\nNext steps:")
        print(f"1. Ensure the redirect URLs above are configured in your {provider_name} provider")
        if pattern == "proxy":
            print(f"2. Create Kubernetes secrets:")
            print(f"   mcp-base create-secrets --namespace <namespace> --release-name <release-name>")
            print(f"3. Deploy using Helm:")
            print(f"   helm install mcp-server ./chart -f oidc-values.yaml")
        else:
            print(f"2. Pattern B (Keycloak native DCR) — no Kubernetes credentials secret needed.")
            print(f"3. Deploy using Helm:")
            print(f"   helm install mcp-server ./chart -f oidc-values.yaml")

    def generate_helm_values(
        self,
        config: Dict[str, Any],
        audience: str,
        required_scopes: Optional[List[str]] = None,
        ingress_host: Optional[str] = None,
        ingress_path: Optional[str] = None,
        ingress_tls_enabled: Optional[bool] = None,
        public_url: Optional[str] = None,
    ) -> None:
        """Generate Helm values file for deployment.

        Shape follows imp/cli-integration-contract.md §4. oidc.authType, redis.enabled,
        and jwt.enabled are always emitted explicitly so the chart can branch on pattern
        without relying on defaults.

        ``required_scopes`` and the ``ingress_*`` / ``public_url`` overrides come
        from ``mcp-project.yaml`` when present; falsy values keep the existing
        example defaults so unset projects don't regress.
        """
        issuer = config["issuer"]
        provider_name = config.get("provider", "generic")
        pattern = config.get("pattern", pattern_for_provider(provider_name))
        auth_type = "keycloak" if provider_name == "keycloak" else "oidc"
        is_proxy = pattern == "proxy"
        server_client = config.get("server_client", {}) if is_proxy else {}
        client_id = server_client.get("client_id", "")

        # Extract hostname from audience URL for ingress; project file overrides.
        parsed = urlparse(audience)
        effective_ingress_host = ingress_host or parsed.netloc or "mcp-api.example.com"
        effective_ingress_path = ingress_path or "/"
        effective_tls_enabled = (
            ingress_tls_enabled if ingress_tls_enabled is not None else True
        )

        public_url_block = ""
        if public_url:
            public_url_block = f'  publicUrl: "{public_url}"\n\n'

        # Pattern A defaults: don't emit requiredScopes unless the project says so.
        # Pattern B default: ["openid", "mcp-scope"] — kept for backward compat
        # with the audience-mapper-based DCR flow when no project file is present.
        if required_scopes is not None:
            scopes_yaml = "[" + ", ".join(f'"{s}"' for s in required_scopes) + "]"
        else:
            scopes_yaml = None

        if is_proxy:
            scopes_line = ""
            if scopes_yaml is not None:
                scopes_line = f"\n  # Required OAuth scopes (from mcp-project.yaml)\n  requiredScopes: {scopes_yaml}\n"
            pattern_block = f"""  # OAuth client ID for MCP server (Pattern A — OAuth Proxy)
  clientId: "{client_id}"
{scopes_line}
  # NOTE: Client secret is automatically loaded from Kubernetes secret
  #   Secret name: <release-name>-oidc-credentials
  #   Secret key: server-client-secret
  # Create the secret with:
  #   mcp-base create-secrets --namespace <namespace> --release-name <release-name>

  # Optional: Override JWKS URI if needed
  # jwksUri: "{issuer}/.well-known/jwks.json"
"""
        else:
            effective_scopes = scopes_yaml or '["openid", "mcp-scope"]'
            pattern_block = f"""  # Pattern B — FastMCP's KeycloakAuthProvider uses Keycloak native DCR.
  # No pre-registered client, no client secret, no Kubernetes credentials secret.
  requiredScopes: {effective_scopes}
"""

        redis_enabled = "true" if is_proxy else "false"
        jwt_enabled = "true" if is_proxy else "false"
        tls_enabled = "true" if effective_tls_enabled else "false"

        helm_values = f"""# Helm Values for MCP Server with {provider_name.upper()} OIDC
# Generated by mcp-base setup-oidc --provider {provider_name}
# Deploy with: helm install mcp-server ./chart -f oidc-values.yaml

# Container image configuration
image:
  repository: your-registry.example.com/mcp-server
  pullPolicy: Always
  tag: ""  # Leave empty to use Chart.AppVersion

# Number of replicas
replicaCount: 1

# OIDC Configuration
oidc:
  # Authentication type — drives Pattern A vs Pattern B chart branches.
  # See imp/cli-integration-contract.md §4.
  authType: "{auth_type}"

  # OIDC issuer URL (Keycloak: realm URL)
  issuer: "{issuer}"

  # API audience (identifier)
  audience: "{audience}"

{public_url_block}{pattern_block}
# Redis subchart — required for Pattern A OAuth session persistence;
# MUST be disabled for Pattern B (Keycloak native DCR).
redis:
  enabled: {redis_enabled}

# JWT signing key Secret — Pattern A only.
jwt:
  enabled: {jwt_enabled}

# Service configuration
service:
  type: ClusterIP

# Ingress (configure for external access)
ingress:
  enabled: true
  className: "nginx"
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt"
  host: {effective_ingress_host}
  path: {effective_ingress_path}
  pathType: Prefix
  tls:
    enabled: {tls_enabled}
    # secretName auto-generated as: <release-name>-tls

# Resource limits
resources:
  requests:
    memory: "256Mi"
    cpu: "100m"
  limits:
    memory: "512Mi"
    cpu: "500m"

# Security
podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1000
  fsGroup: 1000

securityContext:
  allowPrivilegeEscalation: false
  capabilities:
    drop:
    - ALL
"""

        helm_file = "oidc-values.yaml"
        with open(helm_file, "w") as f:
            f.write(helm_values)
        print(f"✅ Created {helm_file}")
        print(f"   Ready to deploy: helm install mcp-server ./chart -f {helm_file}")


def get_env_or_prompt(
    env_var: str,
    prompt: str,
    required: bool = True,
    default: Optional[str] = None,
    secret: bool = False,
    existing_value: Optional[str] = None
) -> str:
    """Get value from environment, existing config, or prompt user."""
    # Check existing value first
    if existing_value:
        print(f"  {prompt}: {existing_value} (from config)")
        if sys.stdin.isatty():
            user_input = input(f"  Press Enter to keep, or type new value: ").strip()
            if user_input:
                return user_input
        return existing_value

    # Check environment variable
    value = os.getenv(env_var)
    if value:
        if secret:
            print(f"  {prompt}: ******* (from {env_var})")
        else:
            print(f"  {prompt}: {value} (from {env_var})")
        return value

    # Prompt user if interactive
    if sys.stdin.isatty():
        if default:
            user_input = input(f"  {prompt} [{default}]: ").strip()
            return user_input if user_input else default
        else:
            if secret:
                import getpass
                return getpass.getpass(f"  {prompt}: ")
            else:
                user_input = input(f"  {prompt}: ").strip()
                if required and not user_input:
                    print(f"    Error: {prompt} is required")
                    sys.exit(1)
                return user_input
    elif required and not default:
        print(f"Error: {env_var} environment variable required in non-interactive mode")
        sys.exit(1)

    return default or ""


def main():
    parser = argparse.ArgumentParser(
        prog="mcp-base setup-oidc --provider generic",
        description="Setup generic OIDC provider for MCP (Dex, Keycloak, etc.)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  OIDC_ISSUER            OIDC issuer URL
  OIDC_AUDIENCE          API audience/identifier
  OIDC_CLIENT_ID         OAuth client ID
  OIDC_CLIENT_SECRET     OAuth client secret

Examples:
  # Interactive mode
  mcp-base setup-oidc --provider generic

  # Non-interactive with Dex
  mcp-base setup-oidc --provider dex \\
      --issuer https://dex.example.com \\
      --audience https://mcp-server.example.com/mcp \\
      --client-id my-client-id \\
      --client-secret my-client-secret

  # From environment variables
  export OIDC_ISSUER=https://dex.example.com
  export OIDC_AUDIENCE=https://mcp-server.example.com/mcp
  export OIDC_CLIENT_ID=my-client-id
  export OIDC_CLIENT_SECRET=my-client-secret
  mcp-base setup-oidc --provider generic

Required Redirect URLs (configure in your IdP):
  - {mcp_base_url}/auth/callback (e.g., https://mcp-server.example.com/auth/callback)
  - https://claude.ai/api/mcp/auth_callback
  - http://localhost:8888/callback (for local testing)
        """
    )

    parser.add_argument(
        "--issuer",
        help="OIDC issuer URL (e.g., https://dex.example.com)"
    )

    parser.add_argument(
        "--audience",
        help="API audience/identifier (e.g., https://mcp-server.example.com/mcp)"
    )

    parser.add_argument(
        "--client-id",
        help="OAuth client ID"
    )

    parser.add_argument(
        "--client-secret",
        help="OAuth client secret"
    )

    parser.add_argument(
        "--config-file",
        default=DEFAULT_CONFIG_FILE,
        help=f"Configuration file path (default: {DEFAULT_CONFIG_FILE})"
    )

    parser.add_argument(
        "--provider-name",
        default="generic",
        choices=["generic", "dex", "keycloak", "okta"],
        help="Provider name for documentation purposes (default: generic)"
    )

    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip OIDC issuer validation"
    )

    parser.add_argument(
        "--fix",
        action="store_true",
        help="Interactively reconcile drift between mcp-project.yaml and the saved config (TTY only)"
    )

    args = parser.parse_args()

    # Load mcp-project.yaml defaults (CWD-only, never raises). Precedence:
    # CLI flag > env var > mcp-project.yaml > saved oidc-config.json > prompt.
    project = load_project_defaults()
    for warning in project.warnings:
        print(f"⚠️  {warning}")
    if project.loaded:
        print(f"📄 Loaded project defaults from {project.source_path}")

    # Initialize setup
    setup = GenericOIDCSetup(config_file=args.config_file)

    print("\n🔧 Generic OIDC Provider Configuration")
    print("=" * 70)

    existing_config = setup.config
    server_client = existing_config.get("server_client", {})

    # Reconcile mcp-project.yaml with saved artifacts. Auto-adds fields that
    # are missing in the project file; conflicts prompt under --fix. Without
    # --fix this is print-only.
    if existing_config:
        reconcile_from_command(existing_config, args.config_file, fix=args.fix)

    issuer = resolve(
        args.issuer, "OIDC_ISSUER", project.issuer, existing_config.get("issuer")
    ) or get_env_or_prompt(
        "OIDC_ISSUER",
        "OIDC Issuer URL (e.g., https://dex.example.com)",
        required=True,
        existing_value=existing_config.get("issuer"),
    )

    audience = resolve(
        args.audience, "OIDC_AUDIENCE", project.audience, existing_config.get("audience")
    ) or get_env_or_prompt(
        "OIDC_AUDIENCE",
        "API Audience/Identifier (e.g., https://mcp-server.example.com/mcp)",
        required=True,
        existing_value=existing_config.get("audience"),
    )

    pattern = pattern_for_provider(args.provider_name)

    client_id: Optional[str] = None
    client_secret: Optional[str] = None

    if pattern == "proxy":
        # Project file does NOT carry client credentials (secrets must not live
        # in a checked-in YAML); fall through env/saved/prompt as before.
        client_id = args.client_id or get_env_or_prompt(
            "OIDC_CLIENT_ID",
            "OAuth Client ID",
            required=True,
            existing_value=server_client.get("client_id")
        )

        client_secret = args.client_secret or get_env_or_prompt(
            "OIDC_CLIENT_SECRET",
            "OAuth Client Secret",
            required=True,
            secret=True,
            existing_value=server_client.get("client_secret")
        )
    else:
        # Pattern B (Keycloak native DCR) — no pre-registered client needed.
        if args.client_id or args.client_secret:
            print(
                "\nℹ️  --client-id / --client-secret are not used for Keycloak "
                "(Pattern B: native DCR). They will not be persisted."
            )
        client_id = args.client_id
        client_secret = args.client_secret

    # Run setup
    setup.setup(
        issuer=issuer,
        audience=audience,
        client_id=client_id,
        client_secret=client_secret,
        provider_name=args.provider_name,
        validate=not args.skip_validation,
        required_scopes=project.required_scopes,
        ingress_host=project.ingress_host,
        ingress_path=project.ingress_path,
        ingress_tls_enabled=project.ingress_tls_enabled,
        public_url=project.public_url,
    )


if __name__ == "__main__":
    main()
