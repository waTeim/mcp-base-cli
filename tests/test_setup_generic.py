"""Tests for setup_generic: OIDC discovery and provider-specific fallbacks."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest
import requests

from mcp_base.setup_generic import (
    GenericOIDCSetup,
    fallback_endpoints,
    pattern_for_provider,
)


# ---- fallback_endpoints ----------------------------------------------------

def test_fallback_keycloak_uses_realm_protocol_paths() -> None:
    issuer = "https://kc.example.com/realms/myrealm"
    endpoints = fallback_endpoints(issuer, "keycloak")
    assert endpoints == {
        "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
        "token_endpoint": f"{issuer}/protocol/openid-connect/token",
        "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
    }


def test_fallback_okta_uses_v1_paths() -> None:
    issuer = "https://example.okta.com"
    endpoints = fallback_endpoints(issuer, "okta")
    assert endpoints["authorization_endpoint"] == f"{issuer}/v1/authorize"
    assert endpoints["token_endpoint"] == f"{issuer}/v1/token"
    assert endpoints["jwks_uri"] == f"{issuer}/v1/keys"


@pytest.mark.parametrize("provider", ["dex", "generic", "unknown"])
def test_fallback_default_is_dex_style(provider: str) -> None:
    issuer = "https://dex.example.com"
    endpoints = fallback_endpoints(issuer, provider)
    assert endpoints["authorization_endpoint"] == f"{issuer}/auth"
    assert endpoints["token_endpoint"] == f"{issuer}/token"
    assert endpoints["jwks_uri"] == f"{issuer}/.well-known/jwks.json"


def test_fallback_strips_trailing_slash() -> None:
    endpoints = fallback_endpoints("https://kc.example.com/realms/r/", "keycloak")
    assert endpoints["token_endpoint"] == (
        "https://kc.example.com/realms/r/protocol/openid-connect/token"
    )


# ---- validate_issuer -------------------------------------------------------

def _mock_response(json_body: Any = None, status: int = 200, raise_json: bool = False) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    if raise_json:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = json_body
    resp.status_code = status
    return resp


def test_validate_issuer_returns_discovery_doc_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    discovery = {
        "issuer": "https://kc.example.com/realms/r",
        "authorization_endpoint": "https://kc.example.com/realms/r/protocol/openid-connect/auth",
        "token_endpoint": "https://kc.example.com/realms/r/protocol/openid-connect/token",
        "jwks_uri": "https://kc.example.com/realms/r/protocol/openid-connect/certs",
    }
    monkeypatch.setattr(
        "mcp_base.setup_generic.requests.get",
        lambda *a, **kw: _mock_response(discovery),
    )
    setup = GenericOIDCSetup(config_file=str(tmp_path / "oidc-config.json"))
    result = setup.validate_issuer("https://kc.example.com/realms/r")
    assert result == discovery


def test_validate_issuer_returns_none_when_fields_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "mcp_base.setup_generic.requests.get",
        lambda *a, **kw: _mock_response({"issuer": "https://x"}),  # missing the rest
    )
    setup = GenericOIDCSetup(config_file=str(tmp_path / "oidc-config.json"))
    assert setup.validate_issuer("https://x") is None


def test_validate_issuer_returns_none_on_network_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    def _boom(*a: Any, **kw: Any) -> Any:
        raise requests.ConnectionError("unreachable")

    monkeypatch.setattr("mcp_base.setup_generic.requests.get", _boom)
    setup = GenericOIDCSetup(config_file=str(tmp_path / "oidc-config.json"))
    assert setup.validate_issuer("https://nope.example.com") is None


def test_validate_issuer_returns_none_on_malformed_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "mcp_base.setup_generic.requests.get",
        lambda *a, **kw: _mock_response(raise_json=True),
    )
    setup = GenericOIDCSetup(config_file=str(tmp_path / "oidc-config.json"))
    assert setup.validate_issuer("https://x") is None


# ---- setup() endpoint selection --------------------------------------------

def _run_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_name: str,
    issuer: str,
    *,
    discovery: Dict[str, Any] | None,
    validate: bool,
) -> Dict[str, Any]:
    """Run setup and return the saved config dict."""
    monkeypatch.chdir(tmp_path)

    if discovery is None:
        def _boom(*a: Any, **kw: Any) -> Any:
            raise requests.ConnectionError("no network in test")
        monkeypatch.setattr("mcp_base.setup_generic.requests.get", _boom)
    else:
        monkeypatch.setattr(
            "mcp_base.setup_generic.requests.get",
            lambda *a, **kw: _mock_response(discovery),
        )

    config_file = tmp_path / "oidc-config.json"
    setup = GenericOIDCSetup(config_file=str(config_file))
    setup.setup(
        issuer=issuer,
        audience="https://mcp.example.com/mcp",
        client_id="cid",
        client_secret="csecret",
        provider_name=provider_name,
        validate=validate,
    )
    with config_file.open() as f:
        return json.load(f)  # type: ignore[no-any-return]


def test_setup_uses_discovery_endpoints_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    issuer = "https://kc.example.com/realms/r"
    discovery = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
        "token_endpoint": f"{issuer}/protocol/openid-connect/token",
        "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
    }
    cfg = _run_setup(
        tmp_path, monkeypatch, "keycloak", issuer, discovery=discovery, validate=True
    )
    assert cfg["authorization_endpoint"] == discovery["authorization_endpoint"]
    assert cfg["token_endpoint"] == discovery["token_endpoint"]
    assert cfg["jwks_uri"] == discovery["jwks_uri"]


def test_setup_falls_back_to_keycloak_paths_when_validation_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    issuer = "https://kc.example.com/realms/r"
    cfg = _run_setup(
        tmp_path, monkeypatch, "keycloak", issuer, discovery=None, validate=False
    )
    assert cfg["authorization_endpoint"] == f"{issuer}/protocol/openid-connect/auth"
    assert cfg["token_endpoint"] == f"{issuer}/protocol/openid-connect/token"
    assert cfg["jwks_uri"] == f"{issuer}/protocol/openid-connect/certs"


def test_setup_falls_back_to_dex_paths_when_discovery_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    issuer = "https://dex.example.com"
    cfg = _run_setup(
        tmp_path, monkeypatch, "dex", issuer, discovery=None, validate=True
    )
    assert cfg["authorization_endpoint"] == f"{issuer}/auth"
    assert cfg["token_endpoint"] == f"{issuer}/token"
    assert cfg["jwks_uri"] == f"{issuer}/.well-known/jwks.json"


def test_setup_discovery_overrides_provider_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the user mislabels a Keycloak realm as 'dex', discovery still wins."""
    issuer = "https://kc.example.com/realms/r"
    discovery = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
        "token_endpoint": f"{issuer}/protocol/openid-connect/token",
        "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
    }
    cfg = _run_setup(
        tmp_path, monkeypatch, "dex", issuer, discovery=discovery, validate=True
    )
    assert cfg["token_endpoint"] == f"{issuer}/protocol/openid-connect/token"


