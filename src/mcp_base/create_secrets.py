#!/usr/bin/env python3
"""
MCP Server - Create Kubernetes Secrets

Creates all required secrets for an MCP server deployment:
1. Auth0 credentials (from auth0-config.json)
2. JWT signing key (auto-generated)
3. Storage encryption key (auto-generated)

Requirements:
    pip install kubernetes cryptography

Usage:
    # Create all secrets
    python create-secrets.py --namespace <namespace> --release-name <release-name>

    # Dry run
    python create-secrets.py --namespace <namespace> --release-name <release-name> --dry-run

    # Replace existing secrets
    python create-secrets.py --namespace <namespace> --release-name <release-name> --replace
"""

import os
import sys
import json
import secrets
import argparse
import base64
from typing import Dict, Any, Optional

try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
except ImportError:
    print("Error: kubernetes Python package not installed")
    print("   Install with: pip install kubernetes")
    sys.exit(1)


def generate_jwt_signing_key() -> str:
    """Generate a secure 256-bit JWT signing key."""
    return secrets.token_hex(32)


def generate_storage_encryption_key() -> bytes:
    """Generate a secure Fernet encryption key for storage."""
    try:
        from cryptography.fernet import Fernet
        return Fernet.generate_key()
    except ImportError:
        print("Error: cryptography package not installed")
        print("   Install with: pip install cryptography")
        sys.exit(1)


class KubernetesSecretCreator:
    """Creates Kubernetes secrets from Auth0 configuration."""

    def __init__(
        self,
        namespace: Optional[str] = None,
        app_name: str = "mcp-server",
        dry_run: bool = False
    ):
        self.dry_run = dry_run
        self.app_name = app_name

        try:
            config.load_kube_config()
            print("Loaded kubeconfig")
        except config.config_exception.ConfigException:
            try:
                config.load_incluster_config()
                print("Loaded in-cluster config")
            except:
                print("Error: Could not load Kubernetes configuration")
                sys.exit(1)

        self.k8s_client = client.CoreV1Api()

        if namespace:
            self.namespace = namespace
            print(f"Using specified namespace: {self.namespace}")
        else:
            self.namespace = self._get_current_namespace()
            print(f"Using namespace from context: {self.namespace}")

        try:
            self.k8s_client.get_api_resources()
            print(f"Connected to Kubernetes cluster")
        except Exception as e:
            print(f"Error: Could not connect to Kubernetes cluster: {e}")
            sys.exit(1)

    def _get_current_namespace(self) -> str:
        """Get the current namespace from kubectl context."""
        try:
            _, active_context = config.list_kube_config_contexts()

            if active_context and 'context' in active_context:
                context = active_context['context']
                namespace = context.get('namespace', 'default')
                return namespace

            return 'default'
        except Exception:
            return 'default'

    def load_config(self, config_file: str) -> Dict[str, Any]:
        """Load configuration from auth0-config.json or oidc-config.json."""
        if not os.path.exists(config_file):
            print(f"Error: Configuration file not found: {config_file}")
            print("\nRun the setup script first:")
            print("  mcp-base setup-oidc --provider <provider>")
            sys.exit(1)

        print(f"Loading {config_file}...")

        try:
            with open(config_file, 'r') as f:
                config_data = json.load(f)
                print(f"   Loaded configuration successfully")
                return config_data
        except Exception as e:
            print(f"Error: Failed to load config file: {e}")
            sys.exit(1)

    def namespace_exists(self) -> bool:
        """Check if the namespace exists."""
        try:
            self.k8s_client.read_namespace(self.namespace)
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            raise

    def create_namespace(self) -> bool:
        """Create namespace if it doesn't exist."""
        if self.namespace == "default":
            return True

        if self.namespace_exists():
            print(f"Namespace {self.namespace} exists")
            return True

        print(f"Creating namespace: {self.namespace}")

        if self.dry_run:
            print(f"   [DRY RUN] Would create namespace: {self.namespace}")
            return True

        try:
            namespace = client.V1Namespace(
                metadata=client.V1ObjectMeta(
                    name=self.namespace,
                    labels={
                        "name": self.namespace,
                        "created-by": f"{self.app_name}-setup-script"
                    }
                )
            )
            self.k8s_client.create_namespace(namespace)
            print(f"Created namespace: {self.namespace}")
            return True
        except ApiException as e:
            print(f"Error: Failed to create namespace: {e.reason}")
            return False

    def secret_exists(self, name: str) -> bool:
        """Check if a secret exists."""
        try:
            self.k8s_client.read_namespaced_secret(name, self.namespace)
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            raise

    def delete_secret(self, name: str) -> bool:
        """Delete a secret."""
        try:
            self.k8s_client.delete_namespaced_secret(
                name=name,
                namespace=self.namespace,
                body=client.V1DeleteOptions()
            )
            return True
        except ApiException:
            return False

    def create_secret(
        self,
        name: str,
        data: Dict[str, str],
        labels: Optional[Dict[str, str]] = None,
        replace: bool = False
    ) -> bool:
        """Create a Kubernetes secret."""
        exists = self.secret_exists(name)

        if exists:
            if replace:
                print(f"Secret {name} exists, replacing...")
                if not self.dry_run:
                    self.delete_secret(name)
            else:
                print(f"Warning: Secret {name} already exists (use --replace to update)")
                return False

        encoded_data = {
            k: base64.b64encode(v.encode()).decode()
            for k, v in data.items()
        }

        final_labels = {
            "app": self.app_name,
            "managed-by": f"{self.app_name}-setup-script"
        }
        if labels:
            final_labels.update(labels)

        secret = client.V1Secret(
            api_version="v1",
            kind="Secret",
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=self.namespace,
                labels=final_labels
            ),
            type="Opaque",
            data=encoded_data
        )

        if self.dry_run:
            print(f"[DRY RUN] Would create secret: {name}")
            print(f"   Namespace: {self.namespace}")
            print(f"   Data keys: {', '.join(data.keys())}")
            return True

        try:
            print(f"Creating secret: {name}")
            self.k8s_client.create_namespaced_secret(
                namespace=self.namespace,
                body=secret
            )
            print(f"Created secret: {name}")
            print(f"   Keys: {', '.join(data.keys())}")
            return True
        except ApiException as e:
            print(f"Error: Failed to create secret: {e.reason}")
            return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Create all Kubernetes secrets for MCP Server deployment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create all secrets
  python create-secrets.py --namespace default --release-name my-release

  # Dry run
  python create-secrets.py --namespace default --release-name my-release --dry-run

  # Force replace existing secrets
  python create-secrets.py --namespace default --release-name my-release --force

