"""
Loader for ``mcp-project.yaml`` defaults.

The CLI auto-detects ``./mcp-project.yaml`` in the current working directory
(no parent walk) and uses its ``publicEndpoint`` and ``auth`` blocks as a
defaults source for ``setup-oidc``. Precedence is:

    CLI flag > env var > mcp-project.yaml > saved oidc-config.json > prompt

See ``imp/NOTES3.md`` for the schema rationale.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - dependency declared in pyproject.toml
    yaml = None  # type: ignore[assignment]


PROJECT_FILE = "mcp-project.yaml"


# Map mcp-project.yaml ``auth.type`` values to the CLI's --provider names.
# When ``auth.type == "oidc"``, ``auth.providerName`` disambiguates among
# {dex, okta, generic}; default to "generic" if unset.
_AUTH_TYPE_TO_PROVIDER: Dict[str, str] = {
    "auth0": "auth0",
    "keycloak": "keycloak",
}


@dataclass
class ProjectDefaults:
    """Defaults derived from mcp-project.yaml. All fields are optional.

    A field of ``None`` means "the project file did not specify this; fall
    through to the next layer of precedence (saved config / prompt)".
    """

    # auth.* derived
    provider_name: Optional[str] = None
    issuer: Optional[str] = None
    audience: Optional[str] = None
    required_scopes: Optional[List[str]] = None

    # publicEndpoint.* derived
    public_url: Optional[str] = None
    ingress_host: Optional[str] = None
    ingress_path: Optional[str] = None
    ingress_tls_enabled: Optional[bool] = None

    # auth.auth0.* — auth0 path only
    auth0_domain: Optional[str] = None
    auth0_api_identifier: Optional[str] = None

    # build.* derived (canonical for oidc-values.image)
    image_repository: Optional[str] = None
    image_tag: Optional[str] = None

    # deployment.* derived (canonical for chart deploy-shape values)
    service_type: Optional[str] = None
    test_sidecar_enabled: Optional[bool] = None

    # Identity fields used to fall back / cross-check CLI flags
    deployment_namespace: Optional[str] = None       # → --namespace
    deployment_release_name: Optional[str] = None    # → --release-name
    project_app_name: Optional[str] = None           # → --app-name

    # Diagnostics
    source_path: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def loaded(self) -> bool:
        """True if a project file was found and parsed (even if empty)."""
        return self.source_path is not None


def _compose_audience(host: Optional[str], scheme: Optional[str], mcp_path: Optional[str]) -> Optional[str]:
    """Compose default audience as ``<scheme>://<host><mcpPath>``."""
    if not host or not scheme:
        return None
    path = mcp_path or ""
    if path and not path.startswith("/"):
        path = "/" + path
    return f"{scheme}://{host}{path}"


def _compose_public_url(
    host: Optional[str], scheme: Optional[str], path: Optional[str]
) -> Optional[str]:
    """Compose oidc.publicUrl as ``<scheme>://<host><path>``.

    A bare ``/`` path is dropped so the URL doesn't end in a redundant slash.
    """
    if not host or not scheme:
        return None
    if path and path not in ("/", ""):
        if not path.startswith("/"):
            path = "/" + path
        return f"{scheme}://{host}{path.rstrip('/')}"
    return f"{scheme}://{host}"


def _resolve_provider_name(auth_block: Dict[str, Any]) -> Optional[str]:
    """Map ``auth.type`` (+ ``auth.providerName`` for oidc) to a CLI provider."""
    auth_type = auth_block.get("type")
    if not auth_type:
        return None
    if auth_type in _AUTH_TYPE_TO_PROVIDER:
        return _AUTH_TYPE_TO_PROVIDER[auth_type]
    if auth_type == "oidc":
        provider_name = auth_block.get("providerName")
        if provider_name in ("dex", "okta", "generic"):
            return provider_name
        # auth.type=oidc with no/unrecognized providerName → fall back to generic
        return "generic"
    return None


