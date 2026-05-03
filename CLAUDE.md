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

**Authoritative source**: `imp/cli-integration-contract.md` defines the wire format between this CLI, the `mcp-base` Helm chart, and scaffolded MCP servers (filenames, Secret layout, ConfigMap shape, mount paths). Read it first if an artifact is under review.

### Authentication patterns

Every provider resolves to exactly one pattern. The CLI writes a `pattern` field into `oidc-config.json` / `auth0-config.json`; downstream commands branch on it.

| Pattern | Providers | FastMCP class | Pre-registered client | Redis | JWT key | K8s Secrets |
|---|---|---|---|---|---|---|
| `proxy` (A) | `auth0`, `dex`, `okta`, `generic` | `Auth0Provider` / `OIDCAuthProvider` | required | required | required | `<release>-oidc-credentials` + `<release>-jwt-signing-key` |
| `remote` (B) | `keycloak` (≥ 26.6.0 + FastMCP ≥ 3.2.4) | `KeycloakAuthProvider` | **no** | **no** | **no** | **none** (create-secrets is a no-op) |

### Non-interactive invocation rules
- Every subcommand prompts when stdin is a TTY and a required value is missing. To guarantee non-interactive behavior, pass every required flag explicitly or set the documented env var.
- Fatal errors exit with status `1` and print to stdout (not stderr). Check the exit code, not the output text.
- Config files are written to the current working directory: `auth0-config.json` (Auth0) or `oidc-config.json` (generic). `cd` into a stable directory before invoking, and read back from there.
- `create-secrets` auto-detects which config file exists in CWD. If both exist, behavior is file-specific — pick one directory per environment.

### `mcp-project.yaml` as a defaults source
`setup-oidc` auto-detects `./mcp-project.yaml` (CWD only — no parent walk). When present, its `publicEndpoint` and `auth` blocks supply defaults with this precedence:

> CLI flag > env var > `mcp-project.yaml` > saved `oidc-config.json` / `auth0-config.json` > interactive prompt

Mappings:
- `publicEndpoint.host` / `scheme` / `path` → `ingress.host` / `ingress.tls.enabled` (`https` → true) / `ingress.path` in `oidc-values.yaml` (and `auth0-values.yaml`).
- `publicEndpoint.{scheme,host,mcpPath}` → default `oidc.audience` = `<scheme>://<host><mcpPath>` if `auth.audience` is omitted.
- `publicEndpoint.{scheme,host,path}` → `oidc.publicUrl`.
- `auth.type` → `--provider`: `keycloak`/`auth0` map directly; `oidc` maps to `auth.providerName` (one of `dex`, `okta`, `generic`; defaults to `generic`).
- `auth.issuer` / `auth.audience` / `auth.requiredScopes` → `oidc.issuer` / `oidc.audience` / `oidc.requiredScopes` (verbatim — `openid` is **not** auto-injected).
- `auth.auth0.domain` / `auth.auth0.apiIdentifier` → Auth0 `--domain` / `--api-identifier`. Tokens never live in the project file.
- Client credentials (`OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET`, `AUTH0_MGMT_TOKEN`) are **never** read from `mcp-project.yaml` — they stay on flags / env / saved config only.

When the project file is absent (or omits a field), the CLI falls back to the existing flag/env/prompt flow without changing behavior.

### Reconciling `mcp-project.yaml` with saved artifacts (`--fix`)
`mcp-project.yaml` is the intended source of truth, but on existing deployments the CLI artifacts (`oidc-config.json` / `auth0-config.json` / `oidc-values.yaml`) came first. Every subcommand that touches those artifacts compares the project file against them and surfaces drift in two directions:

**Saved → project** (bootstrap fields the project file is missing):
- `oidc-config.json`: `provider` (→ `auth.type` + optional `auth.providerName`), `issuer`, `audience`
- `auth0-config.json`: `domain` (→ `auth.auth0.domain`), `audience` (→ `auth.auth0.apiIdentifier` and `auth.audience`)
- `oidc-values.yaml`: `oidc.requiredScopes` (→ `auth.requiredScopes`), `ingress.host` / `ingress.tls.enabled` / `ingress.path` (→ `publicEndpoint.host` / `scheme` / `path`), with `publicEndpoint.mcpPath` derived from the audience URL path. The CLI's example placeholder host (`mcp-api.example.com`) is never proposed.

