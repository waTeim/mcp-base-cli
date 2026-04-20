# Keycloak Client How-To

> **Compatibility:** Keycloak support in [FastMCP](https://github.com/jlowin/fastmcp) (the framework the MCP server is built with) was added in **FastMCP 3.2.4**, and only works against **Keycloak 26.6.0 or newer**. Both constraints must hold simultaneously — an older FastMCP cannot talk to any Keycloak, and FastMCP 3.2.4+ cannot talk to Keycloak < 26.6.0. The client configured below is only usable when both sides meet those minimums.

Step-by-step setup for the Keycloak client that `mcp-base setup-oidc --provider keycloak` consumes. At the end you will have four values:

| `setup-oidc` flag | Keycloak source |
|---|---|
| `--issuer` | `https://<keycloak-host>/realms/<realm>` |
| `--audience` | MCP server API identifier (must match an `aud` claim — see Step 5) |
| `--client-id` | Clients → your client → **Client ID** |
| `--client-secret` | Clients → your client → **Credentials** tab |

Redirect URIs you will configure on the client:
- `https://<mcp-server-host>/auth/callback` — production MCP server
- `https://claude.ai/api/mcp/auth_callback` — Claude Desktop
- `http://localhost:8888/callback`, `http://localhost:8889/callback` — optional, local testing

---

## Prerequisites

- **MCP server built on FastMCP ≥ 3.2.4.** This is the first FastMCP release that understands Keycloak as an OIDC provider. FastMCP is the framework the MCP server is built with, not a server you run directly — check your MCP server project's `pyproject.toml` / lockfile.
- **Keycloak ≥ 26.6.0.** FastMCP's Keycloak support requires features introduced in this Keycloak release; earlier Keycloak versions will fail even with FastMCP 3.2.4+.
- Admin credentials for the master realm (or equivalent permissions on the target realm).
- The MCP server's public base URL and its API audience identifier. Typically audience is the MCP URL ending in `/mcp`, e.g. `https://mcp-server.example.com/mcp`.

---

## Step 1 — Pick or create a realm

The realm is the tenant boundary. `setup-oidc --issuer` points at the realm URL, not the Keycloak root.

1. Admin Console → top-left realm switcher → **Create Realm** (or select an existing one).
2. Give it a name (e.g. `mcp`). The issuer becomes `https://<keycloak-host>/realms/mcp`.

Do **not** use the `master` realm for applications.

---

## Step 2 — Create the OIDC client

1. Left nav → **Clients** → **Create client**.
2. **General settings**
   - Client type: **OpenID Connect**
   - Client ID: e.g. `mcp-server` (record this — becomes `--client-id`)
   - Name / Description: optional
3. **Capability config**
   - Client authentication: **On** (makes it a confidential client with a secret)
   - Authorization: Off (not required)
   - Authentication flow: check **Standard flow** (authorization code) and **Direct access grants** if you want password grants for testing. Leave **Service accounts roles** unchecked unless you need pure M2M.
4. **Login settings** — fill in redirect URIs (see Step 3). You can also edit these after creation.
5. **Save**.

---

## Step 3 — Configure redirect URIs and web origins

On the client's **Settings** tab:

- **Valid redirect URIs** (add all that apply):
  ```
  https://<mcp-server-host>/auth/callback
  https://claude.ai/api/mcp/auth_callback
  http://localhost:8888/callback
  http://localhost:8889/callback
  ```
- **Valid post logout redirect URIs**: same list, or `+` to inherit the redirect URIs.
- **Web origins**: `+` to allow CORS from any redirect URI origin, or list them explicitly.
- **Root URL** / **Home URL**: optional but helpful (`https://<mcp-server-host>`).

Avoid wildcards in production redirect URIs. Each one must exactly match what the client sends.

Save.

---

## Step 4 — Retrieve the client secret

1. Client → **Credentials** tab.
2. Client Authenticator: **Client Id and Secret**.
3. Copy the **Client secret** value. This becomes `--client-secret`.

Regenerate the secret here if it leaks; the old value stops working immediately.

---

## Step 5 — Add an Audience mapper (critical)

By default, Keycloak issues access tokens whose `aud` claim is the **client id**, not your MCP server's API identifier. The MCP server validates `aud` against its configured audience — without this mapper, every request fails with an audience mismatch.

1. Client → **Client scopes** tab.
2. Click the dedicated scope (usually named `<client-id>-dedicated`).
3. **Add mapper** → **By configuration** → **Audience**.
4. Fill in:
   - Name: `mcp-audience`
   - Included Client Audience: leave blank
   - **Included Custom Audience**: the exact value you will pass to `--audience` (e.g. `https://mcp-server.example.com/mcp`)
   - Add to access token: **On**
   - Add to ID token: Off (unless the MCP server validates ID tokens)
   - Add to lightweight access token: On (if your Keycloak version shows this toggle)
5. Save.

Verify: after a test login, decode the access token at https://jwt.io — the `aud` claim should contain the custom audience string.

---

## Step 6 — Create a test user (if the realm has none)

1. Left nav → **Users** → **Add user**.
2. Username: e.g. `alice`. Email verified: On.
3. Save → **Credentials** tab → **Set password**. Turn **Temporary** off for scripted logins.

Assign realm or client roles here if the MCP server expects specific roles.

---

## Step 7 — Run `setup-oidc`

From the directory where you want config files written:

```bash
mcp-base setup-oidc --provider keycloak \
  --issuer    https://<keycloak-host>/realms/<realm> \
  --audience  https://<mcp-server-host>/mcp \
  --client-id <client-id-from-step-2> \
  --client-secret <secret-from-step-4>
```

`setup-oidc` will:
- Hit `<issuer>/.well-known/openid-configuration` and write the real `authorization_endpoint`, `token_endpoint`, and `jwks_uri` into `oidc-config.json`.
- Print the redirect URIs you should have configured in Step 3 — use this as a checklist.
- Generate `oidc-values.yaml` (Helm values) in the current directory.

Pass `--skip-validation` if the CLI cannot reach Keycloak from its network. The endpoints will then be derived as `{issuer}/protocol/openid-connect/{auth,token,certs}`.

---

## Scripted alternative with `kcadm.sh`

For agents and CI, the Keycloak admin CLI is faster than clicking. Requires `kcadm.sh` (ships with Keycloak) and a `KC_*` env set.

```bash
# Authenticate kcadm against the master realm
kcadm.sh config credentials \
  --server "$KC_URL" --realm master \
  --user "$KC_ADMIN" --password "$KC_ADMIN_PASSWORD"

REALM=mcp
CLIENT_ID=mcp-server
AUDIENCE=https://mcp-server.example.com/mcp
MCP_HOST=https://mcp-server.example.com

# (1) Create the realm (skip if it exists)
kcadm.sh create realms -s realm="$REALM" -s enabled=true

# (2) Create the confidential client with redirect URIs + web origins
CLIENT_PK=$(kcadm.sh create clients -r "$REALM" -f - <<EOF | awk -F'[' '{print $2}' | tr -d ']'
{
  "clientId": "$CLIENT_ID",
  "protocol": "openid-connect",
  "publicClient": false,
  "standardFlowEnabled": true,
  "directAccessGrantsEnabled": true,
  "serviceAccountsEnabled": false,
  "redirectUris": [
    "$MCP_HOST/auth/callback",
    "https://claude.ai/api/mcp/auth_callback",
    "http://localhost:8888/callback",
    "http://localhost:8889/callback"
  ],
  "webOrigins": ["+"]
}
EOF
)

# (3) Read the generated client secret
CLIENT_SECRET=$(kcadm.sh get "clients/$CLIENT_PK/client-secret" -r "$REALM" --fields value | sed -n 's/.*"value" : "\([^"]*\)".*/\1/p')

# (4) Add the Audience mapper on the dedicated client scope
SCOPE_ID=$(kcadm.sh get client-scopes -r "$REALM" -q "name=${CLIENT_ID}-dedicated" --fields id --format csv --noquotes | tail -n1)
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

# (5) Hand off to setup-oidc
mcp-base setup-oidc --provider keycloak \
  --issuer "$KC_URL/realms/$REALM" \
  --audience "$AUDIENCE" \
  --client-id "$CLIENT_ID" \
  --client-secret "$CLIENT_SECRET"
```

The exact `kcadm.sh create clients … | awk` line depends on your shell/Keycloak version; on modern Keycloak, `-i` prints the new id to stdout and replaces the awk dance: `CLIENT_PK=$(kcadm.sh create clients -r "$REALM" -f body.json -i)`.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Invalid redirect_uri` at login | The redirect URI sent by the client is not an exact match of a **Valid redirect URI** on the Keycloak client. Wildcards only match one path segment. |
| MCP server rejects token with `aud` mismatch | Step 5 missing or Audience mapper not marked **Add to access token**. |
| `invalid_client` on token exchange | Wrong `--client-secret`, or client authentication is Off (public client). |
| `mcp-base setup-oidc` prints "Could not validate OIDC issuer" | The CLI's host cannot reach `{issuer}/.well-known/openid-configuration`. Verify DNS/TLS, or rerun with `--skip-validation` if the endpoints are known to be standard Keycloak paths. |
| Tokens missing custom claims the MCP server needs | Add additional protocol mappers on the dedicated client scope (role mapper, user-attribute mapper, etc.). |
