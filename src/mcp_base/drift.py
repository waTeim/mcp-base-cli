"""
Reconcile ``mcp-project.yaml`` with the saved CLI artifacts (``oidc-config.json`` /
``auth0-config.json`` and ``oidc-values.yaml``).

`mcp-project.yaml` is intended to be the source of truth, but on existing
deployments the CLI artifacts came first. This module proposes additions to
`mcp-project.yaml` derived from those artifacts:

- without ``--fix``: print drift / missing-field summary, do not touch any file.
- with ``--fix``:
    * fields **missing** from `mcp-project.yaml` and present in saved → add automatically.
    * fields **present** in `mcp-project.yaml` but differing → prompt per-field
      (``y``/``n``/``a``/``s``); answers rewrite `mcp-project.yaml`.
- file is rewritten with ``ruamel.yaml`` so existing comments and key order survive.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from mcp_base.project_config import ProjectDefaults, load_project_defaults


PROJECT_FILE = "mcp-project.yaml"
OIDC_VALUES_FILE = "oidc-values.yaml"

# Placeholder defaults the CLI emits when no real value is known. We never
# propose these into mcp-project.yaml because they're not "real" config —
# they're examples in the generated file.
_PLACEHOLDER_INGRESS_HOSTS = {"mcp-api.example.com"}
_PLACEHOLDER_IMAGE_REPOS = {"your-registry.example.com/mcp-server"}


@dataclass
class Proposal:
    """One proposed addition / change to mcp-project.yaml."""

    path: List[str]                # ["auth", "issuer"], etc.
    proposed: Any                  # value derived from saved artifacts
    current: Any = None            # current value in mcp-project.yaml (None = absent)
    reason: str = ""               # short human-readable source

    @property
    def kind(self) -> str:
        if self.current is None:
            return "missing"
        if self._equal():
            return "match"
        return "conflict"

    def _equal(self) -> bool:
        if isinstance(self.proposed, list) and isinstance(self.current, list):
            return list(self.proposed) == list(self.current)
        return self.proposed == self.current


@dataclass
class ArgDrift:
    """A CLI argument that disagrees with the project file."""

    flag: str            # "--namespace"
    cli_value: Any
    project_value: Any
    project_field: str   # "deployment.namespace"


def detect_arg_drift(
    project: ProjectDefaults,
    *,
    namespace: Optional[str] = None,
    release_name: Optional[str] = None,
    app_name: Optional[str] = None,
) -> List[ArgDrift]:
    """Return arg/project disagreements. Caller decides what to do with them."""
    out: List[ArgDrift] = []
    if (
        namespace and project.deployment_namespace
        and namespace != project.deployment_namespace
    ):
        out.append(
            ArgDrift("--namespace", namespace, project.deployment_namespace,
                     "deployment.namespace")
        )
    if (
        release_name and project.deployment_release_name
        and release_name != project.deployment_release_name
    ):
        out.append(
            ArgDrift("--release-name", release_name,
                     project.deployment_release_name, "deployment.helmRelease")
        )
    if (
        app_name and project.project_app_name
        and app_name != project.project_app_name
    ):
        out.append(
            ArgDrift("--app-name", app_name, project.project_app_name,
                     "project.name")
        )
    return out


def resolve_arg(
    cli_value: Optional[str], project_value: Optional[str]
) -> Optional[str]:
    """CLI arg > project value. Used when the CLI flag is optional."""
    if cli_value:
        return cli_value
    return project_value


def reconcile_args(
    project: ProjectDefaults,
    *,
    namespace: Optional[str],
    release_name: Optional[str],
    app_name: Optional[str],
    fix: bool,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Compare CLI args to project, optionally prompt, return chosen values.

    Without ``--fix``: print warnings and keep the CLI args as-is.
    With ``--fix`` on a TTY: prompt per-disagreement; chosen winner is used
    for *this run only* (the CLI invocation isn't rewritten).
    Non-TTY ``--fix``: print warnings, keep CLI values.
    """
    drift = detect_arg_drift(
        project, namespace=namespace, release_name=release_name, app_name=app_name
    )
    if not drift:
        return namespace, release_name, app_name

    print("\n📋 CLI args vs mcp-project.yaml")
    print("-" * 70)
    for d in drift:
        print(f"  ! {d.flag}")
        print(f"      cli:     {_format_value(d.cli_value)}")
        print(f"      project: {_format_value(d.project_value)}  [{d.project_field}]")
    print("-" * 70)

    if not fix or not sys.stdin.isatty():
        if fix:
            print(
                "  --fix specified but stdin is not a TTY; using CLI values."
            )
        else:
            print("  (re-run with --fix to choose a winner interactively)\n")
        return namespace, release_name, app_name

    chosen_ns, chosen_release, chosen_app = namespace, release_name, app_name
    apply_all = False
    skip_all = False
    print("\n🔧 --fix: reviewing arg conflicts")
    for d in drift:
        print(f"\n• {d.flag}")
        action, apply_all, skip_all = _ask_winner(
            "CLI flag", d.cli_value,
            f"mcp-project.yaml ({d.project_field})", d.project_value,
            apply_all, skip_all,
        )
        if action == "use_other":
            if d.flag == "--namespace":
                chosen_ns = d.project_value
            elif d.flag == "--release-name":
                chosen_release = d.project_value
            elif d.flag == "--app-name":
                chosen_app = d.project_value
            print(f"    ✓ using project value for {d.flag} (this run only)")
        else:
            print(f"    [kept CLI value for {d.flag}]")
    return chosen_ns, chosen_release, chosen_app


