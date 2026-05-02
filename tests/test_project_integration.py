"""Integration tests: mcp-project.yaml drives oidc-values.yaml output."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from mcp_base.setup_generic import GenericOIDCSetup


def _mock_response(json_body: Any) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = json_body
    return resp


def test_helm_values_override_ingress_and_public_url_from_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project overrides should land in oidc-values.yaml verbatim."""
    monkeypatch.chdir(tmp_path)
    issuer = "https://kc.example.com/realms/r"
    monkeypatch.setattr(
        "mcp_base.setup_generic.requests.get",
        lambda *a, **kw: _mock_response({
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
            "token_endpoint": f"{issuer}/protocol/openid-connect/token",
            "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
        }),
    )

    setup = GenericOIDCSetup(config_file=str(tmp_path / "oidc-config.json"))
    setup.setup(
        issuer=issuer,
        audience="https://mcp.example.com/mcp",
        client_id=None,
        client_secret=None,
        provider_name="keycloak",
        validate=True,
        required_scopes=["mcp-scope"],          # verbatim — no openid injection
        ingress_host="mcp.example.com",
        ingress_path="/api",
        ingress_tls_enabled=False,              # http
        public_url="http://mcp.example.com",
    )

    yaml_text = (tmp_path / "oidc-values.yaml").read_text()

    # Pattern B markers preserved
    assert 'authType: "keycloak"' in yaml_text
    assert "redis:\n  enabled: false" in yaml_text
    assert "jwt:\n  enabled: false" in yaml_text

    # Project overrides applied
    assert 'publicUrl: "http://mcp.example.com"' in yaml_text
    assert "host: mcp.example.com" in yaml_text
    assert "path: /api" in yaml_text
    # tls under ingress: enabled MUST be false because scheme=http
    assert "  tls:\n    enabled: false" in yaml_text

    # requiredScopes verbatim — openid NOT auto-injected
    assert 'requiredScopes: ["mcp-scope"]' in yaml_text
    assert '"openid"' not in yaml_text


def test_helm_values_pattern_a_emits_required_scopes_when_project_supplies_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pattern A doesn't emit requiredScopes by default; project file forces it."""
    monkeypatch.chdir(tmp_path)
    issuer = "https://idp.example.com"
    monkeypatch.setattr(
        "mcp_base.setup_generic.requests.get",
        lambda *a, **kw: _mock_response({
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/auth",
            "token_endpoint": f"{issuer}/token",
            "jwks_uri": f"{issuer}/jwks",
        }),
    )

    setup = GenericOIDCSetup(config_file=str(tmp_path / "oidc-config.json"))
    setup.setup(
        issuer=issuer,
        audience="https://mcp.example.com/mcp",
        client_id="cid",
        client_secret="csecret",
        provider_name="dex",
        validate=True,
        required_scopes=["mcp-scope", "profile"],
    )

    yaml_text = (tmp_path / "oidc-values.yaml").read_text()
    assert 'authType: "oidc"' in yaml_text
    assert 'requiredScopes: ["mcp-scope", "profile"]' in yaml_text
    # Pattern A still expects redis/jwt true
    assert "redis:\n  enabled: true" in yaml_text
    assert "jwt:\n  enabled: true" in yaml_text


def test_helm_values_pattern_a_omits_required_scopes_without_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backward compat: no project file → no requiredScopes block in Pattern A."""
    monkeypatch.chdir(tmp_path)
    issuer = "https://idp.example.com"

    def _boom(*a: Any, **kw: Any) -> Any:
        raise requests.ConnectionError("test")

    monkeypatch.setattr("mcp_base.setup_generic.requests.get", _boom)

    setup = GenericOIDCSetup(config_file=str(tmp_path / "oidc-config.json"))
    setup.setup(
        issuer=issuer,
        audience="https://mcp.example.com/mcp",
        client_id="cid",
        client_secret="csecret",
        provider_name="dex",
        validate=False,
    )

    yaml_text = (tmp_path / "oidc-values.yaml").read_text()
    assert "requiredScopes:" not in yaml_text
