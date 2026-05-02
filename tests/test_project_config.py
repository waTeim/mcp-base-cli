"""Tests for mcp_base.project_config — the mcp-project.yaml loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from mcp_base.project_config import (
    PROJECT_FILE,
    load_project_defaults,
    resolve,
)


def _write_project(tmp_path: Path, body: str) -> None:
    (tmp_path / PROJECT_FILE).write_text(body)


# ---- file discovery --------------------------------------------------------

def test_missing_file_returns_unloaded_defaults(tmp_path: Path) -> None:
    defaults = load_project_defaults(cwd=str(tmp_path))
    assert defaults.loaded is False
    assert defaults.provider_name is None
    assert defaults.warnings == []


def test_empty_yaml_is_loaded_but_empty(tmp_path: Path) -> None:
    _write_project(tmp_path, "")
    defaults = load_project_defaults(cwd=str(tmp_path))
    assert defaults.loaded is True
    assert defaults.provider_name is None


def test_malformed_yaml_warns_and_does_not_raise(tmp_path: Path) -> None:
    _write_project(tmp_path, "auth: [unterminated\n")
    defaults = load_project_defaults(cwd=str(tmp_path))
    assert defaults.loaded is True
    assert any("Could not parse" in w for w in defaults.warnings)


def test_non_mapping_top_level_is_ignored_with_warning(tmp_path: Path) -> None:
    _write_project(tmp_path, "- a\n- b\n")
    defaults = load_project_defaults(cwd=str(tmp_path))
    assert defaults.loaded is True
    assert defaults.provider_name is None
    assert any("must be a mapping" in w for w in defaults.warnings)


# ---- publicEndpoint derivations -------------------------------------------

def test_public_endpoint_derives_audience_and_url(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        """
publicEndpoint:
  host: mcp.example.com
  scheme: https
  path: /
  mcpPath: /mcp
auth:
  type: keycloak
  issuer: https://kc.example.com/realms/r
""",
    )
    d = load_project_defaults(cwd=str(tmp_path))
    assert d.audience == "https://mcp.example.com/mcp"
    assert d.public_url == "https://mcp.example.com"
    assert d.ingress_host == "mcp.example.com"
    assert d.ingress_tls_enabled is True
    assert d.ingress_path == "/"


def test_explicit_audience_overrides_derived(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        """
publicEndpoint:
  host: mcp.example.com
  scheme: https
  mcpPath: /mcp
auth:
  type: keycloak
  issuer: https://kc.example.com/realms/r
  audience: https://different.example.com/api
""",
    )
    d = load_project_defaults(cwd=str(tmp_path))
    assert d.audience == "https://different.example.com/api"


def test_http_scheme_disables_tls(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        """
publicEndpoint:
  host: mcp.local
  scheme: http
  mcpPath: /mcp
""",
    )
    d = load_project_defaults(cwd=str(tmp_path))
    assert d.ingress_tls_enabled is False
    assert d.public_url == "http://mcp.local"


def test_subpath_publicUrl_omits_trailing_slash(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        """
publicEndpoint:
  host: mcp.example.com
  scheme: https
  path: /api/
  mcpPath: /mcp
""",
    )
    d = load_project_defaults(cwd=str(tmp_path))
    assert d.public_url == "https://mcp.example.com/api"


# ---- auth.type → provider_name --------------------------------------------

def test_auth_type_keycloak_maps_to_keycloak(tmp_path: Path) -> None:
    _write_project(tmp_path, "auth:\n  type: keycloak\n  issuer: https://x\n")
    d = load_project_defaults(cwd=str(tmp_path))
    assert d.provider_name == "keycloak"


def test_auth_type_auth0_maps_to_auth0(tmp_path: Path) -> None:
    _write_project(tmp_path, "auth:\n  type: auth0\n")
    d = load_project_defaults(cwd=str(tmp_path))
    assert d.provider_name == "auth0"


def test_auth_type_oidc_with_providerName(tmp_path: Path) -> None:
    _write_project(
        tmp_path, "auth:\n  type: oidc\n  providerName: dex\n  issuer: https://x\n"
    )
    d = load_project_defaults(cwd=str(tmp_path))
    assert d.provider_name == "dex"


def test_auth_type_oidc_without_providerName_defaults_to_generic(tmp_path: Path) -> None:
    _write_project(tmp_path, "auth:\n  type: oidc\n  issuer: https://x\n")
    d = load_project_defaults(cwd=str(tmp_path))
    assert d.provider_name == "generic"


def test_auth_type_oidc_with_unknown_providerName_falls_back_to_generic(
    tmp_path: Path,
) -> None:
    _write_project(
        tmp_path, "auth:\n  type: oidc\n  providerName: weird\n  issuer: https://x\n"
    )
    d = load_project_defaults(cwd=str(tmp_path))
    assert d.provider_name == "generic"


# ---- requiredScopes --------------------------------------------------------

def test_required_scopes_passed_verbatim(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        """
auth:
  type: keycloak
  issuer: https://x
  requiredScopes:
    - mcp-scope
    - extra
""",
    )
    d = load_project_defaults(cwd=str(tmp_path))
    # openid is NOT auto-injected — verbatim.
    assert d.required_scopes == ["mcp-scope", "extra"]


def test_non_list_required_scopes_warns(tmp_path: Path) -> None:
    _write_project(
        tmp_path, 'auth:\n  type: keycloak\n  issuer: https://x\n  requiredScopes: "not-a-list"\n'
    )
    d = load_project_defaults(cwd=str(tmp_path))
    assert d.required_scopes is None
    assert any("requiredScopes" in w for w in d.warnings)


# ---- auth.auth0 ------------------------------------------------------------

def test_auth0_block_picked_up(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        """
auth:
  type: auth0
  audience: https://mcp.example.com/mcp
  auth0:
    domain: example.auth0.com
    apiIdentifier: https://mcp.example.com/mcp
""",
    )
    d = load_project_defaults(cwd=str(tmp_path))
    assert d.auth0_domain == "example.auth0.com"
    assert d.auth0_api_identifier == "https://mcp.example.com/mcp"


# ---- resolve() precedence --------------------------------------------------

def test_resolve_prefers_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_VAR", "from-env")
    assert resolve("from-cli", "MY_VAR", "from-project", "from-saved") == "from-cli"


def test_resolve_uses_env_when_cli_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_VAR", "from-env")
    assert resolve(None, "MY_VAR", "from-project", "from-saved") == "from-env"


def test_resolve_uses_project_when_cli_and_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MY_VAR", raising=False)
    assert resolve(None, "MY_VAR", "from-project", "from-saved") == "from-project"


def test_resolve_falls_back_to_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_VAR", raising=False)
    assert resolve(None, "MY_VAR", None, "from-saved") == "from-saved"


def test_resolve_returns_none_when_all_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_VAR", raising=False)
    assert resolve(None, "MY_VAR", None, None) is None