@dataclass
class ValuesProposal:
    """A change the project file implies should land in oidc-values.yaml.

    Reverse direction from ``Proposal``: project-side fields are canonical,
    so when the values file disagrees we propose to patch the values file.
    """

    path: List[str]                  # ["image", "repository"], etc.
    proposed: Any                    # what oidc-values.yaml should hold
    current: Any = None              # what oidc-values.yaml currently holds
    reason: str = ""                 # source field in mcp-project.yaml
    auto_apply: bool = False         # True when current looks like a placeholder

    @property
    def kind(self) -> str:
        if self._equal():
            return "match"
        return "auto" if self.auto_apply else "conflict"

    def _equal(self) -> bool:
        if isinstance(self.proposed, list) and isinstance(self.current, list):
            return list(self.proposed) == list(self.current)
        return self.proposed == self.current


# ---------------------------------------------------------------------------
# Proposal computation: read saved artifacts, derive expected mcp-project values
# ---------------------------------------------------------------------------

def _load_oidc_values(path: str) -> Dict[str, Any]:
    """Load oidc-values.yaml as a plain dict; missing or unreadable → {}."""
    if not os.path.exists(path):
        return {}
    try:
        yaml = YAML(typ="safe")
        with open(path, "r") as f:
            data = yaml.load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _provider_to_auth_block(provider: str) -> Tuple[str, Optional[str]]:
    """Map saved ``provider`` to ``auth.type`` and optional ``auth.providerName``."""
    if provider == "auth0":
        return "auth0", None
    if provider == "keycloak":
        return "keycloak", None
    if provider in ("dex", "okta"):
        return "oidc", provider
    return "oidc", None  # generic / unknown


