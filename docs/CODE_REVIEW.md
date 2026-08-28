# RearVue — code review (priority-ordered work items)

This report is organized as actionable todo lists: **P0** (address before trusting production), **P1** (correctness / broken features), **P2** (security hardening, reliability, ops), **P3** (maintainability / debt). Each item includes enough context to execute without re-auditing the whole tree.

**Scope:** Django app under `src/` (Django 6.x per `requirements.txt`), services in `src/rvservices/`, public site `rvsite`, admin UI `rvadmin`. External config is imported as `rearvue.settings_server` (not present in this repo).

---

## P0 — Critical security and safety

- [x] **Disable global unverified SSL in `update_content`.**  
  **Where:** `src/rvsite/management/commands/update_content.py` (start of `handle`).  
  **Issue:** `ssl._create_default_https_context = ssl._create_unverified_context` disabled verification for the whole process.  
  **Done:** Monkey-patch removed. CA bundle: `rearvue/__init__.py` sets `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` from certifi when unset; README documents overrides.

- [x] **Reconcile Instagram: single Graph + Instagram Login pipeline.**  
  **Where:** `src/rvservices/instagram_graph_service.py` (API fetch, mirror, `update_instagram`, `fix_instagram_item`), `src/rvservices/instagram_oauth.py`, `src/rvadmin/views.py` (OAuth), migration `0013` (token fields + `auth_token` TextField), `reset_instagram_graph` management command.  
  **Done:** Instaloader and `facebook-business` removed from the ingestion path; `auth_token` stores long-lived Instagram user token; optional destructive reset via `reset_instagram_graph --confirm`.

- [x] **Fix Dependabot configuration.**  
  **Where:** `.github/dependabot.yml`.  
  **Issue:** `package-ecosystem: ""` was invalid/empty; updates did not run.  
  **Done:** `package-ecosystem: pip` and `directory: "/src"` so Dependabot sees `src/requirements.txt`. Re-run `pip-compile` after merging Dependabot PRs if you keep `requirements.in` as the source of truth.

- [ ] **Revisit stored HTML and `|safe` on captions.**  
  **Where:** `src/rvsite/templates/rvsite/item_detail.html` (`{{ item.display_caption|safe }}`). Twitter import injects HTML in `src/rvservices/twitter_service.py` (`import_archive`).  
  **Issue:** Any future source of captions (malicious archive, compromised feed, HTML in RSS `body`) becomes stored HTML executed for all visitors. For a “personal nostalgia” app this may be acceptable risk, but it should be explicit.  
  **Work:** Prefer sanitization (e.g. bleach with an allowlist matching what you emit), or store separate `caption_plain` / `caption_html` with a clear pipeline. If you keep `|safe`, document the trust model and sanitize at ingest for untrusted sources.

---

## P1 — Broken or inconsistent behavior

- [x] **Flickr service type mismatch (`"Flickr"` vs `"flickr"`).**  
  **Where:** `src/rvadmin/views.py` created `type="Flickr"` while `src/rvservices/flickr_service.py` filters `type="flickr"`.  
  **Done:** New services use `type="flickr"`; migration `0012_normalize_flickr_service_type` updates existing `"Flickr"` rows.

- [x] **README references missing doc.**  
  **Where:** `README.md` linked to absent `INSTAGRAM_MIGRATION.md`.  
  **Done:** Recent Updates bullet now summarizes Instagram without that link and points to code paths.

- [x] **WSGI module header is wrong project name.**  
  **Where:** `src/rearvue/wsgi.py` had “dogthing project”.  
  **Done:** Docstring names rearvue; deployment link points at Django stable WSGI docs.

---

## P2 — Security hardening, performance, operational risk

