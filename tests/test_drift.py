"""Tests for mcp_base.drift — reconciling mcp-project.yaml from saved CLI artifacts."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict

import pytest

from mcp_base.drift import (
    ArgDrift,
    Proposal,
    ValuesProposal,
    compute_proposals,
    compute_values_drift,
    detect_arg_drift,
    reconcile_args,
    reconcile_project,
)
from mcp_base.project_config import ProjectDefaults
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


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
    # "2" picks the saved value (the second column) as the winner.
    monkeypatch.setattr("sys.stdin", io.StringIO("2\n"))
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
    # "1" keeps mcp-project.yaml as-is (first column wins).
    monkeypatch.setattr("sys.stdin", io.StringIO("1\n"))
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


# ---- compute_values_drift: project → oidc-values --------------------------

def _doc(yaml_text: str) -> CommentedMap:
    """Helper: parse a small YAML string into a CommentedMap."""
    yaml = YAML()
    return yaml.load(yaml_text) or CommentedMap()


def test_image_drift_against_placeholder_is_auto() -> None:
    """Project says wateim/foo, values has the placeholder → auto-apply."""
    project = ProjectDefaults(
        image_repository="wateim/mcp-base-server",
        image_tag="latest",
        source_path="mcp-project.yaml",
    )
    values = {
        "image": {
            "repository": "your-registry.example.com/mcp-server",
            "tag": "",
        }
    }
    drift = compute_values_drift(project, values, _doc("project:\n  name: x\n"))
    by_path = {tuple(d.path): d for d in drift}
    assert by_path[("image", "repository")].kind == "auto"
    assert by_path[("image", "repository")].proposed == "wateim/mcp-base-server"
    assert by_path[("image", "tag")].kind == "auto"


def test_image_drift_against_real_value_is_conflict() -> None:
    """Project says wateim/foo, values has someone-else/bar → conflict (prompt)."""
    project = ProjectDefaults(
        image_repository="wateim/mcp-base-server",
        image_tag="v1.0.0",
        source_path="mcp-project.yaml",
    )
    values = {
        "image": {
            "repository": "different-registry/another-image",
            "tag": "v0.9.0",
        }
    }
    drift = compute_values_drift(project, values, _doc("project:\n  name: x\n"))
    kinds = {tuple(d.path): d.kind for d in drift}
    assert kinds[("image", "repository")] == "conflict"
    assert kinds[("image", "tag")] == "conflict"


def test_image_drift_match_emits_no_entry() -> None:
    project = ProjectDefaults(
        image_repository="wateim/mcp-base-server",
        image_tag="latest",
        source_path="mcp-project.yaml",
    )
    values = {
        "image": {"repository": "wateim/mcp-base-server", "tag": "latest"}
    }
    drift = compute_values_drift(project, values, _doc("project:\n  name: x\n"))
    assert drift == []


def test_ingress_drift_skipped_when_publicEndpoint_absent() -> None:
    """Project has no publicEndpoint block → don't second-guess values ingress."""
    project = ProjectDefaults(
        ingress_host="mcp.example.com",
        ingress_tls_enabled=True,
        public_url="https://mcp.example.com",
        source_path="mcp-project.yaml",
    )
    values = {"ingress": {"host": "different.example.com"}}
    drift = compute_values_drift(project, values, _doc("project:\n  name: x\n"))
    paths = [tuple(d.path) for d in drift]
    assert ("ingress", "host") not in paths


def test_ingress_drift_emitted_when_publicEndpoint_present() -> None:
    project = ProjectDefaults(
        ingress_host="mcp.example.com",
        ingress_path="/",
        ingress_tls_enabled=True,
        public_url="https://mcp.example.com",
        source_path="mcp-project.yaml",
    )
    values = {
        "ingress": {
            "host": "mcp-api.example.com",  # placeholder
            "tls": {"enabled": True},
            "path": "/",
        },
        "oidc": {"publicUrl": ""},
    }
    project_doc = _doc("publicEndpoint:\n  host: mcp.example.com\n")
    drift = compute_values_drift(project, values, project_doc)
    by_path = {tuple(d.path): d for d in drift}
    assert by_path[("ingress", "host")].kind == "auto"
    assert by_path[("oidc", "publicUrl")].kind == "auto"


# ---- reconcile_project: values-side drift ---------------------------------