def compute_proposals(
    saved_oidc: Dict[str, Any],
    saved_values: Dict[str, Any],
) -> List[Proposal]:
    """Build the list of fields that should appear in mcp-project.yaml."""
    proposals: List[Proposal] = []

    provider = saved_oidc.get("provider")
    issuer = saved_oidc.get("issuer")
    audience = saved_oidc.get("audience")

    if provider:
        auth_type, provider_name = _provider_to_auth_block(provider)
        proposals.append(
            Proposal(["auth", "type"], auth_type, reason="oidc-config.json:provider")
        )
        if provider_name is not None:
            proposals.append(
                Proposal(
                    ["auth", "providerName"],
                    provider_name,
                    reason="oidc-config.json:provider",
                )
            )

    if issuer:
        proposals.append(
            Proposal(["auth", "issuer"], issuer, reason="oidc-config.json:issuer")
        )
    if audience:
        proposals.append(
            Proposal(["auth", "audience"], audience, reason="oidc-config.json:audience")
        )

    # Auth0-specific knobs only when the saved config is the auth0 flavor.
    if provider == "auth0":
        domain = saved_oidc.get("domain")
        if domain:
            proposals.append(
                Proposal(
                    ["auth", "auth0", "domain"], domain, reason="auth0-config.json:domain"
                )
            )
        if audience:
            proposals.append(
                Proposal(
                    ["auth", "auth0", "apiIdentifier"],
                    audience,
                    reason="auth0-config.json:audience",
                )
            )

    # oidc-values.yaml — required scopes + ingress / public endpoint
    if saved_values:
        oidc_block = saved_values.get("oidc") or {}
        scopes = oidc_block.get("requiredScopes")
        if isinstance(scopes, list) and scopes:
            proposals.append(
                Proposal(
                    ["auth", "requiredScopes"],
                    [str(s) for s in scopes],
                    reason=f"{OIDC_VALUES_FILE}:oidc.requiredScopes",
                )
            )

        ingress = saved_values.get("ingress") or {}
        host = ingress.get("host")
        if host and host not in _PLACEHOLDER_INGRESS_HOSTS:
            tls_enabled = bool((ingress.get("tls") or {}).get("enabled", True))
            scheme = "https" if tls_enabled else "http"
            proposals.append(
                Proposal(
                    ["publicEndpoint", "host"], host, reason=f"{OIDC_VALUES_FILE}:ingress.host"
                )
            )
            proposals.append(
                Proposal(
                    ["publicEndpoint", "scheme"],
                    scheme,
                    reason=f"{OIDC_VALUES_FILE}:ingress.tls.enabled",
                )
            )
            path = ingress.get("path")
            if path:
                proposals.append(
                    Proposal(
                        ["publicEndpoint", "path"],
                        path,
                        reason=f"{OIDC_VALUES_FILE}:ingress.path",
                    )
                )
            # mcpPath = the path component of the audience URL (the canonical
            # source for "where the MCP route lives behind the host").
            if audience:
                parsed = urlparse(audience)
                if parsed.path and parsed.path != "/":
                    proposals.append(
                        Proposal(
                            ["publicEndpoint", "mcpPath"],
                            parsed.path,
                            reason="derived from audience URL",
                        )
                    )

    return proposals


def _project_has_public_endpoint(project_doc: CommentedMap) -> bool:
    block = project_doc.get("publicEndpoint")
    return isinstance(block, dict) and bool(block)


def compute_values_drift(
    project_defaults: ProjectDefaults,
    saved_values: Dict[str, Any],
    project_doc: CommentedMap,
) -> List[ValuesProposal]:
    """Compare project-canonical fields against ``oidc-values.yaml``.

    - image.{repository,tag} from ``build:`` (always considered when present)
    - ingress.* and oidc.publicUrl from ``publicEndpoint:`` (only when the
      project file actually has that block — we don't second-guess users
      who haven't opted into the new schema yet)
    """
    drift: List[ValuesProposal] = []
    saved_values = saved_values or {}

    image_block = saved_values.get("image") or {}
    current_repo = image_block.get("repository")
    current_tag = image_block.get("tag")

    if project_defaults.image_repository:
        auto = current_repo in (None, "") or current_repo in _PLACEHOLDER_IMAGE_REPOS
        drift.append(
            ValuesProposal(
                path=["image", "repository"],
                proposed=project_defaults.image_repository,
                current=current_repo,
                reason="mcp-project.yaml:build.{registry,imageName}",
                auto_apply=auto,
            )
        )
    if project_defaults.image_tag is not None:
        auto = current_tag in (None, "")
        drift.append(
            ValuesProposal(
                path=["image", "tag"],
                proposed=project_defaults.image_tag,
                current=current_tag,
                reason="mcp-project.yaml:build.tag",
                auto_apply=auto,
            )
        )

    # service.type — always considered (deployment-canonical).
    if project_defaults.service_type:
        current_svc = (saved_values.get("service") or {}).get("type")
        drift.append(
            ValuesProposal(
                path=["service", "type"],
                proposed=project_defaults.service_type,
                current=current_svc,
                reason="mcp-project.yaml:deployment.serviceType",
                auto_apply=current_svc in (None, ""),
            )
        )

    # testSidecar.enabled — chart key assumed to be testSidecar.enabled.
    if project_defaults.test_sidecar_enabled is not None:
        current_sidecar = (saved_values.get("testSidecar") or {}).get("enabled")
        drift.append(
            ValuesProposal(
                path=["testSidecar", "enabled"],
                proposed=project_defaults.test_sidecar_enabled,
                current=current_sidecar,
                reason="mcp-project.yaml:deployment.testSidecarEnabled",
                auto_apply=current_sidecar is None,
            )
        )

    # Ingress / publicUrl drift — gated on the project having a publicEndpoint.
    if _project_has_public_endpoint(project_doc):
        ingress_block = saved_values.get("ingress") or {}
        oidc_block = saved_values.get("oidc") or {}

        if project_defaults.ingress_host:
            current_host = ingress_block.get("host")
            auto = current_host in (None, "") or current_host in _PLACEHOLDER_INGRESS_HOSTS
            drift.append(
                ValuesProposal(
                    path=["ingress", "host"],
                    proposed=project_defaults.ingress_host,
                    current=current_host,
                    reason="mcp-project.yaml:publicEndpoint.host",
                    auto_apply=auto,
                )
            )
        if project_defaults.ingress_path:
            current_path = ingress_block.get("path")
            auto = current_path in (None, "")
            drift.append(
                ValuesProposal(
                    path=["ingress", "path"],
                    proposed=project_defaults.ingress_path,
                    current=current_path,
                    reason="mcp-project.yaml:publicEndpoint.path",
                    auto_apply=auto,
                )
            )
        if project_defaults.ingress_tls_enabled is not None:
            current_tls = (ingress_block.get("tls") or {}).get("enabled")
            auto = current_tls is None
            drift.append(
                ValuesProposal(
                    path=["ingress", "tls", "enabled"],
                    proposed=project_defaults.ingress_tls_enabled,
                    current=current_tls,
                    reason="mcp-project.yaml:publicEndpoint.scheme",
                    auto_apply=auto,
                )
            )
        if project_defaults.public_url:
            current_url = oidc_block.get("publicUrl")
            auto = current_url in (None, "")
            drift.append(
                ValuesProposal(
                    path=["oidc", "publicUrl"],
                    proposed=project_defaults.public_url,
                    current=current_url,
                    reason="mcp-project.yaml:publicEndpoint.{scheme,host,path}",
                    auto_apply=auto,
                )
            )

    # Drop entries where project and values already agree.
    return [d for d in drift if d.kind != "match"]


