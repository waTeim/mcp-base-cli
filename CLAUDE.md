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

The project uses `publish.py` as the canonical publish entry point; `Makefile` wraps it with fixed token filenames.

```bash
# Makefile targets (preferred)
make build       # Build only. Artifacts land in dist/
make dev         # Publish to Test PyPI using ./test.token
make prod        # Publish to production PyPI using ./prod.token (publish.py prompts for "yes" confirmation)
make test        # pytest
make clean       # Remove dist/, build/, *.egg-info

# Direct publish.py invocation
python publish.py --build                            # Build only
python publish.py --token-file test.token            # Test PyPI
python publish.py --prod --token-file prod.token     # Production PyPI (interactive confirmation)
```

Token files (`test.token`, `prod.token`) contain a raw PyPI API token on a single line. `.gitignore` covers `*.token`. The Makefile refuses to run if the expected file is missing or empty.

## Agent Usage Guide

This section is written for automated agents invoking `mcp-base`. Prefer these patterns over the interactive flows in `README.md`.

### Non-interactive invocation rules
- Every subcommand prompts when stdin is a TTY and a required value is missing. To guarantee non-interactive behavior, pass every required flag explicitly or set the documented env var.
- Fatal errors exit with status `1` and print to stdout (not stderr). Check the exit code, not the output text.
- Config files are written to the current working directory: `auth0-config.json` (Auth0) or `oidc-config.json` (generic). `cd` into a stable directory before invoking, and read back from there.
- `create-secrets` auto-detects which config file exists in CWD. If both exist, behavior is file-specific — pick one directory per environment.

### Command contracts

**`setup-oidc --provider auth0`** (full automation; writes `auth0-config.json`)
Required: `--domain`, `--api-identifier`, `--token` (or env `AUTH0_MGMT_TOKEN`).
Side effects: creates/updates Auth0 Resource Server, M2M client, server client, grants. `--recreate-client` forces client recreation if secrets are lost.

**`setup-oidc --provider {dex,keycloak,okta,generic}`** (writes `oidc-config.json`)
Required: `--issuer`, `--audience`, `--client-id`, `--client-secret` (or env `OIDC_ISSUER` / `OIDC_AUDIENCE` / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET`).
Flags: `--skip-validation` disables the discovery HTTP probe (falls back to provider-specific endpoint paths — see Generic OIDC Setup below). `--config-file PATH` overrides the default config path.
Side effects: writes `oidc-config.json` and `oidc-values.yaml` (Helm values) in CWD.
For Keycloak, the client must exist with an Audience mapper before this command runs — see `keycloak-client-howto.md` for the step-by-step (UI + `kcadm.sh`) procedure. **Keycloak requires FastMCP ≥ 3.2.4 on the MCP server side AND Keycloak ≥ 26.6.0 on the IdP side** — both minimums must hold. FastMCP is the framework the MCP server is built with (not a server itself), so check the MCP server project's `pyproject.toml` / lockfile.

**`create-secrets`** (requires `[kubernetes]` extra)
Required: `--namespace`, `--release-name`. Reads `auth0-config.json` or `oidc-config.json` from CWD. Flags: `--dry-run`, `--force` (replace existing secrets).

**`setup-rbac`** (requires `[kubernetes]` extra)
Required: `--app-name`. Optional: `--namespace`, `--scope {cluster,namespace}` (default `cluster`), `--dry-run`, `--delete`.

**`add-user`**
Required: `--email`, `--client-type {server,test,both}` (`server`=production, `test`=testing). Mutates `auth0-config.json` in CWD.

### Environment variables (for non-interactive runs)
| Variable | Consumed by |
|---|---|
| `AUTH0_DOMAIN`, `AUTH0_API_IDENTIFIER`, `AUTH0_MGMT_TOKEN` | `setup-oidc --provider auth0` |
| `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | `setup-oidc --provider {dex,keycloak,okta,generic}` |

### Typical agent pipeline
```bash
cd /run/mcp-deploy && \
  mcp-base setup-oidc --provider keycloak \
    --issuer "$OIDC_ISSUER" --audience "$OIDC_AUDIENCE" \
    --client-id "$OIDC_CLIENT_ID" --client-secret "$OIDC_CLIENT_SECRET" && \
  mcp-base create-secrets --namespace mcp --release-name my-mcp && \
  mcp-base setup-rbac --namespace mcp --app-name my-mcp --scope namespace
```

Publishing from an agent: use `make dev` / `make prod`; both require the corresponding `*.token` file in CWD and will exit `1` if missing. `make prod` still triggers `publish.py`'s interactive "yes/no" prompt, so pipe `yes` or call `publish.py --prod --token-file prod.token` directly if you need to bypass it.

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
- Validates issuer by checking `.well-known/openid-configuration`; **on success the discovery document's `authorization_endpoint`, `token_endpoint`, and `jwks_uri` are written to the saved config verbatim** — never guessed.
- When validation is skipped (`--skip-validation`) or discovery fails, endpoints fall back to provider-specific defaults via `fallback_endpoints()`:
  - `keycloak` → `{issuer}/protocol/openid-connect/{auth,token,certs}`
  - `okta` → `{issuer}/v1/{authorize,token,keys}`
  - `dex` / `generic` / anything else → `{issuer}/auth`, `{issuer}/token`, `{issuer}/.well-known/jwks.json`
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
