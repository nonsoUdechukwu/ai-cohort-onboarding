# PRD: AI Training Cohort Onboarding Portal

| Field | Value |
|---|---|
| Status | Draft v0.2 (open questions resolved) |
| Owner | Nonso |
| Date | 2026-10-03 |
| Hosting | Azure App Service, Free tier (F1) |

---

## 1. Summary

A small web app where students in the AI training cohort enter their email address. The app then:
1. Sends them a **Microsoft Entra ID B2B guest invitation** to the training tenant.
2. Adds the guest user to a **security group** for the cohort. The group holds Azure RBAC role assignments, so members can use the training resources in the **Azure portal**.

The target is a **dedicated test/training tenant**, which the owner will create. There is **one active cohort at a time**.

This replaces manual invitations by the instructor.

## 2. Problem statement

Onboarding each student by hand is slow and error-prone. The instructor has to invite the user in the Entra portal and then add them to the right group. With dozens of students per cohort this does not scale. Students also have to wait until the instructor is available.

## 3. Goals and non-goals

### Goals
- G1: Students can onboard themselves in under 1 minute.
- G2: Every successful submission ends with the student invited **and** in the cohort group.
- G3: Resubmitting the same email does no harm and causes no duplicate errors.
- G4: Strangers cannot misuse the portal to spam invitations.
- G5: The app runs on Azure App Service **Free (F1)** at no hosting cost.

### Non-goals (v1)
- Student sign-in or profiles inside the portal.
- Course content, grading or LMS features.
- Removing students automatically at the end of a cohort (see Future).
- Running multiple tenants.

## 4. Users and personas

| Persona | Needs |
|---|---|
| **Student** | Simple form, clear confirmation, knows what to do next (check email, accept the invite). |
| **Instructor/Admin** | Controls who can join (access code), picks the target group, sees who joined. |

## 5. User stories

1. As a student, I enter my email and the cohort access code so I get access to the training tenant.
2. As a student, I see a clear success message telling me to check my inbox and accept the invitation.
3. As a student who already joined, I can resubmit without an error and get the invite link again.
4. As an admin, I set the access code and target group through configuration, without code changes.
5. As an admin, I can see a log of submissions (email, timestamp, result).
6. As an admin, I want bots and outsiders blocked from triggering invitations.

## 6. Functional requirements

### 6.1 Student form (public page `/`)
| ID | Requirement |
|---|---|
| FR-1 | The form has these fields: **Email** (required), **Full name** (optional, used as the invite display name), and **Cohort access code** (required). |
| FR-2 | Email format is validated on the client and on the server. |
| FR-3 | A CAPTCHA (Cloudflare Turnstile, free) must pass before the form can be submitted. |
| FR-4 | On success, show: "Invitation sent to {email}. Check your inbox (and spam) for an email from Microsoft and accept it." Also show a direct **redeem link** returned by Graph. |
| FR-5 | On failure, show a friendly, generic error. Internal details are never shown. |

### 6.2 Backend processing (`POST /api/register`)
| ID | Requirement |
|---|---|
| FR-6 | Check the access code against the configured value using a constant-time comparison. |
| FR-7 | Gate sign-ups with the **access code only**. There is no email-domain restriction in v1. |
| FR-8 | Call Microsoft Graph `POST /invitations` with `invitedUserEmailAddress`, `inviteRedirectUrl` set to the **Azure portal for the tenant** (`https://portal.azure.com/<TENANT_ID>`), `sendInvitationMessage: true` and an optional custom message. |
| FR-9 | Take `invitedUser.id` from the response and call `POST /groups/{groupId}/members/$ref`. |
| FR-10 | **Idempotency:** if the user already exists, Graph returns the existing user. If they are already a group member (Graph returns 400 "already exist"), treat this as success. |
| FR-11 | Rate limit per IP, for example 5 requests per 10 minutes, and limit total invites per day. |
| FR-12 | Log every attempt: timestamp, email, outcome and Graph request-id. Do not log the access code. |