# ---------------------------------------------------------------------------
# YAML round-trip helpers (ruamel.yaml preserves comments and key order)
# ---------------------------------------------------------------------------

def _yaml() -> YAML:
    yaml = YAML()
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.preserve_quotes = True
    return yaml


def _load_project_yaml(path: str) -> Optional[CommentedMap]:
    if not os.path.exists(path):
        return None
    yaml = _yaml()
    with open(path, "r") as f:
        data = yaml.load(f)
    if data is None:
        return CommentedMap()
    if not isinstance(data, CommentedMap) and isinstance(data, dict):
        return CommentedMap(data)
    if isinstance(data, CommentedMap):
        return data
    return None


def _read_path(doc: CommentedMap, path: List[str]) -> Any:
    """Look up a dotted path in the project document. None if any segment missing."""
    cur: Any = doc
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _write_path(doc: CommentedMap, path: List[str], value: Any) -> None:
    """Write ``value`` at the dotted path, creating CommentedMap nodes as needed."""
    cur = doc
    for key in path[:-1]:
        nxt = cur.get(key) if isinstance(cur, dict) else None
        if not isinstance(nxt, CommentedMap):
            nxt = CommentedMap()
            cur[key] = nxt
        cur = nxt
    cur[path[-1]] = value


def _save_project_yaml(path: str, doc: CommentedMap) -> None:
    yaml = _yaml()
    with open(path, "w") as f:
        yaml.dump(doc, f)


# ---------------------------------------------------------------------------
# Rendering + interactive flow
# ---------------------------------------------------------------------------

def _format_value(value: Any) -> str:
    if value is None:
        return "<unset>"
    if isinstance(value, list):
        return "[" + ", ".join(repr(v) for v in value) + "]"
    return repr(value)


def _path_str(path: List[str]) -> str:
    return ".".join(path)


def _annotate_proposals(
    project_doc: Optional[CommentedMap], proposals: List[Proposal]
) -> List[Proposal]:
    """Populate ``current`` for each proposal by reading the project doc."""
    if project_doc is None:
        return proposals
    for p in proposals:
        p.current = _read_path(project_doc, p.path)
    return proposals