**Project → values** (project is canonical for these fields, so a stale `oidc-values.yaml` should match it):
- `build.{registry,imageName}` → `image.repository`, `build.tag` → `image.tag` (always considered)
- `deployment.serviceType` → `service.type`, `deployment.testSidecarEnabled` → `testSidecar.enabled` (always considered)
- `publicEndpoint.{host,path,scheme}` → `ingress.{host,path,tls.enabled}` and `oidc.publicUrl` (only when the project file has a `publicEndpoint:` block — we don't second-guess users who haven't opted into the new schema)

**Project ↔ CLI flags** (`create-secrets`, `setup-rbac`):
- `deployment.namespace` ↔ `--namespace`, `deployment.helmRelease` ↔ `--release-name`, `project.name` ↔ `--app-name`
- When a flag is omitted and the project supplies the value, the project value is used silently.
- When a flag is explicit and disagrees with the project, drift is logged. With `--fix` on a TTY, the user picks the winner per-flag and it's applied for the current run only (the CLI invocation isn't rewritten).
- `--release-name` is no longer required when `deployment.helmRelease` is set in the project file.

Without `--fix`: print a summary of missing / conflicting fields and continue. **No file is modified.**

With `--fix` (`setup-oidc`, `create-secrets`, `add-user`):
- Project additions (saved → project): missing fields **auto-added**; existing-but-different fields prompt for which side wins.
- Values updates (project → values): when the values side looks like a placeholder (e.g., `your-registry.example.com/mcp-server`, empty tag, `mcp-api.example.com`, missing block) the change is **auto-applied**; real divergences prompt.
- Conflict prompts use a numbered winner-pick — `[1]` keeps the file as-is, `[2]` writes the other side in. `[a]` = pick `[2]` for all remaining; `[s]` = pick `[1]` for all remaining. Example:
  ```
  • auth.requiredScopes
      [1] mcp-project.yaml: ['jupyter-mcp']
      [2] saved config:     ['openid', 'mcp-scope']
    Which is correct? [1/2/a=all-2/s=skip-all]:
  ```
- Non-TTY `--fix` skips conflict prompts (auto-applies still happen).
- Both files are rewritten with `ruamel.yaml`, preserving comments and key order.

`setup-oidc` also uses `mcp-project.build` and `publicEndpoint` directly when generating a fresh `oidc-values.yaml`, so a post-`--fix` regeneration produces a values file that matches the project from the start.

### Command contracts

**`setup-oidc --provider auth0`** (full automation; writes `auth0-config.json`)
Required: `--domain`, `--api-identifier`, `--token` (or env `AUTH0_MGMT_TOKEN`).
Side effects: creates/updates Auth0 Resource Server, M2M client, server client, grants. `--recreate-client` forces client recreation if secrets are lost.

