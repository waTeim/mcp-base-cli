# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MCP Base is a CLI tool for setting up and managing MCP (Model Context Protocol) servers. It provides commands for OIDC authentication setup (Auth0), Kubernetes secret management, and RBAC configuration.

## Build and Development Commands

### Installation (Development Mode)
```bash
# Install with all dependencies
pip install -e ".[all]"

# Install only base (OIDC tools)
pip install -e .

# Install with Kubernetes support
pip install -e ".[kubernetes]"

# Install with development tools
pip install -e ".[dev]"
```

### Testing
```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov
```

### Code Quality
```bash
# Format code (line length: 100)
black src/

# Lint
ruff check src/

# Type checking (requires all functions to be typed)
mypy src/
```

### Building and Publishing
```bash
# Clean and build package
rm -rf dist/ build/ *.egg-info src/*.egg-info
python -m build

# Publish to Test PyPI using helper script
python publish.py

# Publish to production PyPI using helper script
python publish.py --prod

# Publish with token file
python publish.py --prod --token-file ~/.pypi-token

# Build only (no publish)
python publish.py --build
```

## Architecture

### CLI Entry Point Architecture
The CLI uses a two-level delegation pattern:

1. **Main CLI** (`src/mcp_base/cli.py`): Parses the top-level command (add-user, create-secrets, setup-oidc, setup-rbac) and delegates to subcommand modules.

2. **Subcommand Modules**: Each command has its own module with a `main()` function that handles argument parsing:
   - `add_user.py` - Add users to OIDC allowed clients
   - `create_secrets.py` - Create Kubernetes secrets (supports both Auth0 and generic OIDC)
   - `setup_oidc.py` - Router for OIDC providers (Auth0, Dex, Keycloak, generic)
   - `setup_rbac.py` - Set up Kubernetes RBAC
   - `make_config.py` - Generate configuration files (not exposed via CLI)

3. **Provider-Specific Implementation**: `setup_oidc.py` delegates to:
   - `setup_auth0.py` - Automated Auth0 tenant configuration
   - `setup_generic.py` - Pre-configured OIDC providers (Dex, Keycloak, Okta, etc.)

### Configuration Management

**Auth0 Setup** (`setup_auth0.py`):
- Uses a `ConfigManager` class for configuration
- Saves to `auth0-config.json` with comprehensive Auth0 metadata
- Supports precedence: CLI args > Environment variables > Saved config
- Automatically creates/updates OIDC applications, APIs, and grants
- Never persists tokens or sensitive credentials

**Generic OIDC Setup** (`setup_generic.py`):
- Simpler setup for pre-configured OIDC providers (Dex, Keycloak, Okta)
- Saves to `oidc-config.json` with minimal provider configuration
- Assumes client and secret are already configured in the IdP
- Validates issuer by checking `.well-known/openid-configuration`
- Displays required redirect URLs for manual IdP configuration

**create_secrets.py** auto-detects which config file to use (auth0-config.json or oidc-config.json) and creates appropriate Kubernetes secrets.

Key pattern: Configuration is the single source of truth, and the system can resume operations from saved state without re-entering most parameters.

### Kubernetes Integration
The `create_secrets.py` and `setup_rbac.py` modules:
- Auto-detect kubeconfig or in-cluster configuration
- Use the current namespace from kubectl context if not specified
- Generate secure keys automatically (JWT signing key, Fernet encryption key)
- Support dry-run mode to preview changes

### Dependencies
- **Required**: `requests>=2.28.0` (for Auth0 API calls)
- **Optional [kubernetes]**: `kubernetes>=28.0.0`, `cryptography>=41.0.0`
- **Optional [dev]**: `pytest`, `black`, `ruff`, `mypy`, etc.

### Python Version Support
Targets Python 3.9+ (see pyproject.toml for exact compatibility matrix).

## Key Patterns

### Argument Parsing
Subcommands use argparse and are invoked with `sys.argv` manipulation to preserve help messages and allow flexible argument handling.

### OIDC Provider Support

**Auth0 API Integration** (`setup_auth0.py`):
- Performs comprehensive automated Auth0 setup
- Creates/updates Resource Server (API)
- Creates M2M application for DCR
- Configures allowed clients array
- Sets up grants and permissions
- Saves configuration to `auth0-config.json`

**Generic OIDC** (`setup_generic.py`):
- Supports Dex, Keycloak, Okta, and any standard OIDC provider
- Validates OIDC discovery endpoint
- Displays required redirect URLs:
  - MCP Server: `{mcp_base_url}/auth/callback` (derived from audience)
  - Claude Desktop: `https://claude.ai/api/mcp/auth_callback`
  - Local testing: `http://localhost:8888/callback`
- Saves configuration to `oidc-config.json`

### Secret Generation
Secrets are generated securely:
- JWT keys: 256-bit hex tokens via `secrets.token_hex(32)`
- Storage keys: Fernet keys via `cryptography.fernet.Fernet.generate_key()`

### Error Handling
The codebase uses direct error printing and `sys.exit(1)` for fatal errors. Missing dependencies are caught at import time with helpful installation messages.