def _print_summary(
    proposals: List[Proposal],
    values_drift: List[ValuesProposal],
    project_path: str,
    values_path: str,
) -> None:
    missing = [p for p in proposals if p.kind == "missing"]
    conflicts = [p for p in proposals if p.kind == "conflict"]
    auto_values = [v for v in values_drift if v.kind == "auto"]
    conflict_values = [v for v in values_drift if v.kind == "conflict"]

    if not missing and not conflicts and not auto_values and not conflict_values:
        return

    print(f"\n📋 Reconciling {project_path} with saved CLI artifacts")
    print("-" * 70)
    if missing:
        print("Missing in mcp-project.yaml (saved value would be added):")
        for p in missing:
            print(f"  + {_path_str(p.path)}: {_format_value(p.proposed)}  [{p.reason}]")
    if conflicts:
        print("Conflicts (mcp-project.yaml ↔ saved):")
        for p in conflicts:
            print(f"  ! {_path_str(p.path)}")
            print(f"      project: {_format_value(p.current)}")
            print(f"      saved:   {_format_value(p.proposed)}")
    if auto_values or conflict_values:
        print(f"\n{values_path} drift (project values are canonical):")
        for v in auto_values:
            print(
                f"  ~ {_path_str(v.path)}: "
                f"{_format_value(v.current)} → {_format_value(v.proposed)}  "
                f"[{v.reason}]"
            )
        for v in conflict_values:
            print(f"  ! {_path_str(v.path)}")
            print(f"      values:  {_format_value(v.current)}")
            print(f"      project: {_format_value(v.proposed)}")
    print("-" * 70)


def _apply_missing(doc: CommentedMap, missing: List[Proposal]) -> int:
    for p in missing:
        _write_path(doc, p.path, p.proposed)
    return len(missing)


def _load_values_doc(path: str) -> Optional[CommentedMap]:
    if not os.path.exists(path):
        return None
    yaml = _yaml()
    with open(path, "r") as f:
        data = yaml.load(f)
    if isinstance(data, CommentedMap):
        return data
    if isinstance(data, dict):
        return CommentedMap(data)
    return None


def _save_values_doc(path: str, doc: CommentedMap) -> None:
    yaml = _yaml()
    with open(path, "w") as f:
        yaml.dump(doc, f)


def _apply_values_auto(values_doc: CommentedMap, auto: List[ValuesProposal]) -> int:
    for v in auto:
        _write_path(values_doc, v.path, v.proposed)
    return len(auto)


def _ask_winner(
    label_kept: str,
    value_kept: Any,
    label_other: str,
    value_other: Any,
    apply_all: bool,
    skip_all: bool,
) -> Tuple[str, bool, bool]:
    """Prompt the user to pick which side wins.

    ``[1]`` is the side that "lives" in the file we'd be modifying — picking it
    means no change. ``[2]`` is the other side; picking it means write into
    the file. Returns ``(action, apply_all, skip_all)`` where action is
    ``"keep"`` or ``"use_other"``.
    """
    print(f"    [1] {label_kept}: {_format_value(value_kept)}")
    print(f"    [2] {label_other}: {_format_value(value_other)}")
    if skip_all:
        print("    [1 — skip-all]")
        return "keep", apply_all, skip_all
    if apply_all:
        print("    [2 — all]")
        return "use_other", apply_all, skip_all

    choice = input(
        "  Which is correct? [1/2/a=all-2/s=skip-all]: "
    ).strip().lower()
    if choice == "a":
        return "use_other", True, skip_all
    if choice == "s":
        print("    [1 — skip-all]")
        return "keep", apply_all, True
    if choice == "2":
        return "use_other", apply_all, skip_all
    return "keep", apply_all, skip_all


def _prompt_values_conflicts(
    values_doc: CommentedMap, conflicts: List[ValuesProposal], values_path: str
) -> int:
    """Per-field winner prompt for project↔values conflicts."""
    if not conflicts:
        return 0
    if not sys.stdin.isatty():
        print(
            f"\n⚠️  --fix specified but stdin is not a TTY; {values_path} "
            "conflict prompts skipped."
        )
        return 0
    apply_all = False
    skip_all = False
    written = 0
    print(f"\n🔧 --fix: reviewing {values_path} conflicts")
    for v in conflicts:
        print(f"\n• {_path_str(v.path)}")
        action, apply_all, skip_all = _ask_winner(
            values_path, v.current,
            "mcp-project.yaml", v.proposed,
            apply_all, skip_all,
        )
        if action == "use_other":
            _write_path(values_doc, v.path, v.proposed)
            written += 1
            print(f"    ✓ wrote project value into {values_path}")
        else:
            print(f"    [kept {values_path} value]")
    return written


