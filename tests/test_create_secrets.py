"""Tests for create_secrets: Pattern B (Keycloak native DCR) no-op behavior."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def test_create_secrets_is_noop_for_pattern_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """For pattern=remote, create-secrets must exit 0 without touching the cluster."""
    config_file = tmp_path / "oidc-config.json"
    config_file.write_text(json.dumps({
        "provider": "keycloak",
        "pattern": "remote",
        "issuer": "https://kc.example.com/realms/r",
        "audience": "https://mcp.example.com/mcp",
    }))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "mcp-base create-secrets",
        "--release-name", "my-release",
        "--namespace", "mcp",
    ])

    # If this no-op path ever constructs KubernetesSecretCreator, it will try
    # to load kubeconfig and either succeed (bad for this test) or crash. Stub
    # it out so we detect the violation deterministically.
    def _boom(*a: object, **kw: object) -> None:
        raise AssertionError("Pattern B must not instantiate KubernetesSecretCreator")

    monkeypatch.setattr("mcp_base.create_secrets.KubernetesSecretCreator", _boom)

    from mcp_base import create_secrets

    with pytest.raises(SystemExit) as exc_info:
        create_secrets.main()
    assert exc_info.value.code == 0

    out = capsys.readouterr().out
    assert "Pattern B" in out
    assert "no Kubernetes secrets" in out.lower() or "no kubernetes secrets" in out.lower()


def test_create_secrets_infers_pattern_from_provider_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Older configs without a pattern field: infer remote from provider=keycloak."""
    config_file = tmp_path / "oidc-config.json"
    config_file.write_text(json.dumps({
        "provider": "keycloak",
        # pattern field deliberately absent (pre-contract config)
        "issuer": "https://kc.example.com/realms/r",
        "audience": "https://mcp.example.com/mcp",
    }))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "mcp-base create-secrets",
        "--release-name", "my-release",
    ])
    monkeypatch.setattr(
        "mcp_base.create_secrets.KubernetesSecretCreator",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("must not construct k8s client for inferred Pattern B")
        ),
    )

    from mcp_base import create_secrets

    with pytest.raises(SystemExit) as exc_info:
        create_secrets.main()
    assert exc_info.value.code == 0
    assert "Pattern B" in capsys.readouterr().out
