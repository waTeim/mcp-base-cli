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
   - `create_secrets.py` - Create Kubernetes secrets
   - `setup_oidc.py` - Router for OIDC providers (currently only Auth0)
   - `setup_rbac.py` - Set up Kubernetes RBAC
   - `make_config.py` - Generate configuration files (not exposed via CLI)

3. **Provider-Specific Implementation**: `setup_oidc.py` delegates to provider-specific modules (e.g., `setup_auth0.py`).

### Configuration Management
The Auth0 setup uses a `ConfigManager` class that:
- Loads configuration from `auth0-config.json`
- Supports precedence: CLI args > Environment variables > Saved config
- Saves non-sensitive configuration to disk
- Never persists tokens or sensitive credentials

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

### Auth0 API Integration
`setup_auth0.py` performs comprehensive Auth0 setup:
- Creates/updates Resource Server (API)
- Creates M2M application for DCR
- Configures allowed clients array
- Sets up grants and permissions
- Saves configuration for subsequent runs

### Secret Generation
Secrets are generated securely:
- JWT keys: 256-bit hex tokens via `secrets.token_hex(32)`
- Storage keys: Fernet keys via `cryptography.fernet.Fernet.generate_key()`

### Error Handling
The codebase uses direct error printing and `sys.exit(1)` for fatal errors. Missing dependencies are caught at import time with helpful installation messages.
