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
from typing import Dict, Any, Optional
from urllib.parse import urlparse


DEFAULT_CONFIG_FILE = "oidc-config.json"


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

    def validate_issuer(self, issuer: str) -> bool:
        """Validate OIDC issuer by checking .well-known/openid-configuration."""
        print(f"\n🔍 Validating OIDC issuer: {issuer}")

        issuer = issuer.rstrip('/')
        discovery_url = f"{issuer}/.well-known/openid-configuration"

        try:
            response = requests.get(discovery_url, timeout=10)
            response.raise_for_status()
            discovery = response.json()

            # Check required OIDC discovery fields
            required_fields = ['issuer', 'authorization_endpoint', 'token_endpoint', 'jwks_uri']
            missing_fields = [field for field in required_fields if field not in discovery]

            if missing_fields:
                print(f"❌ Invalid OIDC discovery document. Missing fields: {', '.join(missing_fields)}")
                return False

            print(f"✅ Valid OIDC provider found")
            print(f"   Authorization endpoint: {discovery.get('authorization_endpoint')}")
            print(f"   Token endpoint: {discovery.get('token_endpoint')}")
            print(f"   JWKS URI: {discovery.get('jwks_uri')}")

            return True

        except requests.RequestException as e:
            print(f"⚠️  Warning: Could not validate OIDC issuer: {e}")
            print(f"   Make sure the issuer is correct and accessible")
            return False

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
        client_id: str,
        client_secret: str,
        provider_name: str = "generic",
        validate: bool = True
    ) -> None:
        """
        Setup generic OIDC configuration.

        Args:
            issuer: OIDC issuer URL (e.g., https://dex.example.com)
            audience: API audience/identifier (e.g., https://mcp-server.example.com/mcp)
            client_id: OAuth client ID
            client_secret: OAuth client secret
            provider_name: Provider name (generic, dex, keycloak, etc.)
            validate: Whether to validate the issuer
        """
        print(f"\n🔧 Setting up {provider_name.upper()} OIDC provider for MCP")
        print("=" * 70)

        # Clean and validate inputs
        issuer = issuer.rstrip('/')
        audience = audience.rstrip('/')

        # Validate issuer if requested
        if validate:
            self.validate_issuer(issuer)

        # Show required redirect URLs
        self.show_redirect_urls(audience)

        # Build configuration
        config = {
            "provider": provider_name,
            "issuer": issuer,
            "audience": audience,
            "server_client": {
                "client_id": client_id,
                "client_secret": client_secret
            }
        }

        # Derive common endpoints
        config["authorization_endpoint"] = f"{issuer}/auth"
        config["token_endpoint"] = f"{issuer}/token"
        config["jwks_uri"] = f"{issuer}/.well-known/jwks.json"

        # Save configuration
        self.save_config(config)

        # Generate Helm values file
        self.generate_helm_values(config, audience)

        print("\n✅ Setup complete!")
        print(f"\nNext steps:")
        print(f"1. Ensure the redirect URLs above are configured in your {provider_name} provider")
        print(f"2. Create Kubernetes secrets:")
        print(f"   mcp-base create-secrets --namespace <namespace> --release-name <release-name>")
        print(f"3. Deploy using Helm:")
        print(f"   helm install mcp-server ./chart -f oidc-values.yaml")

    def generate_helm_values(self, config: Dict[str, Any], audience: str) -> None:
        """Generate Helm values file for deployment."""
        issuer = config["issuer"]
        client_id = config["server_client"]["client_id"]
        provider_name = config.get("provider", "generic")

        # Extract hostname from audience URL for ingress
        parsed = urlparse(audience)
        ingress_host = parsed.netloc or "mcp-api.example.com"

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
  # OIDC issuer URL
  issuer: "{issuer}"

  # API audience (identifier)
  audience: "{audience}"

  # OAuth client ID for MCP server
  clientId: "{client_id}"

  # NOTE: Client secret is automatically loaded from Kubernetes secret
  #   Secret name: <release-name>-oidc-credentials
  #   Secret key: server-client-secret
  # Create the secret with:
  #   mcp-base create-secrets --namespace <namespace> --release-name <release-name>

  # Optional: Override JWKS URI if needed
  # jwksUri: "{issuer}/.well-known/jwks.json"

# Service configuration
service:
  type: ClusterIP

# Ingress (configure for external access)
ingress:
  enabled: true
  className: "nginx"
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt"
  host: {ingress_host}
  path: /
  pathType: Prefix
  tls:
    enabled: true
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

    args = parser.parse_args()

    # Initialize setup
    setup = GenericOIDCSetup(config_file=args.config_file)

    # Get configuration values (CLI args > env vars > existing config > prompt)
    print("\n🔧 Generic OIDC Provider Configuration")
    print("=" * 70)

    existing_config = setup.config
    server_client = existing_config.get("server_client", {})

    issuer = args.issuer or get_env_or_prompt(
        "OIDC_ISSUER",
        "OIDC Issuer URL (e.g., https://dex.example.com)",
        required=True,
        existing_value=existing_config.get("issuer")
    )

    audience = args.audience or get_env_or_prompt(
        "OIDC_AUDIENCE",
        "API Audience/Identifier (e.g., https://mcp-server.example.com/mcp)",
        required=True,
        existing_value=existing_config.get("audience")
    )

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

    # Run setup
    setup.setup(
        issuer=issuer,
        audience=audience,
        client_id=client_id,
        client_secret=client_secret,
        provider_name=args.provider_name,
        validate=not args.skip_validation
    )


if __name__ == "__main__":
    main()
