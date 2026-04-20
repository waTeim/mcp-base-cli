# Keycloak How-To

> **Compatibility:** Keycloak support in [FastMCP](https://github.com/jlowin/fastmcp) (the framework the MCP server is built with) was added in **FastMCP 3.2.4**, and only works against **Keycloak 26.6.0 or newer**. Both constraints must hold simultaneously — an older FastMCP cannot talk to any Keycloak, and FastMCP 3.2.4+ cannot talk to Keycloak < 26.6.0. If either side is below these minimums, use the Pattern A fallback at the end of this doc (`--provider generic` with a Keycloak issuer) instead.

`mcp-base setup-oidc --provider keycloak` targets **Pattern B** (Remote DCR) as defined in [`imp/cli-integration-contract.md`](imp/cli-integration-contract.md) §1. FastMCP's `KeycloakAuthProvider` registers MCP clients dynamically through Keycloak's native Dynamic Client Registration — **you do not pre-create a client, there is no client secret to copy, and no Kubernetes credentials Secret is created**. `create-secrets` is a no-op for this pattern.

At the end of this procedure you will have two values to pass to `setup-oidc`:

| `setup-oidc` flag | Keycloak source |
|---|---|
| `--issuer` | `https://<keycloak-host>/realms/<realm>` (realm URL, not the Keycloak root) |
| `--audience` | MCP server API identifier — must appear in issued tokens' `aud` claim (see Step 3) |

---

## Prerequisites

- **MCP server built on FastMCP ≥ 3.2.4.** First release with `KeycloakAuthProvider`. FastMCP is a framework, not a server you run directly — check the MCP server project's `pyproject.toml` / lockfile.
- **Keycloak ≥ 26.6.0.** FastMCP's Keycloak support requires DCR features introduced in this release.
- Admin credentials for the master realm (or equivalent permissions on the target realm).
- The MCP server's public base URL and its API audience identifier. Typically the audience is the MCP URL ending in `/mcp`, e.g. `https://mcp-server.example.com/mcp`.

---

## Step 1 — Pick or create a realm

The realm is the tenant boundary. `--issuer` points at the realm URL, not the Keycloak root.

1. Admin Console → top-left realm switcher → **Create Realm** (or select an existing one).
2. Give it a name (e.g. `mcp`). The issuer becomes `https://<keycloak-host>/realms/mcp`.

Do **not** use the `master` realm for applications.

---

## Step 2 — Enable Dynamic Client Registration

Pattern B requires DCR to be enabled on the realm so FastMCP can register the MCP client at runtime. The exact Keycloak admin path depends on your registration policy; the simplest working combination for a trusted environment is:

1. Realm → **Client registration** (left nav) → **Policies** tab.
2. Under **Anonymous Access Policies**, confirm the defaults are in place — Keycloak ships a restrictive set that allows registration but constrains scopes/protocol mappers.
3. If your deployment requires authenticated registration, create an **Initial Access Token**: Realm → **Client registration** → **Initial access token** → **Create**. Give it to whichever FastMCP configuration mechanism consumes it. For anonymous DCR (the default), no token is needed.

The exact DCR configuration Keycloak requires may evolve across 26.x releases; consult the Keycloak docs at https://www.keycloak.org/docs for the specifics that match your version. FastMCP's `KeycloakAuthProvider` documentation will tell you which registration mode it uses.

---

## Step 3 — Ensure tokens carry the MCP audience

By default, Keycloak issues access tokens whose `aud` claim is the client_id of the registered client. FastMCP validates `aud` against your configured audience — without an audience mapper, every MCP request fails with an audience mismatch.

Because DCR-registered clients don't exist until FastMCP registers them, the audience mapper needs to live on a **shared** client scope (not a client-dedicated one) so every newly-registered client inherits it.

1. Realm → **Client scopes** (left nav).
2. Either edit an existing default scope (e.g. `profile`) or create a new scope (e.g. `mcp-audience`) and mark it as a **Default** scope for the realm under **Realm settings → Client registration → Default scopes**.
3. Open the scope → **Mappers** tab → **Add mapper → By configuration → Audience**.
4. Fill in:
   - Name: `mcp-audience`
   - Included Client Audience: leave blank
   - **Included Custom Audience**: the exact value you will pass to `--audience` (e.g. `https://mcp-server.example.com/mcp`)
   - Add to access token: **On**
   - Add to ID token: Off
5. Save.

Verify: after a test DCR + login flow, decode the issued access token at https://jwt.io — the `aud` claim must contain your custom audience string.

---

## Step 4 — Create a test user (if the realm has none)

1. Left nav → **Users** → **Add user**.
2. Username, email verified: On. Save.
3. **Credentials** tab → **Set password**. Turn **Temporary** off for scripted logins.

---

## Step 5 — Run `setup-oidc`

From the directory where you want config files written:

```bash
mcp-base setup-oidc --provider keycloak \
  --issuer   https://<keycloak-host>/realms/<realm> \
  --audience https://<mcp-server-host>/mcp
```

No `--client-id` / `--client-secret` are needed. If you pass them anyway, the CLI prints a warning and drops them — they are never written to `oidc-config.json`.

`setup-oidc` will:
- Hit `<issuer>/.well-known/openid-configuration` and write the real `authorization_endpoint`, `token_endpoint`, and `jwks_uri` into `oidc-config.json`.
- Set `"provider": "keycloak"`, `"pattern": "remote"` in `oidc-config.json` and omit any `server_client` block.
- Generate `oidc-values.yaml` with `oidc.authType: "keycloak"`, `redis.enabled: false`, `jwt.enabled: false`.

Pass `--skip-validation` if the CLI host cannot reach Keycloak; endpoint paths fall back to `{issuer}/protocol/openid-connect/{auth,token,certs}`.

---

## Step 6 — Skip `create-secrets`

For Pattern B this command is a no-op:

```bash
$ mcp-base create-secrets --namespace mcp --release-name my-mcp
Pattern B detected (Keycloak native DCR).
No Kubernetes secrets are required — FastMCP's KeycloakAuthProvider
registers clients dynamically and verifies tokens against JWKS.
Nothing to do. Exiting.
```

The command exits `0` without contacting the Kubernetes API — safe to leave in a pipeline.

---

## Scripted alternative with `kcadm.sh`

For agents and CI, the Keycloak admin CLI sets up realm + audience mapper without clicking.

```bash
kcadm.sh config credentials \
  --server "$KC_URL" --realm master \
  --user "$KC_ADMIN" --password "$KC_ADMIN_PASSWORD"

REALM=mcp
AUDIENCE=https://mcp-server.example.com/mcp

# (1) Create the realm (skip if it exists)
kcadm.sh create realms -s realm="$REALM" -s enabled=true

# (2) Create a shared client scope with the audience mapper and mark it default
SCOPE_ID=$(kcadm.sh create client-scopes -r "$REALM" -f - -i <<EOF
{
  "name": "mcp-audience",
  "protocol": "openid-connect",
  "attributes": { "include.in.token.scope": "true" }
}
EOF
)

kcadm.sh create "client-scopes/$SCOPE_ID/protocol-mappers/models" -r "$REALM" -f - <<EOF
{
  "name": "mcp-audience",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-audience-mapper",
  "config": {
    "included.custom.audience": "$AUDIENCE",
    "access.token.claim": "true",
    "id.token.claim": "false"
  }
}
EOF

# Add the scope to the realm's default scopes so DCR-registered clients inherit it
kcadm.sh update "realms/$REALM/default-default-client-scopes/$SCOPE_ID" -r "$REALM"

# (3) Hand off to setup-oidc — no client credentials
mcp-base setup-oidc --provider keycloak \
  --issuer   "$KC_URL/realms/$REALM" \
  --audience "$AUDIENCE"
```

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| MCP server rejects token with `aud` mismatch | Audience mapper missing from a default (realm-level) client scope, or not marked **Add to access token**. Clients created via DCR only see default scopes. |
| FastMCP can't register a client with Keycloak | DCR not enabled, or registration policy is rejecting the request. Check Keycloak server logs under **Events → Admin events**. |
| MCP server accepts tokens but reports "unsupported provider" | FastMCP version < 3.2.4. Upgrade the MCP server's FastMCP dependency. |
| DCR appears to work but tokens are malformed / missing claims | Keycloak version < 26.6.0 returning DCR responses in an older format. Upgrade Keycloak. |
| `mcp-base setup-oidc` prints "Could not validate OIDC issuer" | The CLI's host cannot reach `{issuer}/.well-known/openid-configuration`. Verify DNS/TLS, or rerun with `--skip-validation` if the endpoints are known to be standard Keycloak paths. |

---

## Appendix — Pattern A fallback (older Keycloak, or pre-registered client)

If your Keycloak is older than 26.6.0, or your FastMCP is older than 3.2.4, or you simply prefer to pre-register a confidential client, use **Pattern A** instead by running `setup-oidc --provider generic` (not `keycloak`) against a Keycloak realm. The CLI will then treat it like any other OIDC provider: client secret required, Redis + JWT secrets created.

### A.1 — Create the confidential client (Admin UI)

1. Realm → **Clients** → **Create client**.
2. **General settings**: Client type **OpenID Connect**, Client ID e.g. `mcp-server`.
3. **Capability config**: Client authentication **On**, Standard flow **On**, Direct access grants **On** (for testing).
4. **Login settings → Valid redirect URIs**:
   ```
   https://<mcp-server-host>/auth/callback
   https://claude.ai/api/mcp/auth_callback
   http://localhost:8888/callback
   http://localhost:8889/callback
   ```
5. **Save**.
6. **Credentials** tab → copy the **Client secret**.

### A.2 — Audience mapper on the dedicated scope

1. Client → **Client scopes** → `<client-id>-dedicated` → **Add mapper → By configuration → Audience**.
2. **Included Custom Audience**: your `--audience` value. **Add to access token: On**.

### A.3 — Run `setup-oidc` in Pattern A mode

```bash
mcp-base setup-oidc --provider generic \
  --issuer        https://<keycloak-host>/realms/<realm> \
  --audience      https://<mcp-server-host>/mcp \
  --client-id     <client-id> \
  --client-secret <secret>
```

This writes `oidc-config.json` with `"pattern": "proxy"` and `server_client`, and `oidc-values.yaml` with `oidc.authType: "oidc"`, `redis.enabled: true`, `jwt.enabled: true`. Follow up with `mcp-base create-secrets` normally.
