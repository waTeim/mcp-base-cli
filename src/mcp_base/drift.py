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


PROJECT_FILE = "mcp-project.yaml"
OIDC_VALUES_FILE = "oidc-values.yaml"

# Placeholder defaults the CLI emits when no real value is known. We never
# propose these into mcp-project.yaml because they're not "real" config —
# they're examples in the generated file.
_PLACEHOLDER_INGRESS_HOSTS = {"mcp-api.example.com"}


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


def _print_summary(proposals: List[Proposal], project_path: str) -> None:
    missing = [p for p in proposals if p.kind == "missing"]
    conflicts = [p for p in proposals if p.kind == "conflict"]
    if not missing and not conflicts:
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
    print("-" * 70)


def _apply_missing(doc: CommentedMap, missing: List[Proposal]) -> int:
    for p in missing:
        _write_path(doc, p.path, p.proposed)
    return len(missing)


def _prompt_conflicts(doc: CommentedMap, conflicts: List[Proposal]) -> int:
    """Per-field prompt for divergent values. Returns count of writes."""
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

    print("\n🔧 --fix: reviewing conflicts")
    for p in conflicts:
        print(f"\n• {_path_str(p.path)}")
        print(f"    project: {_format_value(p.current)}")
        print(f"    saved:   {_format_value(p.proposed)}")
        if skip_all:
            print("    [kept]")
            continue
        if apply_all:
            choice = "y"
        else:
            choice = input(
                "  Update mcp-project.yaml to saved value? "
                "[y]es / [n]o / [a]ll / [s]kip-all: "
            ).strip().lower()
        if choice == "a":
            apply_all = True
            choice = "y"
        elif choice == "s":
            skip_all = True
            print("    [kept]")
            continue
        if choice == "y":
            _write_path(doc, p.path, p.proposed)
            written += 1
            print("    ✓ updated")
        else:
            print("    [kept]")
    return written


def reconcile_project(
    project_path: str,
    saved_oidc: Dict[str, Any],
    oidc_values_path: str = OIDC_VALUES_FILE,
    *,
    fix: bool,
) -> None:
    """Compare mcp-project.yaml against saved artifacts and (optionally) repair.

    - Always: print a summary of missing / conflicting fields.
    - With ``fix``: auto-add missing fields, prompt for conflicts, write back.
    """
    if not os.path.exists(project_path):
        return  # no project file yet — nothing to reconcile

    project_doc = _load_project_yaml(project_path)
    if project_doc is None:
        return

    saved_values = _load_oidc_values(oidc_values_path)
    proposals = compute_proposals(saved_oidc, saved_values)
    if not proposals:
        return

    _annotate_proposals(project_doc, proposals)

    missing = [p for p in proposals if p.kind == "missing"]
    conflicts = [p for p in proposals if p.kind == "conflict"]

    if not missing and not conflicts:
        return

    _print_summary(proposals, project_path)

    if not fix:
        print(f"(re-run with --fix to apply — {len(missing)} addition(s), "
              f"{len(conflicts)} conflict(s))\n")
        return

    added = _apply_missing(project_doc, missing) if missing else 0
    changed = _prompt_conflicts(project_doc, conflicts) if conflicts else 0

    if added or changed:
        _save_project_yaml(project_path, project_doc)
        print(
            f"\n💾 Updated {project_path} "
            f"({added} added, {changed} reconciled)"
        )
    else:
        print(f"\nNothing changed in {project_path}.")


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