def test_reconcile_fix_applies_image_drift_to_oidc_values(tmp_path: Path) -> None:
    """--fix should patch oidc-values.yaml when image differs and values is placeholder."""
    project = _write(
        tmp_path,
        "mcp-project.yaml",
        "build:\n"
        "  registry: wateim\n"
        "  imageName: mcp-base-server\n"
        "  tag: latest\n"
        "auth:\n"
        "  type: keycloak\n"
        "  issuer: https://kc.example.com/realms/r\n"
        "  audience: https://mcp.example.com/mcp\n",
    )
    values = _write(
        tmp_path,
        "oidc-values.yaml",
        "image:\n"
        "  repository: your-registry.example.com/mcp-server  # placeholder\n"
        "  tag: \"\"\n"
        "oidc:\n"
        "  authType: keycloak\n",
    )
    reconcile_project(
        str(project),
        {
            "provider": "keycloak",
            "issuer": "https://kc.example.com/realms/r",
            "audience": "https://mcp.example.com/mcp",
        },
        oidc_values_path=str(values),
        fix=True,
    )
    text = values.read_text()
    assert "repository: wateim/mcp-base-server" in text
    assert 'tag: latest' in text or "tag: 'latest'" in text or 'tag: "latest"' in text
    # Comment must survive the round-trip
    assert "# placeholder" in text


def test_reconcile_fix_image_conflict_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When values has a real (non-placeholder) image, --fix must prompt."""
    project = _write(
        tmp_path,
        "mcp-project.yaml",
        "build:\n"
        "  registry: wateim\n"
        "  imageName: mcp-base-server\n"
        "  tag: v2.0.0\n",
    )
    values = _write(
        tmp_path,
        "oidc-values.yaml",
        "image:\n"
        "  repository: someone-else/image\n"
        "  tag: v1.0.0\n",
    )
    # 'a' = apply all conflicts
    monkeypatch.setattr("sys.stdin", io.StringIO("a\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    reconcile_project(
        str(project),
        {
            "provider": "keycloak",
            "issuer": "https://kc.example.com/realms/r",
            "audience": "https://mcp.example.com/mcp",
        },
        oidc_values_path=str(values),
        fix=True,
    )
    text = values.read_text()
    assert "wateim/mcp-base-server" in text
    assert "someone-else/image" not in text


def test_test_sidecar_drift_auto_when_block_absent() -> None:
    """Project says testSidecar enabled; values has no testSidecar block → auto."""
    project = ProjectDefaults(
        test_sidecar_enabled=True,
        source_path="mcp-project.yaml",
    )
    values: Dict[str, Any] = {}  # no testSidecar block at all
    drift = compute_values_drift(project, values, _doc("project:\n  name: x\n"))
    sidecar = next(d for d in drift if d.path == ["testSidecar", "enabled"])
    assert sidecar.kind == "auto"
    assert sidecar.proposed is True


def test_test_sidecar_drift_conflict_when_disagrees() -> None:
    project = ProjectDefaults(
        test_sidecar_enabled=True,
        source_path="mcp-project.yaml",
    )
    values = {"testSidecar": {"enabled": False}}
    drift = compute_values_drift(project, values, _doc("project:\n  name: x\n"))
    sidecar = next(d for d in drift if d.path == ["testSidecar", "enabled"])
    assert sidecar.kind == "conflict"


def test_service_type_drift_against_default() -> None:
    project = ProjectDefaults(service_type="NodePort", source_path="mcp-project.yaml")
    values = {"service": {"type": "ClusterIP"}}
    drift = compute_values_drift(project, values, _doc("project:\n  name: x\n"))
    svc = next(d for d in drift if d.path == ["service", "type"])
    assert svc.kind == "conflict"
    assert svc.proposed == "NodePort"


def test_reconcile_fix_applies_test_sidecar_drift(tmp_path: Path) -> None:
    project = _write(
        tmp_path,
        "mcp-project.yaml",
        "deployment:\n  testSidecarEnabled: true\n",
    )
    values = _write(
        tmp_path,
        "oidc-values.yaml",
        "image:\n  repository: your-registry.example.com/mcp-server\n  tag: \"\"\n"
        "service:\n  type: ClusterIP\n",
    )
    reconcile_project(
        str(project),
        {
            "provider": "keycloak",
            "issuer": "https://kc.example.com/realms/r",
            "audience": "https://mcp.example.com/mcp",
        },
        oidc_values_path=str(values),
        fix=True,
    )
    text = values.read_text()
    assert "testSidecar:" in text
    assert "enabled: true" in text


# ---- args drift -----------------------------------------------------------

def test_detect_arg_drift_namespace() -> None:
    project = ProjectDefaults(
        deployment_namespace="claude", source_path="mcp-project.yaml"
    )
    drift = detect_arg_drift(project, namespace="other-ns")
    assert len(drift) == 1
    assert drift[0].flag == "--namespace"
    assert drift[0].cli_value == "other-ns"
    assert drift[0].project_value == "claude"


def test_detect_arg_drift_match_emits_nothing() -> None:
    project = ProjectDefaults(
        deployment_namespace="claude",
        deployment_release_name="mcp-base",
        project_app_name="mcp-base",
        source_path="mcp-project.yaml",
    )
    assert detect_arg_drift(
        project, namespace="claude", release_name="mcp-base", app_name="mcp-base"
    ) == []


def test_detect_arg_drift_silent_project_field_skipped() -> None:
    """If project doesn't define a field, no drift even when CLI passes one."""
    project = ProjectDefaults(source_path="mcp-project.yaml")
    assert detect_arg_drift(project, namespace="some-ns", app_name="some-app") == []


