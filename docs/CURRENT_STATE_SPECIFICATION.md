# RearVue current-state specification

Status: as-built catch-up specification

Evidence baseline: repository `main` at `661b5b4`

Scope: the Django application under `src/`, its public and administrative web interfaces, persistence model, ingestion jobs, and external service integrations

## 1. Purpose and scope

RearVue is a self-hosted, host-based social-media archive and nostalgia browser. It collects posts from external services, stores normalized post metadata in a relational database, mirrors supported media into a local data store, and presents the archive through chronological public pages and generated RSS.

This document specifies observed current behavior. It does not convert suspected defects, unused fields, or historical compromises into desired requirements. Known security, correctness, performance, and maintenance concerns are tracked in [CODE_REVIEW.md](CODE_REVIEW.md) and referenced below where they affect the behavioral interpretation.

Excluded from this specification:

- The private `rearvue.settings_server` module and any production infrastructure not stored in this repository.
- Behavior internal to third-party packages such as `django-feed-reader`, `webpreview`, Flickr APIs, and Instagram APIs beyond how RearVue calls them.
- A target-state roadmap or proposed redesign.

## 2. Actors and terminology

- **Visitor**: an unauthenticated user or an authenticated user who is not the selected domain's owner.
- **Domain owner**: the Django user referenced by `RVDomain.owner`.
- **Superuser**: a Django user with `is_superuser=True`; the only actor allowed through RearVue's `/rvadmin/` gate.
- **Domain**: an `RVDomain` selected from the HTTP `Host` header. It supplies archive identity, owner, year range, optional public origin, blurb, poster item, and update timestamp.
- **Service**: an `RVService` belonging to a domain and representing an RSS, Twitter, Flickr, or Instagram source.
- **Item**: an `RVItem` normalized from an external post.
- **Mirrored media**: an `RVMedia` row whose files are stored below `DATA_STORE/media/<domain>/<year>/<month>/<day>/`.
- **Original link**: an automatically discovered `RVLink` with `is_context=False`.
- **Context link**: a superuser-supplied `RVLink` with `is_context=True`.

## 3. System boundaries and configuration

### SYS-001 — Runtime configuration

The Django settings module imports the uncommitted `rearvue.settings_server` module. Runtime startup therefore requires deployment-specific values for secrets, database configuration, data and log paths, public hosts, media/static roots, service credentials, feed-reader configuration, and the default domain protocol.

Optional settings configure the Instagram redirect URI, Graph API version, OAuth scopes, and a legacy Facebook access token.

Evidence: `src/rearvue/settings.py`; `README.md` “Application configuration”.

### SYS-002 — Persistence and filesystem split

Domain, service, item, link, and media metadata are persisted through Django models. Mirrored binaries and generated feeds are written to the filesystem below `DATA_STORE`; database deletion does not generally imply file deletion. The destructive Instagram reset command is a specific exception that attempts both database cascade deletion and file removal.

Evidence: `src/rvsite/models.py`; `src/rvsite/management/commands/reset_instagram_graph.py`.

### SYS-003 — Host-selected tenancy

Every public `rvsite` view and RearVue administrative view resolves an `RVDomain` from `HTTP_HOST`. Exact `RVDomain.name` matches are preferred; otherwise the first row whose `alt_domain` contains the host value is selected. Unknown hosts return HTTP 404.

The public view wrapper supplies `domain` and an inclusive `year_range` from `min_year` through `max_year`. The administrative wrapper supplies `domain` and rejects non-superusers with HTTP 403.

Evidence: `rearvue.utils._resolve_domain`, `page`, and `admin_page`.

### SYS-004 — Static and media delivery

The application references mirrored media with root-relative paths. Django serves `MEDIA_ROOT` at `MEDIA_URL` only when `DEBUG` is enabled; production media serving is therefore an external deployment responsibility.

Evidence: `src/rearvue/urls.py`; public templates.

## 4. Data rules and invariants

### DATA-001 — Domain

Each domain has a unique name, an owner, display metadata, cached minimum and maximum years, an optional alternate public origin, an optional poster item, and an optional last-updated timestamp. Deleting the owner deletes the domain; deleting a poster item deletes the domain because `poster_image` uses cascading deletion.

Evidence: `RVDomain` in `src/rvsite/models.py`.

### DATA-002 — Service

