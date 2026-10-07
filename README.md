# AI Cohort Onboarding Portal

Self-service portal for AI training cohort students: a student enters their email and the cohort
access code, and the app

1. sends a **Microsoft Entra ID B2B guest invitation** to the training tenant, and
2. adds the guest to the **cohort security group**, which holds the Azure RBAC role assignments
   that let students use the training resources in the Azure portal.

Instructors get an Easy Auth–protected `/admin` page with the submission log and a CSV export.
See [docs/PRD.md](docs/PRD.md) for the full requirements.

- **Stack:** Python 3.12, FastAPI (+ Pydantic / pydantic-settings), gunicorn with uvicorn workers,
  `httpx` (async), `azure-identity` (async credentials), `azure-data-tables`, Jinja + vanilla JS
  (no build step).
- **Hosting:** Azure App Service Linux **Free (F1)** + Table Storage.

```
Student ──HTTPS──► App Service (F1, gunicorn/uvicorn + FastAPI)
                     ├─ GET  /               form + Cloudflare Turnstile
                     ├─ POST /api/register   captcha → access code → daily cap → Graph
                     ├─ GET  /admin(.csv)    Easy Auth + admin UPN/group check
                     ├──► Microsoft Graph: POST /invitations, POST /groups/{id}/members/$ref
                     └──► Azure Table Storage: submission log (PartitionKey = GROUP_ID)
```

## Repository layout

| Path | Purpose |
|---|---|
| `portal/` | FastAPI app (`config`, `schemas`, `graph`, `turnstile`, `storage`, `auth`, `ratelimit`, `middleware`, `routes`, templates, static) |
| `asgi.py`, `gunicorn.conf.py` | ASGI entry point; one async uvicorn worker (keeps in-memory rate limits consistent) |
| `tests/` | pytest suite (FastAPI `TestClient`, `respx`); Graph, Turnstile and Table Storage are mocked |
| `infra/main.bicep` | F1 Linux plan, Web App (Python 3.12, HTTPS only, managed identity), Storage + table, app settings, optional Easy Auth |
| `scripts/` | Infra deploy, Graph permission grant (bash + PowerShell), GitHub OIDC setup |
| `.github/workflows/deploy.yml` | Run tests, then deploy to the Web App with OIDC |

## Configuration

All configuration comes from environment variables (App Service **Application settings**). See
[`.env.example`](.env.example).

| Setting | Required | Notes |
|---|---|---|
| `TENANT_ID` | yes | Training tenant ID |
| `CLIENT_ID` / `CLIENT_SECRET` | only for secret auth | If `CLIENT_SECRET` is empty the app uses the Web App's **managed identity** |
| `MANAGED_IDENTITY_CLIENT_ID` | no | Only for a *user-assigned* managed identity |
| `GROUP_ID` | yes | Cohort group object ID |
| `ACCESS_CODE` | yes | Compared in constant time; never logged or displayed |
| `INVITE_REDIRECT_URL` | no | Default `https://portal.azure.com/<TENANT_ID>` |
| `INVITE_MESSAGE` | no | Custom text in the invitation email |
| `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY` | yes | From Cloudflare Turnstile |
| `STORAGE_CONNECTION_STRING` | yes in Azure | Empty = in-memory log (local dev only) |
| `TABLE_NAME` | no | Default `submissions` |
| `ADMIN_UPNS` | one of | Comma-separated UPNs allowed on `/admin` |
| `ADMIN_GROUP_ID` | one of | Entra group allowed on `/admin` (needs the groups claim) |
| `DAILY_INVITE_CAP` | no | Default `100` invites per UTC day; `0` = unlimited |
| `REGISTER_RATE_LIMIT` | no | Default `5 per 10 minutes` per client IP (`N per [M] second/minute/hour/day` or `N/minute`) |
| `LOCAL_DEV_ADMIN` | no | `true` bypasses `/admin` auth **locally only**; ignored on App Service |

## Local development

```bash
# Python 3.12 recommended (e.g. `brew install python@3.12`); code also runs on 3.9+.
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env        # edit values; the Turnstile test keys in it always pass
pytest                      # run the test suite
uvicorn asgi:app --reload --env-file .env   # http://127.0.0.1:8000
```

- With `LOCAL_DEV_ADMIN=true`, `/admin` works without Easy Auth.
- Without `STORAGE_CONNECTION_STRING` submissions are kept in memory. To test against real tables
  locally, run [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite) and use
  `UseDevelopmentStorage=true`.
- Real invitations need Graph credentials. Locally use an app registration with `CLIENT_SECRET`
  in a **test tenant** only.
- Production-like run: `gunicorn --config gunicorn.conf.py -k uvicorn.workers.UvicornWorker asgi:app`
  (port 8000).

## Entra ID / tenant setup

Do all of this in the **dedicated training tenant**.

1. **Tenant and subscription.** Create the test tenant and link an Azure subscription to it.
   The managed identity only gets tokens for the tenant that owns the subscription hosting the
   Web App. If the Web App runs in a different tenant, use an app registration in the training
   tenant with `CLIENT_ID` + `CLIENT_SECRET` instead.
