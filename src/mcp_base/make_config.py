#!/usr/bin/env python3
"""
MCP Server - Configuration Generator

Creates configuration files required for deployment:
1. auth0-config.json - Auth0 application credentials
2. helm-values.yaml - Customized Helm values
3. .env - Local development environment file

Usage:
    # Interactive mode - prompts for all values
    python make-config.py --server-name "My Server"

    # From environment variables
    python make-config.py --server-name "My Server" --from-env

    # Specify values directly
    python make-config.py --server-name "My Server" --domain your-tenant.auth0.com --client-id xxx

    # Output to specific directory
    python make-config.py --server-name "My Server" --output-dir ./config
"""

import os
import sys
import json
import re
import argparse
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import urlparse


def to_snake_case(name: str) -> str:
    """Convert a name to snake_case."""
    # Replace spaces and hyphens with underscores
    s = re.sub(r'[\s\-]+', '_', name)
    # Insert underscore before uppercase letters and lowercase them
    s = re.sub(r'([A-Z])', r'_\1', s).lower()
    # Remove leading underscore and collapse multiple underscores
    s = re.sub(r'^_+', '', s)
    s = re.sub(r'_+', '_', s)
    return s


def get_env_or_prompt(
    env_var: str,
    prompt: str,
    required: bool = True,
    default: Optional[str] = None,
    secret: bool = False
) -> Optional[str]:
    """Get value from environment variable or prompt user."""
    value = os.environ.get(env_var)
    if value:
        if secret:
            print(f"  {prompt}: ******* (from {env_var})")
        else:
            print(f"  {prompt}: {value} (from {env_var})")
        return value

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
    return default


def validate_domain(domain: str) -> bool:
    """Validate Auth0 domain format."""
    if not domain:
        return False
    # Should be like: your-tenant.auth0.com or your-tenant.us.auth0.com
    if '.auth0.com' not in domain and '.auth0.co' not in domain:
        print(f"    Warning: Domain '{domain}' doesn't look like an Auth0 domain")
        return True  # Allow custom domains
    return True


def validate_url(url: str, name: str) -> bool:
    """Validate URL format."""
    try:
        result = urlparse(url)
        if not all([result.scheme, result.netloc]):
            print(f"    Error: {name} must be a valid URL")
            return False
        return True
    except:
        print(f"    Error: {name} must be a valid URL")
        return False