### 6.3 Admin (v1 minimal)
| ID | Requirement |
|---|---|
| FR-13 | All settings live in App Service **Application Settings**: tenant ID, client ID, client secret (or managed identity), group ID, access code, redirect URL, and Turnstile keys. Switching to a new cohort means updating `GROUP_ID` and `ACCESS_CODE`. |
| FR-14 | **In v1:** an `/admin` page protected with Entra ID sign-in (App Service "Easy Auth"). Only members of an admin group, or a configured list of admin UPNs, may access it. |
| FR-15 | The admin page lists submissions (newest first): timestamp, name, email, outcome (invited / already member / failed), error summary, and cohort/group ID. It can filter by outcome and export to CSV. |
| FR-16 | The admin page shows the current cohort config (group name, redirect URL). It never shows secrets or the access code. |
| FR-17 | The submission log is stored in **Azure Table Storage**, partitioned by cohort/group ID. |

## 7. Non-functional requirements

| Area | Requirement |
|---|---|
| Cost | $0 hosting on F1. Table Storage costs a few cents a month or less. |
| Performance | Form submission finishes in under 5 s at p95. Cold starts are acceptable because F1 has no Always On. |
| Security | HTTPS only. Secrets live in app settings and are never in the repo. Least-privilege Graph permissions. CAPTCHA, access code and rate limiting. |
| Availability | Best effort. F1 has no SLA and a 60 CPU-minute/day quota, which is plenty for one cohort. |
| Accessibility | Labelled inputs, keyboard navigable, mobile friendly. |
| Privacy | Store only email, name and timestamp. Show a short notice explaining how the email is used. |

## 8. Technical design (proposed)

### 8.1 Architecture
```
Student browser
   │  HTTPS (form + Turnstile token)
   ▼
Azure App Service (F1, Linux, Python 3.12, gunicorn + uvicorn worker)
   ├─ FastAPI: GET /            student form (Jinja2Templates)
   ├─ FastAPI: POST /api/register
   └─ FastAPI: GET /admin       (Easy Auth: Entra sign-in + admin check)
         │ client-credentials token (azure-identity)
         ├──────────────► Microsoft Graph
         │                  1. POST /invitations
         │                  2. POST /groups/{id}/members/$ref
         └──────────────► Azure Table Storage (submission log)
```

### 8.2 Stack
- **Runtime:** Python 3.12 + **FastAPI**, served by **gunicorn** with one `uvicorn.workers.UvicornWorker` (async; light enough for F1). Pydantic models for the register request/response and `pydantic-settings` for env config.
- **Auth to Graph:** async `azure-identity` (`azure.identity.aio` `ClientSecretCredential` or `ManagedIdentityCredential`) with `httpx.AsyncClient` calls to Graph REST (Turnstile verification also uses `httpx`).
- **Storage:** `azure-data-tables` for the submission log (sync client run in a threadpool; in-memory fallback for local dev).
- **Rate limiting:** a small in-memory per-IP sliding-window limiter (fine because F1 runs a single instance and one worker).
- **Frontend:** Jinja templates + minimal CSS + vanilla JS. No build step.
- **Admin auth:** App Service Authentication (Easy Auth) with Entra ID. The app reads the `X-MS-CLIENT-PRINCIPAL` header and checks the admin list or group.
- **Tests:** `pytest` with FastAPI `TestClient` and `respx`, with Graph, Turnstile and Table calls mocked.
- **Repo/CI/CD:** a **new GitHub repo** will be created. A GitHub Actions workflow builds and deploys with `azure/webapps-deploy` using OIDC (federated credential, no publish-profile secret).
- **IaC:** a Bicep template or `az` CLI script for the App Service plan (F1), Web App, Storage account and app settings.

### 8.3 Entra ID setup
1. Create an **app registration** in the training tenant, for example "Cohort Onboarding Portal".
2. Grant **Application** permissions and admin consent:
   - `User.Invite.All`: create guest invitations.
   - `GroupMember.ReadWrite.All`: add members to groups.
3. Create a client secret (rotate every 6 months or less), or assign the same permissions to the Web App's **system-assigned managed identity** with PowerShell/Graph. Managed identity is preferred because there is no secret to store.
4. Create a security group, for example "AI Cohort 2026-Q4", and record its Object ID.
5. **Azure portal access:** assign the cohort group an Azure RBAC role (for example *Contributor* or *Reader*) on a training subscription or resource group. Without a role assignment, guests land in an empty portal.
6. Check **External collaboration settings**: guest invites must be allowed and the domain allow/deny list must permit student domains.
7. Create an **admin group** (or list admin UPNs) for `/admin` access, and enable Easy Auth on the Web App, which needs its own app registration.