Secrets Created:
  1. <release-name>-auth0-credentials
     - server-client-id: Server client ID (for FastMCP)
     - server-client-secret: Server client secret (for FastMCP)
     - mgmt-client-id: Management API client ID (for scripts)
     - mgmt-client-secret: Management API client secret (for scripts)
     - auth0-domain: Auth0 domain
     - connection-id: Auth0 connection ID

  2. <release-name>-jwt-signing-key
     - jwt-signing-key: JWT signing key for MCP tokens (256-bit hex)
     - storage-encryption-key: Fernet key for OAuth token encryption (base64)
        """
    )

    parser.add_argument(
        "--namespace", "-n",
        help="Kubernetes namespace (default: from current context)"
    )
    parser.add_argument(
        "--config-file",
        help="Path to config file (auto-detects auth0-config.json or oidc-config.json)",
        default=None
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without creating"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force replace secrets if they already exist"
    )
    parser.add_argument(
        "--release-name",
        help="Helm release name (used to generate secret name)",
        required=True
    )
    parser.add_argument(
        "--app-name",
        help="Application name for labels (default: mcp-server)",
        default="mcp-server"
    )

    args = parser.parse_args()

    # Auto-detect config file if not specified
    if args.config_file is None:
        if os.path.exists("./oidc-config.json"):
            args.config_file = "./oidc-config.json"
        elif os.path.exists("./auth0-config.json"):
            args.config_file = "./auth0-config.json"
        else:
            print("Error: No configuration file found")
            print("  Looking for: ./oidc-config.json or ./auth0-config.json")
            print("\nRun the setup script first:")
            print("  mcp-base setup-oidc --provider <provider>")
            sys.exit(1)

    print("=" * 70)
    print("Kubernetes Secret Creator for MCP Server")
    print("=" * 70)
    print()

    creator = KubernetesSecretCreator(
        namespace=args.namespace,
        app_name=args.app_name,
        dry_run=args.dry_run
    )

    print()

    auth_config = creator.load_config(args.config_file)

    # Detect provider type from config
    provider_type = auth_config.get('provider', 'auth0')  # Default to auth0 for backward compatibility
    is_auth0 = provider_type == 'auth0' or 'domain' in auth_config

    if is_auth0:
        # Auth0-specific validation
        required_keys = ['domain', 'issuer', 'audience', 'management_api', 'connection_id']
        missing = [key for key in required_keys if key not in auth_config]

        if missing:
            print(f"Error: Missing required configuration: {', '.join(missing)}")
            print("\nRun the setup script first:")
            print("  mcp-base setup-oidc --provider auth0")
            sys.exit(1)

        mgmt_api = auth_config.get('management_api', {})
        if not mgmt_api.get('client_secret'):
            print("\nWarning: Management client secret is empty")
            proceed = input("\nContinue anyway? (y/N): ")
            if proceed.lower() != 'y':
                print("Aborted.")
                sys.exit(0)
    else:
        # Generic OIDC validation
        required_keys = ['issuer', 'audience', 'server_client']
        missing = [key for key in required_keys if key not in auth_config]

        if missing:
            print(f"Error: Missing required configuration: {', '.join(missing)}")
            print("\nRun the setup script first:")
            print(f"  mcp-base setup-oidc --provider {provider_type}")
            sys.exit(1)

    print()
    print("=" * 70)
    print("Configuration Summary")
    print("=" * 70)
    print(f"Namespace:        {creator.namespace}")
    print(f"Dry Run:          {args.dry_run}")
    print(f"Force Replace:    {args.force}")
    print()

    server_client = auth_config.get('server_client', {})
    server_client_id = server_client.get('client_id', '')
    server_client_secret = server_client.get('client_secret', '')

    # Build secret data based on provider type
    if is_auth0:
        mgmt_api = auth_config.get('management_api', {})
        mgmt_secret = mgmt_api.get('client_secret', '')
        mgmt_client_id = mgmt_api.get('client_id', '')

        mgmt_data = {
            'server-client-id': server_client_id,
            'server-client-secret': server_client_secret,
            'mgmt-client-id': mgmt_client_id,
            'mgmt-client-secret': mgmt_secret,
            'auth0-domain': auth_config['domain'],
            'connection-id': auth_config['connection_id'],
        }

        print("Secret to create:")
        print()
        print(f"{args.release_name}-auth0-credentials (Auth0 credentials)")
        print("   Server Client Credentials (for FastMCP server):")
        print(f"     - server-client-id: {mgmt_data.get('server-client-id', 'N/A')}")
        print(f"     - server-client-secret: {'***hidden***' if mgmt_data.get('server-client-secret') else '***empty***'}")
        print()
        print("   Management API Credentials (for setup scripts):")
        print(f"     - mgmt-client-id: {mgmt_data.get('mgmt-client-id', 'N/A')}")
        print(f"     - mgmt-client-secret: {'***hidden***' if mgmt_data.get('mgmt-client-secret') else '***empty***'}")
        print()
        print("   Common Configuration:")
        print(f"     - auth0-domain: {mgmt_data.get('auth0-domain', 'N/A')}")
        print(f"     - connection-id: {mgmt_data.get('connection-id', 'N/A')}")
        print()
        secret_name = f"{args.release_name}-auth0-credentials"
    else:
        # Generic OIDC - simpler structure
        mgmt_data = {
            'server-client-id': server_client_id,
            'server-client-secret': server_client_secret,
            'issuer': auth_config['issuer'],
            'audience': auth_config['audience'],
        }

        print("Secret to create:")
        print()
        print(f"{args.release_name}-oidc-credentials ({provider_type.upper()} credentials)")
        print("   Server Client Credentials (for MCP server):")
        print(f"     - server-client-id: {mgmt_data.get('server-client-id', 'N/A')}")
        print(f"     - server-client-secret: {'***hidden***' if mgmt_data.get('server-client-secret') else '***empty***'}")
        print()
        print("   OIDC Configuration:")
        print(f"     - issuer: {mgmt_data.get('issuer', 'N/A')}")
        print(f"     - audience: {mgmt_data.get('audience', 'N/A')}")
        print()
        secret_name = f"{args.release_name}-oidc-credentials"

    if not args.dry_run:
        proceed = input("Proceed with secret creation? (y/N): ")
        if proceed.lower() != 'y':
            print("Aborted.")
            sys.exit(0)

    print()

    success = True

    component_label = "auth0-credentials" if is_auth0 else "oidc-credentials"
    if not creator.create_secret(
        name=secret_name,
        data=mgmt_data,
        labels={"component": component_label},
        replace=args.force
    ):
        success = False
    print()

    print("=" * 70)
    print("Creating JWT Signing Key and Storage Encryption Key Secret")
    print("=" * 70)
    print()

    jwt_key = generate_jwt_signing_key()
    storage_key = generate_storage_encryption_key()

    print(f"Generated JWT signing key: {jwt_key[:16]}...{jwt_key[-16:]}")
    print(f"Generated storage encryption key: {storage_key[:16]}...{storage_key[-16:]}")
    print()

    jwt_secret_name = f"{args.release_name}-jwt-signing-key"
    jwt_secret_data = {
        "jwt-signing-key": jwt_key,
        "storage-encryption-key": storage_key.decode()
    }

    if not creator.create_secret(
        name=jwt_secret_name,
        data=jwt_secret_data,
        labels={"component": "jwt-signing-key"},
        replace=args.force
    ):
        success = False
    print()

    print("=" * 70)

    if success:
        print("All secrets created successfully!")
        print("=" * 70)
        print()
        print("Next Steps:")
        print()
        print("1. Secrets created:")
        cred_type = "Auth0" if is_auth0 else f"{provider_type.upper()} OIDC"
        print(f"   {secret_name} ({cred_type} credentials)")
        print(f"   {jwt_secret_name} (JWT signing key + storage encryption key)")
        print()
        print("2. Deploy your MCP server:")
        print(f"   helm upgrade --install {args.release_name} ./chart -n {creator.namespace}")
        print()
        print("3. Verify secrets:")
        print(f"   kubectl describe secret {secret_name} -n {creator.namespace}")
        print(f"   kubectl describe secret {jwt_secret_name} -n {creator.namespace}")
        print()
    else:
        print("Error: Some secrets failed to create")
        print("=" * 70)
        sys.exit(1)


if __name__ == "__main__":
    main()