def test_reconcile_args_no_drift_returns_inputs() -> None:
    project = ProjectDefaults(
        deployment_namespace="claude", source_path="mcp-project.yaml"
    )
    ns, rel, app = reconcile_args(
        project, namespace="claude", release_name=None, app_name=None, fix=False
    )
    assert (ns, rel, app) == ("claude", None, None)


def test_reconcile_args_print_only_keeps_cli(
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = ProjectDefaults(
        deployment_namespace="claude", source_path="mcp-project.yaml"
    )
    ns, _rel, _app = reconcile_args(
        project, namespace="other-ns", release_name=None, app_name=None, fix=False
    )
    out = capsys.readouterr().out
    assert "CLI args vs mcp-project.yaml" in out
    assert "deployment.namespace" in out
    # Without --fix, the CLI value is kept.
    assert ns == "other-ns"


def test_reconcile_args_fix_picks_project_via_choice_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectDefaults(
        deployment_namespace="claude", source_path="mcp-project.yaml"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("2\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    ns, _rel, _app = reconcile_args(
        project, namespace="other-ns", release_name=None, app_name=None, fix=True
    )
    assert ns == "claude"


def test_reconcile_args_fix_keeps_cli_via_choice_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectDefaults(
        deployment_namespace="claude", source_path="mcp-project.yaml"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("1\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    ns, _rel, _app = reconcile_args(
        project, namespace="other-ns", release_name=None, app_name=None, fix=True
    )
    assert ns == "other-ns"


def test_reconcile_args_fix_non_tty_keeps_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = ProjectDefaults(
        deployment_namespace="claude", source_path="mcp-project.yaml"
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    ns, _rel, _app = reconcile_args(
        project, namespace="other-ns", release_name=None, app_name=None, fix=True
    )
    out = capsys.readouterr().out
    assert "not a TTY" in out
    assert ns == "other-ns"


def test_reconcile_args_fix_apply_all_uses_project_for_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectDefaults(
        deployment_namespace="claude",
        deployment_release_name="mcp-base",
        project_app_name="mcp-base",
        source_path="mcp-project.yaml",
    )
    # First answer 'a' applies project for all remaining drift entries.
    monkeypatch.setattr("sys.stdin", io.StringIO("a\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    ns, rel, app = reconcile_args(
        project,
        namespace="ns-cli", release_name="rel-cli", app_name="app-cli",
        fix=True,
    )
    assert ns == "claude"
    assert rel == "mcp-base"
    assert app == "mcp-base"


def test_reconcile_print_only_does_not_touch_oidc_values(tmp_path: Path) -> None:
    project = _write(
        tmp_path,
        "mcp-project.yaml",
        "build:\n"
        "  registry: wateim\n"
        "  imageName: mcp-base-server\n",
    )
    values = _write(
        tmp_path,
        "oidc-values.yaml",
        "image:\n  repository: your-registry.example.com/mcp-server\n  tag: \"\"\n",
    )
    before = values.read_text()
    reconcile_project(
        str(project),
        {
            "provider": "keycloak",
            "issuer": "https://kc.example.com/realms/r",
            "audience": "https://mcp.example.com/mcp",
        },
        oidc_values_path=str(values),
        fix=False,
    )
    assert values.read_text() == before