### 8.4 Configuration (App Settings)
| Key | Example |
|---|---|
| `TENANT_ID` | `xxxxxxxx-...` |
| `CLIENT_ID` | `xxxxxxxx-...` |
| `CLIENT_SECRET` | *(omit if using managed identity)* |
| `GROUP_ID` | `xxxxxxxx-...` |
| `ACCESS_CODE` | `AI-COHORT-Q4` |
| `INVITE_REDIRECT_URL` | `https://portal.azure.com/<TENANT_ID>` |
| `STORAGE_CONNECTION_STRING` / `TABLE_NAME` | Table Storage for the log (`submissions`) |
| `ADMIN_GROUP_ID` or `ADMIN_UPNS` | Who can open `/admin` |
| `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY` | from Cloudflare |
| `INVITE_MESSAGE` | "Welcome to the AI training cohort!" |

### 8.5 F1 tier constraints and mitigations
| Constraint | Impact | Mitigation |
|---|---|---|
| 60 CPU min/day, 1 GB RAM | Fine for low traffic | Keep the app light; no heavy frameworks |
| No Always On | First request after idle can take about 10–30 s | Show a loading spinner; acceptable for this use case |
| No custom domain SSL | Uses `*.azurewebsites.net` | Acceptable; upgrade to B1 if a custom domain is needed |
| No deployment slots or autoscale | Deploys cause brief downtime | Deploy outside cohort sign-up windows |

## 9. Success metrics
- At least 95% of cohort students onboarded through the portal with no manual intervention.
- Zero invitations sent to emails outside the cohort (no abuse).
- Hosting cost: $0.

## 10. Risks
| Risk | Mitigation |
|---|---|
| Access code leaks and outsiders join | Rotate the code per cohort, review group membership on the admin page, and enforce daily invite caps |
| Highly privileged app permissions | Dedicated training tenant; managed identity; secret rotation |
| Invite emails land in spam | Show the redeem link on screen; tell students to check spam |
| Tenant external collaboration policy blocks some domains | Document the settings; show a clear error |

## 11. Milestones
| # | Milestone | Deliverable |
|---|---|---|
| M1 | PRD sign-off | This document |
| M2 | Tenant + Entra setup | Test tenant, app registration, permissions, cohort and admin groups, RBAC role for the cohort group |
| M3 | MVP app (Python/FastAPI) | Form + `/api/register` + Turnstile + rate limit + Table logging, with pytest |
| M4 | Admin page | Easy Auth–protected `/admin` with submission list and CSV export |
| M5 | Repo + Deploy | New GitHub repo, Bicep/CLI infra, F1 Web App, GitHub Actions (OIDC) |
| M6 | Pilot | Test with 2–3 users, then open to the cohort |

## 12. Future enhancements
- Several cohorts at the same time, chosen by access code (code maps to group).
- Email-domain allow-list.
- Remove guests automatically at the end of a cohort, or use Entra Access Reviews.
- Notify the instructor in Teams/email for each new joiner.
- Use Entra **Entitlement Management access packages** as the more "native" alternative (needs P2/Governance licensing).

## 13. Decisions log
| # | Question | Decision |
|---|---|---|
| 1 | Target tenant | A **dedicated test tenant**, to be created by the owner |
| 2 | Cohorts | **One at a time**, switched through `GROUP_ID` + `ACCESS_CODE` |
| 3 | Sign-up gate | **Access code** (plus CAPTCHA and rate limiting) |
| 4 | Post-accept landing | **Azure portal** (`https://portal.azure.com/<TENANT_ID>`) |
| 5 | Admin page and log | **Yes, in v1** (Easy Auth + Table Storage) |
| 6 | Language | **Python** (FastAPI + gunicorn/uvicorn; migrated from Flask) |
| 7 | Repo | **New GitHub repo to be created**; CI/CD through GitHub Actions |

### Remaining minor questions
- Which Azure RBAC role and scope should the cohort group get (for example Contributor on one resource group)?
- What GitHub repo name and owner (personal or org) should be used? Public or private?
- Is Cloudflare Turnstile OK for the CAPTCHA? It is free and needs a Cloudflare account.