2. **Graph identity (choose one).**
   - *Managed identity (recommended):* deploy the infra (below), then grant the Web App's
     system-assigned identity the Graph app roles:
     ```bash
     az login --tenant <TENANT_ID> --allow-no-subscriptions
     ./scripts/grant-graph-permissions.sh <managedIdentityPrincipalId>
     # or: ./scripts/grant-graph-permissions.ps1 -TenantId <id> -ManagedIdentityPrincipalId <id>
     ```
   - *App registration:* create "Cohort Onboarding Portal", add the **Application** permissions
     `User.Invite.All` and `GroupMember.ReadWrite.All`, grant admin consent, create a client secret
     (rotate within 6 months), and set `CLIENT_ID` / `CLIENT_SECRET`.
3. **Cohort group.** Create a security group (for example "AI Cohort 2026-Q4") and set its object
   ID as `GROUP_ID`.
4. **Azure RBAC for the cohort.** Give the group a role on the training scope, otherwise guests
   land in an empty portal:
   ```bash
   az role assignment create --assignee-object-id <GROUP_ID> --assignee-principal-type Group \
     --role Reader --scope /subscriptions/<sub>/resourceGroups/<training-rg>
   ```
5. **External collaboration settings** (Entra admin center → External Identities → External
   collaboration settings): guest invitations must be allowed, and any domain allow/deny list must
   permit student domains.
6. **Admins.** Set `ADMIN_UPNS` and/or create an admin group and set `ADMIN_GROUP_ID`.
7. **Easy Auth for `/admin`.**
   1. Create an app registration "Cohort Portal Admin Sign-in" with redirect URI
      `https://<app>.azurewebsites.net/.auth/login/aad/callback` (Web platform) and enable
      **ID tokens**.
   2. If you use `ADMIN_GROUP_ID`, set *Token configuration → Add groups claim → Security groups*
      (groups claim emitted as object IDs). Users in more than 200 groups get no groups claim; use
      `ADMIN_UPNS` for them.
   3. Create a client secret, then either pass `easyAuthClientId` / `EASY_AUTH_CLIENT_SECRET` to the
      Bicep deployment, or in the portal: Web App → **Authentication** → Add identity provider →
      Microsoft → existing app registration, and set **Unauthenticated requests: Allow
      unauthenticated access** (the student form must stay public).
   4. The app redirects unauthenticated `/admin` visitors to `/.auth/login/aad`. On App Service it
      only trusts `X-MS-CLIENT-PRINCIPAL` when Easy Auth is enabled (`WEBSITE_AUTH_ENABLED`), so the
      header cannot be forged.
8. **Cloudflare Turnstile.** Create a widget for `<app>.azurewebsites.net` and set the site and
   secret keys.

## Deploy to Azure

### 1. Infrastructure (Bicep)

```bash
cp infra/main.parameters.example.json infra/main.parameters.local.json   # git-ignored; edit it
az login --tenant <TENANT_ID>
ACCESS_CODE='...' TURNSTILE_SECRET_KEY='...' EASY_AUTH_CLIENT_SECRET='...' \
  ./scripts/deploy-infra.sh rg-ai-cohort westeurope infra/main.parameters.local.json
```

This creates the F1 Linux plan, the Web App (Python 3.12, HTTPS only, gunicorn + uvicorn worker startup command,
system-assigned managed identity), the Storage account and `submissions` table, and the app
settings. The output includes `managedIdentityPrincipalId` for step 2 of the Entra setup.

> Each Bicep deployment **replaces all app settings**. Pass the secure values again every time, or
> change settings later with `az webapp config appsettings set` instead of re-deploying Bicep.
> An F1 plan has no Always On, so the first request after idle can take 10–30 s.

### 2. GitHub Actions (OIDC, no secrets)

```bash
./scripts/setup-github-oidc.sh nonsoUdechukwu/ai-cohort-onboarding rg-ai-cohort main
```

Then add repository **variables** `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` and
`AZURE_WEBAPP_NAME`. Every push to `main` runs `pytest`, zips the app, deploys it with
`azure/webapps-deploy` (Oryx installs `requirements.txt`) and checks `/healthz`. Pull requests
only run the tests. The deploy job is skipped until `AZURE_WEBAPP_NAME` is set.

## Switching to a new cohort

1. Create the new cohort group and give it the RBAC role assignment.
2. Update the app settings (the app restarts automatically):
   ```bash
   az webapp config appsettings set -g rg-ai-cohort -n <app> \
     --settings GROUP_ID=<new-group-id> ACCESS_CODE='<new-code>'
   ```
3. Share the new access code with the students. Old submissions stay in the table under the old
   group ID; tick **All cohorts** on `/admin` to see them.

## Security notes

- The access code is compared with `hmac.compare_digest` and is never logged, stored or displayed.
- Users only see generic errors; details (Graph error code, `request-id`) go to the server log and
  the submission table.
- Abuse protection: Turnstile, access code, per-IP rate limit, and `DAILY_INVITE_CAP` (counts
  `invited` + `already_member` submissions per UTC day).
- Rate limits are kept in memory. This is correct because F1 runs one instance and gunicorn uses
  one uvicorn worker (`WEB_CONCURRENCY=1`). If you add workers or scale out, move the limiter
  (`portal/ratelimit.py`) to a shared store (for example Redis).
- The client IP is the last `X-Forwarded-For` hop (set by the App Service front end, `:port`
  stripped), like the previous werkzeug `ProxyFix(x_for=1)` setup.
- `User.Invite.All` and `GroupMember.ReadWrite.All` are powerful; keep them in the dedicated training
  tenant and prefer the managed identity over a client secret.