class ConfigGenerator:
    """Generate configuration files for MCP Server."""

    def __init__(
        self,
        server_name: str,
        server_name_snake: Optional[str] = None,
        default_port: int = 4208,
        default_namespace: str = "default",
        output_dir: Path = Path(".")
    ):
        self.server_name = server_name
        self.server_name_snake = server_name_snake or to_snake_case(server_name)
        self.default_port = default_port
        self.default_namespace = default_namespace
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.config: Dict[str, Any] = {}

    def collect_auth0_config(self, from_env: bool = False, args: Optional[argparse.Namespace] = None) -> Dict[str, Any]:
        """Collect Auth0 configuration."""
        print("\n=== Auth0 Configuration ===")

        # Domain
        domain = args.domain if args and args.domain else None
        if not domain:
            domain = get_env_or_prompt(
                "AUTH0_DOMAIN",
                "Auth0 Domain (e.g., your-tenant.auth0.com)",
                required=True
            )
        validate_domain(domain)

        # Client ID
        client_id = args.client_id if args and args.client_id else None
        if not client_id:
            client_id = get_env_or_prompt(
                "AUTH0_CLIENT_ID",
                "Auth0 Client ID",
                required=True
            )

        # Client Secret
        client_secret = args.client_secret if args and args.client_secret else None
        if not client_secret:
            client_secret = get_env_or_prompt(
                "AUTH0_CLIENT_SECRET",
                "Auth0 Client Secret",
                required=True,
                secret=True
            )

        # API Audience
        audience = args.audience if args and args.audience else None
        if not audience:
            default_audience = f"https://{self.server_name_snake.replace('_', '-')}.example.com/mcp"
            audience = get_env_or_prompt(
                "AUTH0_AUDIENCE",
                "Auth0 API Audience",
                required=True,
                default=default_audience
            )

        # Build issuer URL
        issuer = f"https://{domain}/"

        return {
            "domain": domain,
            "client_id": client_id,
            "client_secret": client_secret,
            "audience": audience,
            "issuer": issuer
        }

    def collect_kubernetes_config(self, args: Optional[argparse.Namespace] = None) -> Dict[str, Any]:
        """Collect Kubernetes deployment configuration."""
        print("\n=== Kubernetes Configuration ===")

        namespace = args.namespace if args and args.namespace else None
        if not namespace:
            namespace = get_env_or_prompt(
                "K8S_NAMESPACE",
                "Kubernetes Namespace",
                required=False,
                default=self.default_namespace
            )

        release_name = args.release_name if args and args.release_name else None
        if not release_name:
            release_name = get_env_or_prompt(
                "HELM_RELEASE_NAME",
                "Helm Release Name",
                required=False,
                default=self.server_name_snake.replace("_", "-")
            )

        return {
            "namespace": namespace,
            "release_name": release_name
        }

    def generate_auth0_config_json(self, auth0_config: Dict[str, Any]) -> Path:
        """Generate auth0-config.json file."""
        config = {
            "domain": auth0_config["domain"],
            "client_id": auth0_config["client_id"],
            "client_secret": auth0_config["client_secret"],
            "audience": auth0_config["audience"]
        }

        output_path = self.output_dir / "auth0-config.json"
        with open(output_path, 'w') as f:
            json.dump(config, f, indent=2)

        # Set restrictive permissions
        os.chmod(output_path, 0o600)

        print(f"\n  Created: {output_path}")
        print(f"    Permissions: 600 (owner read/write only)")
        return output_path

    def generate_env_file(self, auth0_config: Dict[str, Any], k8s_config: Dict[str, Any]) -> Path:
        """Generate .env file for local development."""
        env_content = f'''# {self.server_name} MCP Server - Environment Configuration
# Generated by make-config.py
# DO NOT commit this file to version control!

# Auth0 Configuration
OIDC_ISSUER={auth0_config["issuer"]}
OIDC_AUDIENCE={auth0_config["audience"]}
AUTH0_DOMAIN={auth0_config["domain"]}
AUTH0_CLIENT_ID={auth0_config["client_id"]}
AUTH0_CLIENT_SECRET={auth0_config["client_secret"]}

# Kubernetes Configuration
K8S_NAMESPACE={k8s_config["namespace"]}

# Server Configuration
PORT={self.default_port}

# Redis Configuration (for local development)
REDIS_URL=redis://localhost:6379/0
'''

        output_path = self.output_dir / ".env"
        with open(output_path, 'w') as f:
            f.write(env_content)

        # Set restrictive permissions
        os.chmod(output_path, 0o600)

        print(f"  Created: {output_path}")
        print(f"    Permissions: 600 (owner read/write only)")
        return output_path

    def generate_helm_values(self, auth0_config: Dict[str, Any], k8s_config: Dict[str, Any]) -> Path:
        """Generate custom Helm values file."""
        image_name = self.server_name_snake.replace("_", "-")
        values_content = f'''# {self.server_name} MCP Server - Helm Values
# Generated by make-config.py
# Use with: helm install {k8s_config["release_name"]} chart/ -f helm-values.yaml

replicaCount: 1

image:
  repository: {image_name}
  tag: latest
  pullPolicy: IfNotPresent

oidc:
  issuer: "{auth0_config["issuer"]}"
  audience: "{auth0_config["audience"]}"

# Auth0 credentials - these will be read from Kubernetes secrets
# Run: python create-secrets.py --namespace {k8s_config["namespace"]} --release-name {k8s_config["release_name"]}

redis:
  enabled: true
  architecture: standalone
  auth:
    enabled: false

service:
  type: ClusterIP
  port: {self.default_port}

ingress:
  enabled: false
  # className: nginx
  # hosts:
  #   - host: {image_name}.example.com
  #     paths:
  #       - path: /
  #         pathType: Prefix
'''

        output_path = self.output_dir / "helm-values.yaml"
        with open(output_path, 'w') as f:
            f.write(values_content)

        print(f"  Created: {output_path}")
        return output_path

    def generate_gitignore_entries(self) -> Path:
        """Generate .gitignore entries for config files."""
        gitignore_content = f'''# {self.server_name} MCP Server - Sensitive files
# Add these lines to your .gitignore

# Auth0 configuration (contains secrets)
auth0-config.json

# Environment files
.env
.env.local
.env.*.local

# Helm values with secrets
helm-values.yaml

# Token files
*-token.txt
*.token
'''

        output_path = self.output_dir / "gitignore-additions.txt"
        with open(output_path, 'w') as f:
            f.write(gitignore_content)

        print(f"  Created: {output_path}")
        print(f"    Add these entries to your .gitignore file")
        return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Generate configuration files for MCP Server deployment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode
  python make-config.py --server-name "My MCP Server"

  # With snake_case name override
  python make-config.py --server-name "My MCP Server" --server-name-snake my_mcp_server

  # Non-interactive with environment variables
  python make-config.py --server-name "My Server" --from-env

  # Specify all Auth0 values
  python make-config.py --server-name "My Server" \\
    --domain your-tenant.auth0.com \\
    --client-id xxx \\
    --client-secret yyy

  # Custom port and namespace
  python make-config.py --server-name "My Server" \\
    --port 8080 --default-namespace production
        """
    )

    # Required server identification
    parser.add_argument(
        "--server-name",
        required=True,
        help="Human-readable server name (e.g., 'My MCP Server')"
    )

    parser.add_argument(
        "--server-name-snake",
        help="Snake_case server name (default: auto-derived from --server-name)"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=4208,
        help="Default server port (default: 4208)"
    )

    parser.add_argument(
        "--default-namespace",
        default="default",
        help="Default Kubernetes namespace (default: default)"
    )

    parser.add_argument(
        "--from-env",
        action="store_true",
        help="Read all values from environment variables (non-interactive)"
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Directory to write configuration files (default: current directory)"
    )

    # Auth0 options
    parser.add_argument("--domain", help="Auth0 domain (e.g., your-tenant.auth0.com)")
    parser.add_argument("--client-id", help="Auth0 client ID")
    parser.add_argument("--client-secret", help="Auth0 client secret")
    parser.add_argument("--audience", help="Auth0 API audience")

    # Kubernetes options
    parser.add_argument("--namespace", help="Kubernetes namespace")
    parser.add_argument("--release-name", help="Helm release name")

    # Output options
    parser.add_argument(
        "--skip-env",
        action="store_true",
        help="Skip generating .env file"
    )
    parser.add_argument(
        "--skip-helm",
        action="store_true",
        help="Skip generating helm-values.yaml"
    )

    args = parser.parse_args()

    print(f"=== {args.server_name} Configuration Generator ===")

    generator = ConfigGenerator(
        server_name=args.server_name,
        server_name_snake=args.server_name_snake,
        default_port=args.port,
        default_namespace=args.default_namespace,
        output_dir=args.output_dir
    )

    print(f"\nServer Configuration:")
    print(f"  Name: {generator.server_name}")
    print(f"  Snake Case: {generator.server_name_snake}")
    print(f"  Default Port: {generator.default_port}")
    print(f"  Default Namespace: {generator.default_namespace}")

    # Collect configurations
    auth0_config = generator.collect_auth0_config(from_env=args.from_env, args=args)
    k8s_config = generator.collect_kubernetes_config(args=args)

    # Generate files
    print("\n=== Generating Configuration Files ===")

    generator.generate_auth0_config_json(auth0_config)

    if not args.skip_env:
        generator.generate_env_file(auth0_config, k8s_config)

    if not args.skip_helm:
        generator.generate_helm_values(auth0_config, k8s_config)

    generator.generate_gitignore_entries()

    # Print next steps
    print("\n=== Next Steps ===")
    print(f"1. Add entries from gitignore-additions.txt to your .gitignore")
    print(f"2. Create Kubernetes secrets:")
    print(f"   python create-secrets.py --namespace {k8s_config['namespace']} --release-name {k8s_config['release_name']}")
    print(f"3. Deploy with Helm:")
    print(f"   helm install {k8s_config['release_name']} chart/ -f helm-values.yaml")
    print(f"\nFor local development:")
    print(f"   source .env && python src/{generator.server_name_snake}.py")


if __name__ == "__main__":
    main()