def load_project_defaults(cwd: Optional[str] = None) -> ProjectDefaults:
    """Load and parse ``./mcp-project.yaml`` if present.

    Returns an empty ``ProjectDefaults`` (with ``loaded=False``) when no file
    exists, when YAML support is unavailable, or when the file is malformed.
    Parse errors are recorded in ``warnings`` rather than raised — the CLI
    should still be usable when the file is broken.
    """
    base = cwd or os.getcwd()
    path = os.path.join(base, PROJECT_FILE)
    if not os.path.exists(path):
        return ProjectDefaults()

    if yaml is None:  # pragma: no cover
        defaults = ProjectDefaults(source_path=path)
        defaults.warnings.append(
            "PyYAML is not installed; mcp-project.yaml ignored. "
            "Reinstall mcp-base to pick up the dependency."
        )
        return defaults

    try:
        with open(path, "r") as f:
            raw = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as exc:
        defaults = ProjectDefaults(source_path=path)
        defaults.warnings.append(f"Could not parse {PROJECT_FILE}: {exc}")
        return defaults

    if not isinstance(raw, dict):
        defaults = ProjectDefaults(source_path=path)
        defaults.warnings.append(
            f"{PROJECT_FILE}: top level must be a mapping; ignoring contents"
        )
        return defaults

    defaults = ProjectDefaults(source_path=path)

    public_endpoint = raw.get("publicEndpoint") or {}
    auth_block = raw.get("auth") or {}

    if not isinstance(public_endpoint, dict):
        defaults.warnings.append("publicEndpoint must be a mapping; ignoring")
        public_endpoint = {}
    if not isinstance(auth_block, dict):
        defaults.warnings.append("auth must be a mapping; ignoring")
        auth_block = {}

    host = public_endpoint.get("host")
    scheme = public_endpoint.get("scheme")
    path_value = public_endpoint.get("path")
    mcp_path = public_endpoint.get("mcpPath")

    if host:
        defaults.ingress_host = host
    if path_value:
        defaults.ingress_path = path_value
    if scheme:
        defaults.ingress_tls_enabled = (scheme == "https")
    defaults.public_url = _compose_public_url(host, scheme, path_value)

    # auth fields
    defaults.provider_name = _resolve_provider_name(auth_block)
    issuer = auth_block.get("issuer")
    if issuer:
        defaults.issuer = issuer

    audience = auth_block.get("audience")
    if audience:
        defaults.audience = audience
    else:
        # Per NOTES3.md: default to https://<host><mcpPath> when omitted
        defaults.audience = _compose_audience(host, scheme, mcp_path)

    required_scopes = auth_block.get("requiredScopes")
    if isinstance(required_scopes, list) and required_scopes:
        defaults.required_scopes = [str(s) for s in required_scopes]
    elif required_scopes is not None:
        defaults.warnings.append(
            "auth.requiredScopes must be a non-empty list; ignoring"
        )

    auth0_block = auth_block.get("auth0") or {}
    if isinstance(auth0_block, dict):
        domain = auth0_block.get("domain")
        api_identifier = auth0_block.get("apiIdentifier")
        if domain:
            defaults.auth0_domain = domain
        if api_identifier:
            defaults.auth0_api_identifier = api_identifier
    elif auth0_block:
        defaults.warnings.append("auth.auth0 must be a mapping; ignoring")

    build_block = raw.get("build") or {}
    if isinstance(build_block, dict):
        registry = build_block.get("registry")
        image_name = build_block.get("imageName")
        if registry and image_name:
            defaults.image_repository = f"{registry}/{image_name}"
        elif image_name:
            defaults.image_repository = image_name
        tag = build_block.get("tag")
        if tag:
            defaults.image_tag = str(tag)
    elif build_block:
        defaults.warnings.append("build must be a mapping; ignoring")

    deployment_block = raw.get("deployment") or {}
    if isinstance(deployment_block, dict):
        service_type = deployment_block.get("serviceType")
        if service_type:
            defaults.service_type = str(service_type)
        sidecar = deployment_block.get("testSidecarEnabled")
        if sidecar is not None:
            defaults.test_sidecar_enabled = bool(sidecar)
        ns = deployment_block.get("namespace")
        if ns:
            defaults.deployment_namespace = str(ns)
        release = deployment_block.get("helmRelease")
        if release:
            defaults.deployment_release_name = str(release)
    elif deployment_block:
        defaults.warnings.append("deployment must be a mapping; ignoring")

    project_block = raw.get("project") or {}
    if isinstance(project_block, dict):
        name = project_block.get("name")
        if name:
            defaults.project_app_name = str(name)
    elif project_block:
        defaults.warnings.append("project must be a mapping; ignoring")

    return defaults


def resolve(
    cli_value: Optional[str],
    env_var: Optional[str],
    project_value: Optional[str],
    saved_value: Optional[str] = None,
) -> Optional[str]:
    """Walk the standard precedence chain and return the first non-empty value.

    Order: CLI flag → env var → mcp-project.yaml → saved config.
    Returns ``None`` if every layer is empty; callers handle prompting.
    """
    if cli_value:
        return cli_value
    if env_var:
        env_value = os.getenv(env_var)
        if env_value:
            return env_value
    if project_value:
        return project_value
    if saved_value:
        return saved_value
    return None