- [ ] **Harden production Django settings (likely in `settings_server`).**  
  **Where:** `src/rearvue/settings.py` pulls `DEBUG`, `ALLOWED_HOSTS`, secrets from `settings_server`; middleware list has no `SecurityMiddleware` and no visible `SECURE_*` cookie/redirect/HSTS flags in-repo.  
  **Work:** For production, enable `SecurityMiddleware`, set `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, and HSTS as appropriate behind HTTPS. Ensure `ALLOWED_HOSTS` is explicit. Document required `settings_server` keys in README for new environments.

- [x] **Side-effecting admin action over GET (`fix_item`).**  
  **Where:** `src/rvadmin/views.py`, `src/rvsite/templates/rvsite/item.html`.  
  **Done:** `@require_POST` on `fix_item`; item page uses POST form with CSRF + submit button.

- [ ] **SSR F and outbound fetch consistency.**  
  **Where:** `src/rearvue/utils.py` implements `validate_public_http_url` for `make_link` / `final_destination`. `src/rvservices/rss_service.py` uses `requests.get(e.href, ...)` for enclosures without the same validation. Instagram/Twitter mirroring use hard-coded or platform URLs (lower risk than arbitrary RSS). `find_rss_links` uses `final_destination` but `requests.get(p.image)` without re-validating image URL (partially addressed in `make_link`).  
  **Work:** Route all untrusted URL fetches through a single helper that validates scheme/host and optionally resolves redirects safely, matching `validate_public_http_url` rules.

- [ ] **Expensive random ordering on year view.**  
  **Where:** `src/rvsite/views.py` `show_year` uses `order_by("?")`.  
  **Issue:** On large tables this becomes a full scan / temp table in many databases.  
  **Work:** Replace with deterministic sampling (e.g. bucket by month + random IDs with limits, or precompute “featured” items).

- [ ] **Secrets in the database.**  
  **Where:** `RVService.auth_token` / `auth_secret` (Flickr, Instagram OAuth), `extra_data` (e.g. legacy blobs).  
  **Work:** At minimum encrypt-at-rest using `django-fernet-fields` or custom encryption with a key from env. Document key rotation. Never log tokens (audit `print` statements in services).

- [ ] **Production dependency hygiene.**  
  **Where:** `src/requirements.in` includes `django-debug-toolbar`, `PdbBBEditSupport`, `safety`, `bandit` alongside runtime packages.  
  **Work:** Split `requirements-dev.in` / `requirements.txt` vs production pins so production images do not install debug tooling unless intended.

- [x] **`fixssl.py` mutates system OpenSSL paths via symlink.**  
  **Where:** `src/fixssl.py` (removed).  
  **Done:** Use env-based bundles (see README) and certifi bootstrap in `rearvue/__init__.py` when vars are unset.

---

## P3 — Maintainability, testing, and polish

- [ ] **Add meaningful automated tests.**  
  **Where:** `src/rvsite/tests.py`, `src/rvadmin/tests.py` are placeholders.  
  **Work:** Start with model/service unit tests (Flickr `type`, Instagram field semantics after refactor), URL/view tests for public 404 on unknown host (`page` decorator), and admin permission tests (`admin_page` superuser gate).

- [ ] **Replace bare `except` / `print` in hot paths with logging.**  
  **Where:** Throughout `src/rvservices/*.py` and `src/rvsite/models.py` (`RVItem.thumbnail` property).  
  **Work:** Use module loggers (`logging.getLogger(__name__)`), catch specific exceptions, and log context (`service.id`, `item.id`) without secrets.

- [x] **Slug generation guard in `RVItem.save`.**  
  **Where:** `src/rvsite/models.py`.  
  **Done:** `save` and `get_slug` use `if not self.slug:` so `None` and `""` both trigger generation.

- [ ] **Template / RSS correctness.**  
  **Where:** `src/rvsite/templates/rss.xml` — `description` embeds `{{ i.display_caption }}` without XML escaping; `link` uses `https://{{ domain.alt_domain }}` which may be empty for some domains.  
  **Work:** Use `{% autoescape on %}` defaults or `force_escape`, or CDATA. Fall back to `domain.name` for links when `alt_domain` is blank.

- [x] **`feed_datetime` filter portability.**  
  **Where:** `src/rvsite/templatetags/rv_filters.py`.  
  **Done:** Normalize to UTC (`astimezone` / `replace(tzinfo=UTC)`), then `timestamp()` + `formatdate(..., usegmt=True)` for RFC-822–style RSS `pubDate`.

- [ ] **Typos and API consistency.**  
  **Where:** `orginal_links` typo on `RVItem` (`src/rvsite/models.py`) kept as backward-compatible alias.  
  **Work:** Prefer single spelling in new code; optionally deprecate alias in templates.

- [x] **Settings/docstring drift.**  
  **Where:** `src/rearvue/settings.py` pointed at Django 1.6 docs.  
  **Done:** Module docstring and comments use `en/stable` Django URLs.

- [ ] **Access model clarity for `rvadmin`.**  
  **Where:** `admin_page` requires `is_superuser`; `RVDomain.owner` exists but is not used for `rvadmin`.  
  **Work:** Decide if domain owners should manage their own site without global superuser; if yes, add permission checks tied to `owner` and tests; if no, document that only superusers may use `/rvadmin/`.

---

## Quick wins checklist (can batch in one PR)

- [x] Fix Dependabot `package-ecosystem` + directory.
- [x] Fix Flickr `type` string and migrate data.
- [x] Change `fix_item` to POST + template form.
- [x] Remove global SSL verify bypass from `update_content` (verify stays on; certifi/env for CAs).
- [x] Fix README / settings header doc drift.
- [x] Fix `wsgi.py` docstring (project name + docs link).

---

## Positive observations (keep as patterns)

- **SSRF-minded URL validation** in `validate_public_http_url` and use in `make_link` / `final_destination` is a strong baseline; extending it to RSS enclosures would align the codebase.
- **Host-based multi-tenancy** via `HTTP_HOST` → `RVDomain` in `rearvue.utils.page` is clear; logging unknown hosts is helpful for operations.
- **Safe redirect** pattern in `_safe_admin_redirect` (Django `url_has_allowed_host_and_scheme`) avoids open redirects on return to Referer.
- **`contextualize_item`** correctly uses `@require_POST` and CSRF in the form on the item page.

---

*Generated from repository state; `settings_server` was not reviewed because it is not in-tree.*