Each service belongs to one domain. Its `type` is a free-form string, but active integration code recognizes the exact lowercase values `rss`, `twitter`, `flickr`, and `instagram`. It stores source identity, update cursors, live status, OAuth credentials, opaque extra data, and Instagram token timestamps.

Deleting a domain cascades to its services. Service credentials are stored in database fields without repository-visible encryption; see the secrets-at-rest concern in [CODE_REVIEW.md](CODE_REVIEW.md#p2--security-hardening-performance-operational-risk).

### DATA-003 — Item identity and ownership

Each item belongs to both a service and a domain and carries the source identifier, creation dates, remote URL, title, caption, raw source data, public flag, mirror state, moderation flags, and slug. Integration code searches for existing items by `(service, item_id)`, but the database does not declare that pair unique.

Evidence: `RVItem`; service import functions.

### DATA-004 — Slugs

Saving an item without a slug first persists it to obtain an ID, derives a slug from its title or `post-<id>`, truncates the base to 47 characters, and appends a numeric suffix until no item anywhere in the database has that slug. Slug uniqueness is enforced by application queries rather than a database constraint.

Evidence: `RVItem.save` and `get_slug`.

### DATA-005 — Media state

Observed integration behavior uses `mirror_state` as a processing state:

- `0`: media mirroring is pending or not yet completed.
- `1`: the item is viewable/mirrored and may still need automatic link discovery.
- `2`: automatic link discovery has completed or been attempted.

This meaning is inferred from queues and transitions rather than declared as model choices. Page queries use these values inconsistently; see `A-003`.

### DATA-006 — Media representation

An item can have zero or more media rows. Media types are `0` none, `1` image, `2` video, and `3` autoplaying video. Stored paths distinguish original, primary, and thumbnail variants. Item display helpers use the first related media row as the representative media.

### DATA-007 — Links

An item can have automatic original links and manually supplied context links. Link previews can store a destination URL, title, description, and remote image URL. Creating a context link replaces all existing context links for that item before attempting the new preview.

## 5. Public archive behavior

### PUB-001 — Home page

`GET /` displays at most 12 public items for the selected domain, newest first by `datetime_created`. The query does not filter by mirror state. Each item is rendered using the shared item-detail template.

Evidence: `rvsite.views.index`; `rvsite/templates/rvsite/index.html`.

### PUB-002 — Year page

`GET /rv/<year>/` groups items from the requested calendar year by month. It counts all matching items and selects up to six items with thumbnails per month after random database ordering. Visitors see only public items; the domain owner may also see private items. Items require `mirror_state >= 1`.

The random ordering can be expensive on large archives; see [CODE_REVIEW.md](CODE_REVIEW.md#p2--security-hardening-performance-operational-risk).

### PUB-003 — Month page

`GET /rv/<year>/<month>/` displays matching items oldest first and provides links to the preceding and following calendar months. Visitors see only public items; the domain owner may also see private items. Items require `mirror_state >= 1`.

Invalid calendar components are not handled explicitly and can raise an exception rather than a deliberate 404.

### PUB-004 — Day page

`GET /rv/<year>/<month>/<day>/` displays matching items oldest first. It also displays thumbnail-bearing items from the same month and day in other years. Visitors see only public items; the domain owner may also see private items.

The primary day query requires `mirror_state == 1`, while its cross-year query accepts `mirror_state >= 1`. This differs from month and year behavior and is treated as a suspected defect rather than a requirement; see `A-003`.

### PUB-005 — Item page and canonical URL

`GET /rv/<year>/<month>/<day>/<slug>/` resolves an item by the selected domain and slug. A missing slug is retried as `post-<slug>` for legacy URLs. If found through that fallback, or if the URL date does not match the item's actual creation date, the response permanently redirects to the canonical dated slug URL.

Private items return 404 unless the requester is the domain owner. The page includes same-day items from other years, subject to the visitor/private rule.

### PUB-006 — Item rendering

The shared item renderer displays title or date, caption, media, discovered link previews, creation date navigation, and a link to the original service post. Images may be displayed singly or in a carousel; standard videos use controls; media type 3 videos autoplay and loop.

Captions are rendered with Django's `safe` filter. Twitter archive import deliberately stores anchor, line-break, and blockquote markup in captions; RSS bodies may also contain HTML. The resulting stored-HTML trust boundary is unresolved and documented in [CODE_REVIEW.md](CODE_REVIEW.md#p0--critical-security-and-safety).

### PUB-007 — Layout and archive navigation

All normal pages display the domain name, blurb, owner attribution, poster image when configured, inclusive year navigation, generated RSS link, and last-updated metadata. Superusers additionally see links to RearVue Admin and Django Admin.

The “Sources” and “Links” sidebar entries are hard-coded to one deployment rather than derived from domain or service records; see `A-005`.

### PUB-008 — Implemented summary view

An implemented `summary` view selects random subsets for Recent, Last Year, and Five Years Ago sections. It is not registered in `rvsite.urls`, so it is not reachable through the checked-in URL configuration. This is dead or incomplete behavior, not a public requirement; see `A-001`.

## 6. Administrative behavior

### ADM-001 — Access control

All `/rvadmin/` views require authentication, a resolvable domain, and `is_superuser=True`. Domain ownership alone is insufficient. Django's `/admin/` retains Django's own permission model.

This differs from public private-item visibility, which checks exact domain ownership; see `A-002` and the access-model item in [CODE_REVIEW.md](CODE_REVIEW.md#p3--maintainability-testing-and-polish).

### ADM-002 — Service index

`GET /rvadmin/` lists services belonging to the selected domain. Each entry constructs a relative connection URL from the service type and ID. Unsupported or incorrectly cased types can therefore lead to missing routes.

### ADM-003 — Repair item

`POST /rvadmin/fix_item/<id>/` dispatches a repair operation for Instagram, RSS, or Twitter items in the selected domain. Flickr repair is not implemented. Success or failure is reported through Django messages, then the user is redirected to a safe same-host referrer or the admin index.

### ADM-004 — Contextualize item

`POST /rvadmin/contextualize_item/<id>/` deletes existing context links, requires a nonblank URL, then resolves redirects and builds a link preview. Only public HTTP(S) destinations that pass hostname/IP checks are accepted by this path. The result is communicated through Django messages.

### ADM-005 — Instagram connection

A superuser can start connection for a new or existing Instagram service. New connection creates an `instagram` service immediately, stores its ID and a random OAuth state in the session, and redirects through Instagram authorization.

The callback requires matching state, an authorization code, the saved service session, and a service belonging to the selected domain. It exchanges the code for a short-lived token, exchanges that for a long-lived token, fetches the Instagram user identity, persists the token and expiry metadata, clears session state, and redirects to the admin index. Failures are shown as messages.

### ADM-006 — Flickr connection

A superuser can start OAuth for a new or existing Flickr service. New connection creates a lowercase `flickr` service. A request token and secret are stored on the service, Flickr performs authorization, and the callback exchanges the verifier for the final username, user ID, token, and secret.

### ADM-007 — Twitter archive import

For an existing Twitter service, the connect page labels live connection as broken and offers upload of the archive's `tweets.js`. On an `archive` POST, the view removes the expected `window.YTD.tweets.part0 = ` prefix, parses JSON, and imports non-retweet, non-reply tweets. Input shape and prefix are assumed rather than validated with a user-facing error path.

## 7. Ingestion and scheduled processing

### ING-001 — Main update command

`python manage.py update_content` independently runs RSS update/mirroring/link discovery, Twitter mirroring/link discovery, Instagram update/mirroring, Flickr update/mirroring, and cleanup. Flags can skip each service family or cleanup. Exceptions are caught per phase, reported to command output, and do not stop subsequent phases. The command ends with a success message even when individual phases reported errors.

Evidence: `src/rvsite/management/commands/update_content.py`.

### ING-002 — Domain cleanup and feed generation

Unless skipped, the update command calculates each domain's min/max content year from item timestamps, sets `last_updated`, and writes the 25 newest public items to `DATA_STORE/media/<domain.name>/rss.xml` using the RSS template.

Feed output uses `alt_domain` directly for channel/media URLs and does not apply an explicit XML-safety strategy to captions. These are known correctness gaps in [CODE_REVIEW.md](CODE_REVIEW.md#p3--maintainability-testing-and-polish).

### ING-003 — RSS ingestion

Live `rss` services use `auth_token` as the feed URL. RearVue obtains or creates a `django-feed-reader` source, runs the package-wide feed update, then creates or updates items from that source's posts. Posts without enclosures advance directly to mirror state 1; posts with enclosures are mirrored.

The mirroring queue processes up to 50 live RSS items at state 0, recreates their media rows, downloads image/video enclosures, creates thumbnails for images, then advances items to state 1. Link discovery processes up to 50 state-1 items, heuristically selects a link from caption HTML, creates a preview, and advances the item to state 2.

RSS enclosure and preview-image fetches do not consistently use the central public-URL validator; see the SSRF concern in [CODE_REVIEW.md](CODE_REVIEW.md#p2--security-hardening-performance-operational-risk).

### ING-004 — Twitter archive ingestion

Archive import excludes retweets and leading-mention replies, converts mentions and URLs into HTML, embeds quoted tweets, removes media URLs from captions, converts newlines to `<br>`, and stores the original tweet JSON. Items with media remain at state 0 for later mirroring; items without media advance to state 1.

The mirroring queue processes up to 100 live Twitter items at state 0, downloads photos or the highest-bitrate available video, creates representative thumbnails, and advances to state 1. Link discovery processes up to 100 state-1 items, recreates non-context previews, and advances to state 2 even if processing raises an exception.

There is no live Twitter API update path.

### ING-005 — Flickr ingestion

Flickr services are checked no more frequently than approximately every 12 hours. The integration pages through the user's photos, creates or updates items, stores raw Flickr JSON, advances the upload-date cursor, and updates `last_checked`. Mirroring processes up to 100 pending items per Flickr service, downloads an original or best available image and a display image, creates a thumbnail, and advances successfully processed items to state 1.

### ING-006 — Instagram ingestion

Live Instagram services use the Instagram API with Instagram Login. The integration refreshes eligible long-lived tokens, fetches account media with paging, normalizes image/video/carousel data into stored raw JSON, creates or updates items, and mirrors pending media. Mirroring downloads media and thumbnails into the data store and advances successfully processed items.

The exact accessible history and media set depend on the external Instagram API and account type.

### ING-007 — Instagram reset

`python manage.py reset_instagram_graph --confirm` is intentionally destructive. Without `--confirm` it refuses to run. With confirmation, it finds all Instagram services across domains, clears domain poster references to their items, cascades deletion of services/items/links/media rows, and attempts to delete referenced files from disk.

## 8. Network and security behavior

### SEC-001 — TLS verification

The package sets `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` to certifi's bundle only when neither environment variable is already set. In-repository download calls generally retain TLS certificate verification. Deployment variables can override the bundle.

### SEC-002 — Outbound URL validation

The central link-preview path accepts only HTTP(S), rejects known local hostnames and non-public IP address classes, resolves DNS, validates each HTTP redirect/meta-refresh hop, and limits traversal to ten hops. This validation is not uniformly used by all source-specific fetches.

### SEC-003 — CSRF and redirects

State-changing repair and contextualization endpoints require POST and use template CSRF tokens. OAuth connection forms also use POST and CSRF. Administrative post-action redirects accept the referrer only when it is a same-host safe URL.

### SEC-004 — Production hardening boundary

Production settings are partly external and cannot be verified from the repository. The checked-in middleware omits `SecurityMiddleware`, and secure-cookie, HTTPS redirect, and HSTS settings are not visible. These are deployment requirements or gaps, not assumed current guarantees; see [CODE_REVIEW.md](CODE_REVIEW.md#p2--security-hardening-performance-operational-risk).

## 9. Error handling and recovery

### ERR-001 — Unknown domains and private items

Unknown hosts return 404. Private item detail returns 404 to non-owners. RearVue admin returns 403 to authenticated non-superusers, while unauthenticated users are redirected by `login_required`.

### ERR-002 — Import resilience

The main content update command isolates broad service phases so later phases continue after an error. Individual legacy service functions frequently catch broad exceptions and print errors; failure can leave items in their previous processing state. Twitter link discovery is an exception: it advances items to state 2 even on an error.

### ERR-003 — Manual repair

Superusers can re-run source-specific mirroring/link behavior for Instagram, RSS, and Twitter through the item page. The operation reports a message but has no transactional rollback guarantee across database rows and filesystem writes.

## 10. Compatibility and operational constraints

- The checked-in dependency set targets Django and Python packages pinned in `src/requirements.txt`; `requirements.in` mixes runtime, development, and security tooling.
- The application requires a writable `DATA_STORE` and, outside local runserver use, a writable configured log path.
- Database migrations define the persisted schema and include normalization from legacy `Flickr` to `flickr` service types and the current Instagram token fields.
- Generated media paths and item URLs are long-lived external interfaces; changing domain names, dates, slugs, or path conventions can break stored links.
- The repository contains placeholder test modules only. No executable acceptance or regression suite currently substantiates behavior beyond source inspection.

## 11. Suspected defects and coverage gaps

The following are observed gaps, not requirements:

- Day pages exclude state-2 items while month/year pages include them.
- The summary view is implemented but unreachable.
- Stored captions and the domain summary blurb can be rendered as trusted HTML.
- Some outbound RSS fetches bypass central SSRF validation.
- RSS URLs can be malformed when `alt_domain` is blank or already contains a scheme.
- The root recent-items query can include items before mirroring completes.
- Service type, item source identity, and slug uniqueness rely partly on conventions rather than database constraints.
- Placeholder tests leave public visibility, admin permissions, ingestion transitions, OAuth behavior, and host routing without regression coverage.
- Several broad exception handlers and partial database/filesystem operations make recovery behavior source-specific and difficult to observe.

The prioritized remediation record is [CODE_REVIEW.md](CODE_REVIEW.md).

## 12. Assumptions requiring clarification

### A-001 — Intended home-page experience

Provisional interpretation: the current routed home page (12 newest public items) is canonical; the summary view is incomplete or abandoned.

Evidence: `index` is routed at `/`; `summary` and its template exist but have no URL.

Confidence: medium.

Impact if wrong: `PUB-001`, navigation, and expected nostalgia discovery behavior change.

Question: Should the summary view replace or supplement the current home page?

### A-002 — Owner and administrator roles

Provisional interpretation: owners may see their own private archive items, but only global superusers may administer services or repair/contextualize items.

Evidence: public views compare `request.user` to `RVDomain.owner`; `admin_page` checks only `is_superuser`.

Confidence: high that this is current behavior; low that it is intended.

Impact if wrong: permissions across every public and administrative workflow change.

Question: Should a domain owner who is not a superuser be able to administer that domain?

### A-003 — Meaning of mirror state 2 on day pages

Provisional interpretation: state 2 means “mirrored and link processing completed” and should remain publicly viewable; its omission from the primary day query is a defect.

Evidence: month/year and cross-year day queries use `>=1`; link discovery transitions `1` to `2`; the primary day query alone uses `==1`.

Confidence: high.

Impact if wrong: fixing the query would expose items intentionally hidden after link processing.

Question: Should state-2 items appear on day pages?

### A-004 — Moderation model

Provisional interpretation: `moderated`, `edited`, and `hide_unmoderated` are incomplete or dormant functionality and impose no current visibility rule.

Evidence: fields exist, but no checked-in view or ingestion query uses them to filter content.

Confidence: high.

Impact if wrong: public visibility and ingestion acceptance requirements are missing.

Question: Is moderation intended to affect current public visibility, or should these fields be treated as legacy?

### A-005 — Deployment-specific sidebar links

Provisional interpretation: hard-coded `xurble` sources and related sites are a deployment customization, not a reusable multi-domain product requirement.

Evidence: links are embedded directly in `base.html` and are not derived from `RVDomain` or `RVService`.

Confidence: high.

Impact if wrong: the base layout specification must require these exact links for all domains.

Question: Should source and related-site links become domain-configurable?

## 13. Evidence index

Primary behavioral evidence inspected:

- `README.md`
- `AGENTS.md`
- `src/rearvue/settings.py`, `urls.py`, and `utils.py`
- `src/rvsite/models.py`, `views.py`, `urls.py`, templates, migrations, template filters, and management commands
- `src/rvadmin/views.py`, `urls.py`, admin registration, and templates
- all modules under `src/rvservices/`
- `src/requirements.in`, `src/requirements.txt`, `.github/dependabot.yml`, and `.gitignore`
- placeholder test modules in `src/rvsite/tests.py` and `src/rvadmin/tests.py`
- the repository history near the evidence baseline
- [CODE_REVIEW.md](CODE_REVIEW.md), treated as a secondary audit record and checked against current source where referenced

No runtime application checks were executed because the required uncommitted `settings_server` configuration is deployment-specific and the repository provides no test settings or substantive automated tests.
