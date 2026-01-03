# MCP Base

CLI tools for MCP (Model Context Protocol) server setup and management. These tools help you configure OIDC authentication, create Kubernetes secrets, set up RBAC, and manage MCP server deployments.

## Installation

### From PyPI

```bash
# Install base package (OIDC tools only)
pip install mcp-base

# Install with Kubernetes support
pip install mcp-base[kubernetes]

# Install all optional dependencies
pip install mcp-base[all]
```

### From Source

```bash
git clone https://github.com/your-org/mcp-base.git
cd mcp-base
pip install -e ".[all]"
```

## Usage

After installation, the `mcp-base` command is available with the following subcommands:

```bash
mcp-base <command> [options]
```

| Command | Description |
|---------|-------------|
| `setup-oidc` | Set up OIDC provider (Auth0, etc.) for MCP authentication |
| `create-secrets` | Create Kubernetes secrets for MCP deployment |
| `make-config` | Generate configuration files |
| `setup-rbac` | Set up Kubernetes RBAC resources |
| `add-user` | Add users to allowed clients |

### Setting Up OIDC (Auth0)

Configure your OIDC provider for MCP authentication:

```bash
# Set up Auth0 (first run)
mcp-base setup-oidc --provider auth0 \
    --domain your-tenant.auth0.com \
    --api-identifier https://mcp-server.example.com/mcp \
    --token YOUR_MGMT_TOKEN

# Subsequent runs (uses saved configuration)
mcp-base setup-oidc --provider auth0

# Force recreate clients (if secrets are lost)
mcp-base setup-oidc --provider auth0 --recreate-client
```

### Creating Kubernetes Secrets

Create secrets required for MCP server deployment:

```bash
# Create secrets in a namespace
mcp-base create-secrets --namespace default --release-name my-mcp-server

# Dry run to see what would be created
mcp-base create-secrets --namespace default --release-name my-mcp-server --dry-run

# Replace existing secrets
mcp-base create-secrets --namespace default --release-name my-mcp-server --force
```

### Generating Configuration Files

Generate configuration files for deployment:

```bash
# Interactive mode
mcp-base make-config --server-name "My MCP Server"

# Non-interactive with environment variables
mcp-base make-config --server-name "My Server" --from-env

# Specify values directly
mcp-base make-config --server-name "My Server" \
    --domain your-tenant.auth0.com \
    --client-id xxx \
    --client-secret yyy
```

### Setting Up RBAC

Configure Kubernetes RBAC for MCP server:

```bash
# Cluster-wide permissions
mcp-base setup-rbac --namespace production --app-name my-mcp-server

# Namespace-scoped permissions
mcp-base setup-rbac --namespace production --app-name my-mcp-server --scope namespace

# Dry run
mcp-base setup-rbac --app-name my-mcp-server --dry-run

# Delete RBAC resources
mcp-base setup-rbac --app-name my-mcp-server --delete
```

### Adding Users to Allowed Clients

Add users to the allowedClients array in your OIDC provider:

```bash
# Interactive mode
mcp-base add-user

# Non-interactive
mcp-base add-user --email user@example.com --client-type both
```

## Dependencies

### Required
- `requests>=2.28.0` - HTTP client for Auth0 API

### Optional (Kubernetes support)
- `kubernetes>=28.0.0` - Kubernetes Python client
- `cryptography>=41.0.0` - For generating encryption keys

Install with: `pip install mcp-base[kubernetes]`

## Development

### Setup

```bash
# Clone the repository
git clone https://github.com/your-org/mcp-base.git
cd mcp-base

# Install in development mode with all dependencies
pip install -e ".[dev]"
```

### Running Tests

```bash
pytest
```

### Code Quality

```bash
# Format code
black src/

# Lint
ruff check src/

# Type checking
mypy src/
```

## Publishing to PyPI

### Prerequisites

1. Create an account on [PyPI](https://pypi.org/) and [Test PyPI](https://test.pypi.org/)
2. Install build tools:
   ```bash
   pip install build twine
   ```

### Build the Package

```bash
# Clean previous builds
rm -rf dist/ build/ *.egg-info src/*.egg-info

# Build source distribution and wheel
python -m build
```

This creates:
- `dist/mcp_base-0.1.0.tar.gz` (source distribution)
- `dist/mcp_base-0.1.0-py3-none-any.whl` (wheel)

### Test on Test PyPI (Recommended)

```bash
# Upload to Test PyPI
python -m twine upload --repository testpypi dist/*

# Test installation from Test PyPI
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ mcp-base
```

### Publish to PyPI

```bash
# Upload to PyPI
python -m twine upload dist/*
```

### Using API Tokens (Recommended)

Instead of username/password, use API tokens:

1. Go to PyPI > Account Settings > API tokens
2. Create a token with scope "Entire account" or project-specific
3. Use the token:
   ```bash
   python -m twine upload dist/* -u __token__ -p pypi-YOUR_TOKEN_HERE
   ```

Or create `~/.pypirc`:
```ini
[pypi]
username = __token__
password = pypi-YOUR_TOKEN_HERE

[testpypi]
username = __token__
password = pypi-YOUR_TEST_TOKEN_HERE
```

### Automated Publishing with GitHub Actions

Create `.github/workflows/publish.yml`:

```yaml
name: Publish to PyPI

on:
  release:
    types: [published]

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install build dependencies
        run: pip install build twine

      - name: Build package
        run: python -m build

      - name: Publish to PyPI
        env:
          TWINE_USERNAME: __token__
          TWINE_PASSWORD: ${{ secrets.PYPI_API_TOKEN }}
        run: python -m twine upload dist/*
```

Add your PyPI API token as a repository secret named `PYPI_API_TOKEN`.

## License

MIT License - see LICENSE file for details.