def _prompt_conflicts(doc: CommentedMap, conflicts: List[Proposal]) -> int:
    """Per-field winner prompt for saved↔project conflicts."""
    if not conflicts:
        return 0
    if not sys.stdin.isatty():
        print(
            "\n⚠️  --fix specified but stdin is not a TTY; conflict prompts skipped.\n"
            "    Re-run interactively to apply conflict fixes."
        )
        return 0

    apply_all = False
    skip_all = False
    written = 0

    print("\n🔧 --fix: reviewing project-side conflicts")
    for p in conflicts:
        print(f"\n• {_path_str(p.path)}")
        action, apply_all, skip_all = _ask_winner(
            "mcp-project.yaml", p.current,
            "saved config", p.proposed,
            apply_all, skip_all,
        )
        if action == "use_other":
            _write_path(doc, p.path, p.proposed)
            written += 1
            print("    ✓ wrote saved value into mcp-project.yaml")
        else:
            print("    [kept project value]")
    return written


def reconcile_project(
    project_path: str,
    saved_oidc: Dict[str, Any],
    oidc_values_path: str = OIDC_VALUES_FILE,
    *,
    fix: bool,
    project_defaults: Optional[ProjectDefaults] = None,
) -> None:
    """Compare mcp-project.yaml against saved artifacts and (optionally) repair.

    - Always: print a summary of missing / conflicting fields plus oidc-values.yaml drift.
    - With ``fix``:
        * auto-add missing project fields
        * prompt for project-side conflicts
        * auto-apply oidc-values.yaml drift where the values side looks placeholder
        * prompt for real values conflicts
        * write both files back, preserving comments
    """
    if not os.path.exists(project_path):
        return  # no project file yet — nothing to reconcile

    project_doc = _load_project_yaml(project_path)
    if project_doc is None:
        return

    saved_values = _load_oidc_values(oidc_values_path)
    proposals = compute_proposals(saved_oidc, saved_values)
    _annotate_proposals(project_doc, proposals)

    if project_defaults is None:
        project_defaults = load_project_defaults(
            cwd=os.path.dirname(os.path.abspath(project_path))
        )
    values_drift = compute_values_drift(project_defaults, saved_values, project_doc)

    missing = [p for p in proposals if p.kind == "missing"]
    conflicts = [p for p in proposals if p.kind == "conflict"]
    auto_values = [v for v in values_drift if v.kind == "auto"]
    conflict_values = [v for v in values_drift if v.kind == "conflict"]

    if not missing and not conflicts and not auto_values and not conflict_values:
        return

    _print_summary(proposals, values_drift, project_path, oidc_values_path)

    if not fix:
        total_proj = len(missing) + len(conflicts)
        total_val = len(auto_values) + len(conflict_values)
        print(
            f"(re-run with --fix to apply — "
            f"{total_proj} project change(s), {total_val} values change(s))\n"
        )
        return

    added = _apply_missing(project_doc, missing) if missing else 0
    changed = _prompt_conflicts(project_doc, conflicts) if conflicts else 0
    if added or changed:
        _save_project_yaml(project_path, project_doc)
        print(
            f"\n💾 Updated {project_path} "
            f"({added} added, {changed} reconciled)"
        )

    if auto_values or conflict_values:
        values_doc = _load_values_doc(oidc_values_path)
        if values_doc is None:
            print(
                f"\n⚠️  Cannot apply {oidc_values_path} drift — file is missing "
                "or unreadable. Run mcp-base setup-oidc to regenerate."
            )
        else:
            v_auto = _apply_values_auto(values_doc, auto_values)
            v_changed = _prompt_values_conflicts(
                values_doc, conflict_values, oidc_values_path
            )
            if v_auto or v_changed:
                _save_values_doc(oidc_values_path, values_doc)
                print(
                    f"\n💾 Updated {oidc_values_path} "
                    f"({v_auto} auto, {v_changed} reconciled)"
                )


def reconcile_from_command(
    saved_config: Dict[str, Any],
    saved_config_path: str,
    *,
    fix: bool,
    project_path: str = PROJECT_FILE,
    oidc_values_path: str = OIDC_VALUES_FILE,
) -> None:
    """Convenience entry point used by every subcommand.

    No-op when ``mcp-project.yaml`` doesn't exist or saved config is empty.
    """
    if not saved_config:
        return
    if not os.path.exists(project_path):
        return
    reconcile_project(
        project_path,
        saved_config,
        oidc_values_path=oidc_values_path,
        fix=fix,
    )
