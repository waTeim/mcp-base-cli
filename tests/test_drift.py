"""Tests for mcp_base.drift — reconciling mcp-project.yaml from saved CLI artifacts."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict

import pytest

from mcp_base.drift import (
    Proposal,
    compute_proposals,
    reconcile_project,
)


# ---- compute_proposals -----------------------------------------------------

def test_keycloak_provider_yields_auth_type_keycloak() -> None:
    saved = {
        "provider": "keycloak",
        "issuer": "https://kc.example.com/realms/r",
        "audience": "https://mcp.example.com/mcp",
    }
    proposals = compute_proposals(saved, {})
    paths = [p.path for p in proposals]
    assert ["auth", "type"] in paths
    type_p = next(p for p in proposals if p.path == ["auth", "type"])
    assert type_p.proposed == "keycloak"
    # No providerName for keycloak (it's only for type=oidc)
    assert ["auth", "providerName"] not in paths


def test_dex_provider_maps_to_oidc_with_providerName() -> None:
    proposals = compute_proposals({"provider": "dex", "issuer": "https://x"}, {})
    type_p = next(p for p in proposals if p.path == ["auth", "type"])
    name_p = next(p for p in proposals if p.path == ["auth", "providerName"])
    assert type_p.proposed == "oidc"
    assert name_p.proposed == "dex"


def test_auth0_provider_emits_auth0_block() -> None:
    saved = {
        "provider": "auth0",
        "domain": "example.auth0.com",
        "audience": "https://mcp.example.com/mcp",
    }
    proposals = compute_proposals(saved, {})
    paths = [p.path for p in proposals]
    assert ["auth", "auth0", "domain"] in paths
    assert ["auth", "auth0", "apiIdentifier"] in paths


def test_required_scopes_pulled_verbatim_from_oidc_values() -> None:
    saved = {"provider": "keycloak", "issuer": "https://x", "audience": "https://x/mcp"}
    values = {"oidc": {"requiredScopes": ["mcp-scope", "extra"]}}
    proposals = compute_proposals(saved, values)
    scopes = next(p for p in proposals if p.path == ["auth", "requiredScopes"])
    assert scopes.proposed == ["mcp-scope", "extra"]


def test_placeholder_ingress_host_is_skipped() -> None:
    """The example placeholder host must NOT pollute mcp-project.yaml."""
    saved = {"provider": "dex", "issuer": "https://x", "audience": "https://x/mcp"}
    values = {
        "ingress": {"host": "mcp-api.example.com", "tls": {"enabled": True}, "path": "/"}
    }
    proposals = compute_proposals(saved, values)
    paths = [p.path for p in proposals]
    assert ["publicEndpoint", "host"] not in paths


def test_real_ingress_host_yields_publicEndpoint_block() -> None:
    saved = {
        "provider": "keycloak",
        "issuer": "https://kc.example.com/realms/r",
        "audience": "https://mcp.example.com/mcp",
    }
    values = {
        "ingress": {"host": "mcp.example.com", "tls": {"enabled": True}, "path": "/"},
    }
    proposals = compute_proposals(saved, values)
    by_path = {tuple(p.path): p.proposed for p in proposals}
    assert by_path[("publicEndpoint", "host")] == "mcp.example.com"
    assert by_path[("publicEndpoint", "scheme")] == "https"
    assert by_path[("publicEndpoint", "path")] == "/"
    assert by_path[("publicEndpoint", "mcpPath")] == "/mcp"


def test_http_tls_disabled_maps_to_http_scheme() -> None:
    saved = {"provider": "dex", "issuer": "https://x", "audience": "https://m.local/mcp"}
    values = {"ingress": {"host": "m.local", "tls": {"enabled": False}}}
    proposals = compute_proposals(saved, values)
    by_path = {tuple(p.path): p.proposed for p in proposals}
    assert by_path[("publicEndpoint", "scheme")] == "http"


# ---- reconcile_project: file mutation --------------------------------------

def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


def test_reconcile_no_project_file_is_noop(tmp_path: Path) -> None:
    """No mcp-project.yaml → nothing to do, never raises."""
    reconcile_project(
        str(tmp_path / "mcp-project.yaml"),
        {"provider": "keycloak", "issuer": "https://x", "audience": "https://x/mcp"},
        oidc_values_path=str(tmp_path / "oidc-values.yaml"),
        fix=True,
    )


def test_reconcile_print_only_does_not_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _write(tmp_path, "mcp-project.yaml", "project:\n  name: mcp-base\n")
    before = project.read_text()

    reconcile_project(
        str(project),
        {"provider": "keycloak", "issuer": "https://kc.example.com/realms/r",
         "audience": "https://mcp.example.com/mcp"},
        oidc_values_path=str(tmp_path / "no-such-values.yaml"),
        fix=False,
    )

    out = capsys.readouterr().out
    assert "Missing in mcp-project.yaml" in out
    assert "auth.issuer" in out
    assert "re-run with --fix" in out
    assert project.read_text() == before  # untouched


def test_reconcile_fix_adds_missing_auth_block(tmp_path: Path) -> None:
    """Auto-add: with --fix, missing fields are written without prompting."""
    project = _write(
        tmp_path,
        "mcp-project.yaml",
        "# top-level comment\n"
        "project:\n  name: mcp-base   # name comment\n",
    )
    reconcile_project(
        str(project),
        {
            "provider": "keycloak",
            "issuer": "https://kc.example.com/realms/r",
            "audience": "https://mcp.example.com/mcp",
        },
        oidc_values_path=str(tmp_path / "no-values.yaml"),
        fix=True,
    )

    text = project.read_text()
    # Comments preserved
    assert "# top-level comment" in text
    assert "# name comment" in text
    # Auth block added
    assert "auth:" in text
    assert "type: keycloak" in text
    assert "issuer: https://kc.example.com/realms/r" in text
    assert "audience: https://mcp.example.com/mcp" in text


def test_reconcile_fix_adds_publicEndpoint_from_oidc_values(tmp_path: Path) -> None:
    project = _write(tmp_path, "mcp-project.yaml", "project:\n  name: mcp-base\n")
    _write(
        tmp_path,
        "oidc-values.yaml",
        "oidc:\n"
        "  authType: keycloak\n"
        "  requiredScopes:\n"
        "    - mcp-scope\n"
        "ingress:\n"
        "  host: mcp.example.com\n"
        "  path: /\n"
        "  tls:\n"
        "    enabled: true\n",
    )
    reconcile_project(
        str(project),
        {
            "provider": "keycloak",
            "issuer": "https://kc.example.com/realms/r",
            "audience": "https://mcp.example.com/mcp",
        },
        oidc_values_path=str(tmp_path / "oidc-values.yaml"),
        fix=True,
    )
    text = project.read_text()
    assert "publicEndpoint:" in text
    assert "host: mcp.example.com" in text
    assert "scheme: https" in text
    assert "mcpPath: /mcp" in text
    # Required scopes from oidc-values
    assert "requiredScopes:" in text
    assert "mcp-scope" in text


def test_reconcile_skips_existing_matching_field(tmp_path: Path) -> None:
    """A field already correct in project must NOT be flagged as drift."""
    project = _write(
        tmp_path,
        "mcp-project.yaml",
        "auth:\n"
        "  type: keycloak\n"
        "  issuer: https://kc.example.com/realms/r\n"
        "  audience: https://mcp.example.com/mcp\n",
    )
    before = project.read_text()
    reconcile_project(
        str(project),
        {
            "provider": "keycloak",
            "issuer": "https://kc.example.com/realms/r",
            "audience": "https://mcp.example.com/mcp",
        },
        oidc_values_path=str(tmp_path / "no-values.yaml"),
        fix=True,
    )
    # Nothing added, nothing changed
    assert project.read_text() == before


def test_reconcile_conflict_prompt_yes_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _write(
        tmp_path,
        "mcp-project.yaml",
        "auth:\n"
        "  type: keycloak\n"
        "  issuer: https://kc.OLD.example.com/realms/r\n"
        "  audience: https://mcp.example.com/mcp\n",
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    reconcile_project(
        str(project),
        {
            "provider": "keycloak",
            "issuer": "https://kc.NEW.example.com/realms/r",
            "audience": "https://mcp.example.com/mcp",
        },
        oidc_values_path=str(tmp_path / "no-values.yaml"),
        fix=True,
    )
    text = project.read_text()
    assert "kc.NEW.example.com" in text
    assert "kc.OLD.example.com" not in text


def test_reconcile_conflict_prompt_no_keeps_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _write(
        tmp_path,
        "mcp-project.yaml",
        "auth:\n"
        "  type: keycloak\n"
        "  issuer: https://kc.OLD.example.com/realms/r\n"
        "  audience: https://mcp.example.com/mcp\n",
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    reconcile_project(
        str(project),
        {
            "provider": "keycloak",
            "issuer": "https://kc.NEW.example.com/realms/r",
            "audience": "https://mcp.example.com/mcp",
        },
        oidc_values_path=str(tmp_path / "no-values.yaml"),
        fix=True,
    )
    text = project.read_text()
    assert "kc.OLD.example.com" in text


def test_reconcile_non_tty_with_fix_still_auto_adds_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Auto-add doesn't need a TTY; only conflict prompts do."""
    project = _write(tmp_path, "mcp-project.yaml", "project:\n  name: mcp-base\n")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    reconcile_project(
        str(project),
        {
            "provider": "keycloak",
            "issuer": "https://kc.example.com/realms/r",
            "audience": "https://mcp.example.com/mcp",
        },
        oidc_values_path=str(tmp_path / "no-values.yaml"),
        fix=True,
    )
    text = project.read_text()
    assert "auth:" in text
    assert "issuer: https://kc.example.com/realms/r" in text


def test_reconcile_fix_without_changes_is_noop(tmp_path: Path) -> None:
    project = _write(
        tmp_path,
        "mcp-project.yaml",
        "auth:\n"
        "  type: keycloak\n"
        "  issuer: https://kc.example.com/realms/r\n"
        "  audience: https://mcp.example.com/mcp\n",
    )
    before = project.read_text()
    reconcile_project(
        str(project),
        {
            "provider": "keycloak",
            "issuer": "https://kc.example.com/realms/r",
            "audience": "https://mcp.example.com/mcp",
        },
        oidc_values_path=str(tmp_path / "no-values.yaml"),
        fix=True,
    )
    assert project.read_text() == before