# ---- pattern_for_provider --------------------------------------------------

def test_pattern_for_provider_keycloak_is_remote() -> None:
    assert pattern_for_provider("keycloak") == "remote"


@pytest.mark.parametrize("provider", ["auth0", "dex", "okta", "generic", "unknown"])
def test_pattern_for_provider_non_keycloak_is_proxy(provider: str) -> None:
    assert pattern_for_provider(provider) == "proxy"


# ---- Pattern A vs B in oidc-config.json ------------------------------------

def test_setup_keycloak_writes_remote_pattern_without_server_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pattern B (Keycloak): pattern field is 'remote', server_client omitted."""
    issuer = "https://kc.example.com/realms/r"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "mcp_base.setup_generic.requests.get",
        lambda *a, **kw: _mock_response({
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
            "token_endpoint": f"{issuer}/protocol/openid-connect/token",
            "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
        }),
    )

    config_file = tmp_path / "oidc-config.json"
    setup = GenericOIDCSetup(config_file=str(config_file))
    # Pass credentials to confirm they're ignored and a warning is printed.
    setup.setup(
        issuer=issuer,
        audience="https://mcp.example.com/mcp",
        client_id="should-be-dropped",
        client_secret="also-dropped",
        provider_name="keycloak",
        validate=True,
    )

    with config_file.open() as f:
        cfg = json.load(f)

    assert cfg["pattern"] == "remote"
    assert cfg["provider"] == "keycloak"
    assert "server_client" not in cfg, "Pattern B must not persist server_client"

    out = capsys.readouterr().out
    assert "ignored for Keycloak" in out
    assert "should-be-dropped" not in out  # secret must never be echoed


@pytest.mark.parametrize("provider", ["dex", "okta", "generic"])
def test_setup_pattern_a_writes_proxy_pattern_and_server_client(
    provider: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _run_setup(
        tmp_path, monkeypatch, provider, "https://idp.example.com",
        discovery=None, validate=False,
    )
    assert cfg["pattern"] == "proxy"
    assert cfg["server_client"] == {"client_id": "cid", "client_secret": "csecret"}


# ---- Helm values file shape ------------------------------------------------

def _run_setup_and_read_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_name: str,
    issuer: str,
    *,
    client_id: str | None,
    client_secret: str | None,
) -> str:
    monkeypatch.chdir(tmp_path)
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
        client_id=client_id,
        client_secret=client_secret,
        provider_name=provider_name,
        validate=True,
    )
    return (tmp_path / "oidc-values.yaml").read_text()


def test_helm_values_keycloak_sets_pattern_b_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    yaml_text = _run_setup_and_read_values(
        tmp_path, monkeypatch, "keycloak", "https://kc.example.com/realms/r",
        client_id=None, client_secret=None,
    )
    assert 'authType: "keycloak"' in yaml_text
    # Pattern B: Redis and JWT MUST be disabled.
    assert "redis:\n  enabled: false" in yaml_text
    assert "jwt:\n  enabled: false" in yaml_text
    # Pattern B MUST NOT persist a clientId into values.
    assert "clientId:" not in yaml_text
    # mcp-scope must be included so the audience mapper fires during DCR OAuth.
    assert '"mcp-scope"' in yaml_text


@pytest.mark.parametrize("provider", ["dex", "okta", "generic"])
def test_helm_values_pattern_a_sets_proxy_flags(
    provider: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    yaml_text = _run_setup_and_read_values(
        tmp_path, monkeypatch, provider, "https://idp.example.com",
        client_id="the-id", client_secret="the-secret",
    )
    assert 'authType: "oidc"' in yaml_text
    assert "redis:\n  enabled: true" in yaml_text
    assert "jwt:\n  enabled: true" in yaml_text
    assert 'clientId: "the-id"' in yaml_text
    # Secret must never appear in values.yaml.
    assert "the-secret" not in yaml_text