**`setup-oidc --provider {dex,okta,generic}`** — Pattern A (writes `oidc-config.json`)
Required: `--issuer`, `--audience`, `--client-id`, `--client-secret` (or env `OIDC_ISSUER` / `OIDC_AUDIENCE` / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET`).
Flags: `--skip-validation` disables the discovery HTTP probe (falls back to provider-specific endpoint paths — see Generic OIDC Setup below). `--config-file PATH` overrides the default config path.
Side effects: writes `oidc-config.json` (includes `"pattern": "proxy"` and `server_client` block) and `oidc-values.yaml` (Helm values with `oidc.authType: "oidc"`, `redis.enabled: true`, `jwt.enabled: true`) in CWD.

**`setup-oidc --provider keycloak`** — Pattern B (writes `oidc-config.json`)
Required: `--issuer` (realm URL), `--audience`. **No `--client-id` / `--client-secret`**; any passed are ignored with a warning and not persisted.
Side effects: writes `oidc-config.json` with `"pattern": "remote"` and **no `server_client` block**, plus `oidc-values.yaml` with `oidc.authType: "keycloak"`, `redis.enabled: false`, `jwt.enabled: false`. Subsequent `create-secrets` is a no-op.
**Requires FastMCP ≥ 3.2.4 on the MCP server AND Keycloak ≥ 26.6.0 on the IdP** — both minimums must hold. FastMCP is the framework the MCP server is built with (not a server itself); check the MCP server project's `pyproject.toml` / lockfile. See `KEYCLOAK-HOWTO.md` for realm + DCR setup.

**`create-secrets`** (requires `[kubernetes]` extra)
Required: `--namespace`, `--release-name`. Reads `auth0-config.json` or `oidc-config.json` from CWD. Flags: `--dry-run`, `--force` (replace existing secrets).
Checks `pattern` from the config file **before** touching kubeconfig: `"remote"` (Keycloak) exits `0` immediately with a notice and never contacts the cluster. `"proxy"` creates `<release>-oidc-credentials` (standardized name; the Auth0 path previously used `-auth0-credentials`) and `<release>-jwt-signing-key`.

**`setup-rbac`** (requires `[kubernetes]` extra)
Required: `--app-name` (or `project.name` in `mcp-project.yaml`). Optional: `--namespace` (falls back to `deployment.namespace`), `--scope {cluster,namespace}` (default `cluster`), `--dry-run`, `--delete`, `--fix`.

**`add-user`**
Required: `--email`, `--client-type {server,test,both}` (`server`=production, `test`=testing). Mutates `auth0-config.json` in CWD.

### Environment variables (for non-interactive runs)
| Variable | Consumed by |
|---|---|
| `AUTH0_DOMAIN`, `AUTH0_API_IDENTIFIER`, `AUTH0_MGMT_TOKEN` | `setup-oidc --provider auth0` |
| `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | `setup-oidc --provider {dex,keycloak,okta,generic}` |

### Typical agent pipeline

Pattern B (Keycloak — no client creds, `create-secrets` is a no-op):
```bash
cd /run/mcp-deploy && \
  mcp-base setup-oidc --provider keycloak \
    --issuer "$OIDC_ISSUER" --audience "$OIDC_AUDIENCE" && \
  mcp-base create-secrets --namespace mcp --release-name my-mcp && \
  mcp-base setup-rbac --namespace mcp --app-name my-mcp --scope namespace
```

Pattern A (Dex/Okta/generic — pre-registered client, create-secrets writes two Secrets):
```bash
cd /run/mcp-deploy && \
  mcp-base setup-oidc --provider dex \
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

**Auth0 Setup** (`setup_auth0.py`) — always Pattern A:
- Uses a `ConfigManager` class for configuration
- Saves to `auth0-config.json` with `"provider": "auth0"`, `"pattern": "proxy"`, and comprehensive Auth0 metadata
- Supports precedence: CLI args > Environment variables > Saved config
- Automatically creates/updates OIDC applications, APIs, and grants
- Never persists tokens or sensitive credentials
- Generated `auth0-values.yaml` includes `oidc.authType: "auth0"`, `redis.enabled: true`, `jwt.enabled: true`

**Generic OIDC Setup** (`setup_generic.py`) — Pattern A or B depending on provider:
- Pattern is derived by `pattern_for_provider()`: `keycloak` → `"remote"`, everything else → `"proxy"`
- Saves to `oidc-config.json` with the `pattern` field; **Pattern B omits `server_client` entirely**
- For Pattern B, `--client-id` / `--client-secret` are ignored with a warning
- Validates issuer by checking `.well-known/openid-configuration`; **on success the discovery document's `authorization_endpoint`, `token_endpoint`, and `jwks_uri` are written to the saved config verbatim** — never guessed.
- When validation is skipped (`--skip-validation`) or discovery fails, endpoints fall back to provider-specific defaults via `fallback_endpoints()`:
  - `keycloak` → `{issuer}/protocol/openid-connect/{auth,token,certs}`
  - `okta` → `{issuer}/v1/{authorize,token,keys}`
  - `dex` / `generic` / anything else → `{issuer}/auth`, `{issuer}/token`, `{issuer}/.well-known/jwks.json`
- Generated `oidc-values.yaml`:
  - Pattern A: `oidc.authType: "oidc"`, `oidc.clientId: <id>`, `redis.enabled: true`, `jwt.enabled: true`
  - Pattern B: `oidc.authType: "keycloak"`, no `clientId`, `redis.enabled: false`, `jwt.enabled: false`, `oidc.requiredScopes: ["openid"]`
- Displays required redirect URLs for manual IdP configuration (Pattern A only — Pattern B redirects are chosen by DCR at runtime)

**create_secrets.py** auto-detects which config file to use (auth0-config.json or oidc-config.json), peeks at the `pattern` field **before** connecting to Kubernetes, and:
- Pattern B (`remote`) → prints a notice and exits `0` without touching the cluster
- Pattern A (`proxy`) → creates `<release>-oidc-credentials` (standardized name; Auth0 path previously wrote `<release>-auth0-credentials`) and `<release>-jwt-signing-key`
- Older configs without a `pattern` field: inferred from `provider` (`keycloak` → `remote`, else `proxy`)

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
