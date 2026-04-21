# Keycloak How-To

> **Compatibility:** Keycloak support in [FastMCP](https://github.com/jlowin/fastmcp) (the framework the MCP server is built with) was added in **FastMCP 3.2.4**, and only works against **Keycloak 26.6.0 or newer**. Both constraints must hold simultaneously. If either side is below these minimums, use the Pattern A fallback at the end of this doc (`--provider generic` with a Keycloak issuer) instead.

`mcp-base setup-oidc --provider keycloak` targets **Pattern B** (Remote DCR) as defined in [`imp/cli-integration-contract.md`](imp/cli-integration-contract.md) §1. FastMCP's `KeycloakAuthProvider` registers MCP clients dynamically through Keycloak's native Dynamic Client Registration — **you do not pre-create a client, there is no client secret to copy, and no Kubernetes credentials Secret is created**. `create-secrets` is a no-op for this pattern.

At the end of this procedure you will have two values to pass to `setup-oidc`:

| `setup-oidc` flag | Keycloak source |
|---|---|
| `--issuer` | `https://<keycloak-host>/realms/<realm>` (realm URL, not the Keycloak root) |
| `--audience` | MCP server API identifier — must appear in issued tokens' `aud` claim (see Step 3) |

---

## How the flow works

The MCP server publishes protected-resource metadata at:

```
https://<mcp-server-host>/.well-known/oauth-protected-resource/mcp
```

Expected response:

```json
{
  "resource": "https://<mcp-server-host>/mcp",
  "authorization_servers": ["https://<keycloak-host>/realms/<realm>"],
  "scopes_supported": ["openid", "mcp-scope"],
  "bearer_methods_supported": ["header"]
}
```

`scopes_supported` **must include `mcp-scope`**. MCP clients read this metadata and request `openid mcp-scope` during OAuth. Keycloak runs the Audience mapper attached to `mcp-scope`, which stamps the `aud` claim that FastMCP validates. If the server advertises only `openid`, clients never request `mcp-scope`, the Audience mapper never fires, and FastMCP rejects every token with `audience mismatch (got None, expected '...')`.

---

## Prerequisites

- **MCP server built on FastMCP ≥ 3.2.4.** FastMCP is a framework, not a server you run directly — check the MCP server project's `pyproject.toml` / lockfile.
- **Keycloak ≥ 26.6.0.** FastMCP's Keycloak support requires DCR features introduced in this release.
- Admin credentials for the master realm (or equivalent permissions on the target realm).
- The MCP server's public base URL and its API audience identifier (typically the MCP URL ending in `/mcp`, e.g. `https://mcp-server.example.com/mcp`).

---

## Step 1 — Pick or create a realm

1. Admin Console → top-left realm switcher → **Create Realm** (or select an existing one).
2. Give it a name (e.g. `mcp`). The issuer becomes `https://<keycloak-host>/realms/mcp`.

Do **not** use the `master` realm for applications.

---

## Step 2 — Create the `mcp-scope` client scope

`mcp-scope` is a real Keycloak client scope object. It carries the Audience mapper that stamps `aud` claims in tokens, and it is what triggers the mapper when clients request it during OAuth.

> **Note on `openid`:** `openid` is an OAuth/OIDC *protocol scope string*, not a Keycloak client scope object. Do not create a Keycloak client scope named `openid`. Keycloak may log `Referenced client scope 'openid' doesn't exist. Ignoring` — that is expected and harmless. The DCR registration policy still requires the literal string `openid` in its allowed-scopes list (covered in Step 3).

### Create the scope

1. Realm → **Client scopes** (left nav) → **Create client scope**.
2. Settings:
   - Name: `mcp-scope`
   - Protocol: `openid-connect`
   - Include in token scope: **ON**
3. Save.

### Add the Audience mapper

4. Open `mcp-scope` → **Mappers** tab → **Add mapper → By configuration → Audience**.
5. Fill in:
   - Name: `mcp-audience`
   - Included Client Audience: leave blank
   - **Included Custom Audience**: the exact value you will pass to `--audience` (e.g. `https://mcp-server.example.com/mcp`)
   - Add to access token: **On**
   - Add to ID token: **Off**
6. Save.

### Make the scope a realm default

7. Realm → **Client scopes** → find `mcp-scope` → set **Assigned type** to **Default**.

DCR-registered clients inherit default scopes automatically. Without this, newly registered MCP clients won't have `mcp-scope` and the audience mapper will never run.

---

## Step 3 — Configure Dynamic Client Registration policies

1. Realm → **Clients → Client registration** (left nav) → **Policies** tab.
2. Click **Anonymous Access Policies**.

### Trusted Hosts policy

Keycloak's Trusted Hosts policy checks both the sender host and the redirect URIs of registering clients. Cloud MCP clients (Claude, ChatGPT, etc.) register from hosts that Keycloak can't predict in advance.

Add the callback domains used by the MCP clients you support:

```
chatgpt.com
claude.ai
```

Policy shape that works for cloud MCP clients:

| Check | Setting |
|---|---|
| Host sending client registration request must match | **OFF** |
| Client URIs must match | **ON** |

If Keycloak requires keeping at least one host-based check, disable sender-host matching and keep only the client URI check. Enabling sender-host matching against ingress or service-mesh traffic will cause spurious rejections like `Failed to verify remote host: 10.233.90.0`.

### Allowed Client Scopes policy

DCR will fail with `Requested scope 'openid' not trusted` if `openid` is not in the allowed list, even though `openid` is not a real Keycloak client scope object — the policy validates the requested scope *strings*.

Add both:

```
openid
mcp-scope
```

---

## Step 4 — Create a test user

1. Left nav → **Users** → **Add user**.
2. Set username, email verified: **On**. Save.
3. **Credentials** tab → **Set password**. Turn **Temporary** off.

---

## Step 5 — Run `setup-oidc`

From the directory where you want config files written:

```bash
mcp-base setup-oidc --provider keycloak \
  --issuer   https://<keycloak-host>/realms/<realm> \
  --audience https://<mcp-server-host>/mcp
```

No `--client-id` / `--client-secret` are needed. Any that are passed are ignored with a warning and not written to `oidc-config.json`.

`setup-oidc` writes:
- `oidc-config.json` with `"provider": "keycloak"`, `"pattern": "remote"`, no `server_client` block, and OIDC endpoints from discovery.
- `oidc-values.yaml` for the Helm chart:

```yaml
oidc:
  authType: "keycloak"
  issuer: "https://<keycloak-host>/realms/<realm>"
  audience: "https://<mcp-server-host>/mcp"
  requiredScopes: ["openid", "mcp-scope"]

redis:
  enabled: false

jwt:
  enabled: false
```

`requiredScopes: ["openid", "mcp-scope"]` drives the `scopes_supported` list in the MCP server's protected-resource metadata. Both scopes must be present for the audience mapper to fire.

Pass `--skip-validation` if the CLI host cannot reach Keycloak; endpoint paths fall back to `{issuer}/protocol/openid-connect/{auth,token,certs}`.

---

## Step 6 — Skip `create-secrets`

For Pattern B this command is a no-op:

```
$ mcp-base create-secrets --namespace mcp --release-name my-mcp
Pattern B detected (Keycloak native DCR).
No Kubernetes secrets are required — FastMCP's KeycloakAuthProvider
registers clients dynamically and verifies tokens against JWKS.
Nothing to do. Exiting.
```

The command exits `0` without contacting the Kubernetes API — safe to leave in a pipeline.

---

## Step 7 — Verify

After deploying the MCP server, check the protected-resource metadata:

```bash
curl https://<mcp-server-host>/.well-known/oauth-protected-resource/mcp
```

Expected:

```json
{
  "resource": "https://<mcp-server-host>/mcp",
  "authorization_servers": ["https://<keycloak-host>/realms/<realm>"],
  "scopes_supported": ["openid", "mcp-scope"],
  "bearer_methods_supported": ["header"]
}
```

Then reconnect an MCP client and decode the issued access token. Expected claims:

```json
{
  "iss": "https://<keycloak-host>/realms/<realm>",
  "aud": "https://<mcp-server-host>/mcp",
  "scope": "openid mcp-scope"
}
```

If `aud` is missing, work through the audience-mismatch checklist in the Troubleshooting section below.

---

## Scripted alternative with `kcadm.sh`

```bash
kcadm.sh config credentials \
  --server "$KC_URL" --realm master \
  --user "$KC_ADMIN" --password "$KC_ADMIN_PASSWORD"

REALM=mcp
AUDIENCE=https://mcp-server.example.com/mcp

# (1) Create the realm
kcadm.sh create realms -s realm="$REALM" -s enabled=true

# (2) Create mcp-scope with Include in token scope enabled
SCOPE_ID=$(kcadm.sh create client-scopes -r "$REALM" -f - -i <<EOF
{
  "name": "mcp-scope",
  "protocol": "openid-connect",
  "attributes": { "include.in.token.scope": "true" }
}
EOF
)

# (3) Add the Audience mapper to mcp-scope
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

# (4) Make mcp-scope a realm default so DCR clients inherit it
kcadm.sh update "realms/$REALM/default-default-client-scopes/$SCOPE_ID" -r "$REALM"

# (5) Allow openid and mcp-scope in anonymous DCR allowed-scopes policy
# (exact kcadm path varies by Keycloak version; check UI if this errors)
kcadm.sh update "realms/$REALM" -r "$REALM" \
  -s 'clientRegistrationPolicy.Allowed Client Scopes.allowedScopes=["openid","mcp-scope"]'

# (6) Hand off to setup-oidc
mcp-base setup-oidc --provider keycloak \
  --issuer   "$KC_URL/realms/$REALM" \
  --audience "$AUDIENCE"
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| DCR fails — `URI '…' doesn't match any trustedHost or trustedDomain` | Callback host not in Trusted Hosts | Add `chatgpt.com`, `claude.ai`, or the exact callback host to Trusted Hosts |
| DCR fails — `Failed to verify remote host: 10.x.x.x` | Sender-host check is matching an internal/proxy IP | Disable **Host sending client registration request must match** |
| DCR fails — `Requested scope 'openid' not trusted` | `openid` not in Allowed Client Scopes policy | Add the literal string `openid` to the policy's allowed list |
| DCR fails — `Not permitted to use specified clientScope` | `mcp-scope` not in Allowed Client Scopes policy | Add `mcp-scope` to the policy's allowed list |
| Token rejected — `audience mismatch (got None, expected '...')` | `mcp-scope` mapper never ran (scope not requested or mapper not firing) | Verify: `mcp-scope` is a realm default scope; `requiredScopes: ["openid", "mcp-scope"]` in Helm values; `mcp-scope` has the Audience mapper; **Include in token scope: ON**; MCP client was force-reconnected after scope changes |
| Token missing `mcp-scope` in `scope` claim | `Include in token scope` is OFF for `mcp-scope` | Turn it **ON** on the `mcp-scope` client scope |
| Keycloak logs `Referenced client scope 'openid' doesn't exist. Ignoring` | `openid` is a protocol scope string, not a Keycloak client scope object | Expected/harmless — do not create a Keycloak client scope named `openid` |
| MCP server logs show `unsupported provider` | FastMCP version < 3.2.4 | Upgrade the MCP server's FastMCP dependency |
| Server accepts tokens but claims are malformed | Keycloak < 26.6.0 DCR format incompatibility | Upgrade Keycloak |
| `mcp-base setup-oidc` prints "Could not validate OIDC issuer" | CLI host cannot reach `{issuer}/.well-known/openid-configuration` | Check DNS/TLS; rerun with `--skip-validation` |

### Audience-mismatch checklist

When FastMCP logs `audience mismatch (got None, expected '...')`, check all of these in order:

1. MCP protected-resource metadata advertises `mcp-scope` in `scopes_supported`.
2. `requiredScopes: ["openid", "mcp-scope"]` is set in the MCP server's Helm values.
3. Keycloak DCR Allowed Client Scopes includes both `openid` and `mcp-scope`.
4. `mcp-scope` has the Audience mapper with the correct custom audience value.
5. `mcp-scope` has **Include in token scope: ON**.
6. `mcp-scope` is a **Default** realm scope so DCR-registered clients inherit it.
7. The MCP client was removed and re-added (or forced through a fresh OAuth flow) after the scope configuration changed — clients may cache old grants.

---

## Appendix — Pattern A fallback (older Keycloak, or pre-registered client)

If your Keycloak is older than 26.6.0, or your FastMCP is older than 3.2.4, or you prefer a pre-registered confidential client, use **Pattern A** instead by running `setup-oidc --provider generic` (not `keycloak`) against the Keycloak realm. The CLI treats it like any other OIDC provider: client secret required, Redis + JWT signing-key Secrets created.

### A.1 — Create the confidential client

1. Realm → **Clients** → **Create client**.
2. Client type: **OpenID Connect**, Client ID: e.g. `mcp-server`. Client authentication: **On**. Standard flow: **On**.
3. **Valid redirect URIs**:
   ```
   https://<mcp-server-host>/auth/callback
   https://claude.ai/api/mcp/auth_callback
   http://localhost:8888/callback
   http://localhost:8889/callback
   ```
4. Save → **Credentials** tab → copy the **Client secret**.

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
