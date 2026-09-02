import logging
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db.models import Count
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from rvsite.models import RVService

from .archive_import import maximum_archive_bytes, submit_twitter_archive
from .auth import current_client_id
from .capabilities import (
    create_link,
    create_media,
    create_service,
    safe_service_with_count,
    serialize_job,
    serialize_link,
    serialize_media,
    submit_processing_job,
    update_link,
    update_media,
    update_service,
)
from .capabilities import (
    get_job as get_job_record,
)
from .capabilities import (
    get_link as get_link_record,
)
from .capabilities import (
    get_media as get_media_record,
)
from .capabilities import (
    list_jobs as list_job_records,
)
from .capabilities import (
    list_links as list_link_records,
)
from .capabilities import (
    list_media as list_media_records,
)
from .contracts import (
    ItemCreateInput,
    ItemPatchInput,
    ItemSearchFilters,
    ItemUpsertInput,
    LinkCreateInput,
    LinkPatchInput,
    MediaCreateInput,
    MediaPatchInput,
    ServiceCreateInput,
    ServicePatchInput,
)
from .destruction import confirm_delete, preview_delete
from .exports import export_json_page, submit_export
from .jobs import registered_operations
from .models import MCP_SCOPES, MCPClient
from .services import (
    MCPServiceError,
    _item_for_client,
    bulk_upsert_items,
    create_item,
    require_domain,
    require_scope,
    search_items,
    serialize_domain,
    serialize_item,
    serialize_service,
    update_item,
    upsert_item,
)

logger = logging.getLogger(__name__)
mcp = FastMCP(
    "RearVue",
    instructions="Authenticated, domain-scoped access to RearVue normalized archive data.",
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
    max_request_body_size=settings.MCP_MAX_REQUEST_BODY_BYTES,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.MCP_ALLOWED_HOSTS),
        allowed_origins=list(settings.MCP_ALLOWED_ORIGINS),
    ),
)


def _client():
    client_id = current_client_id.get()
    if client_id is None:
        raise MCPServiceError("unauthenticated", "Bearer authentication is required.")
    return MCPClient.objects.get(pk=client_id)


async def _run(callback):
    try:
        return await sync_to_async(callback, thread_sensitive=True)()
    except MCPServiceError as exc:
        return exc.as_result()
    except Exception:
        logger.exception("Unhandled RearVue MCP tool error")
        return {
            "ok": False,
            "error": {
                "code": "internal_error",
                "message": "The operation failed unexpectedly.",
                "retryable": True,
            },
        }


@mcp.tool(name="rearvue_v1_discover", structured_output=True)
async def discover() -> dict[str, Any]:
    """Discover this server's versioned capabilities, limits, schemas, and caller grants."""
    def execute():
        client = _client()
        return {
            "ok": True,
            "contract_version": "1.1",
            "transport": "streamable-http",
            "tools_version": "rearvue_v1",
            "caller": {
                "name": client.name,
                "scopes": sorted(client.scopes),
                "domain_ids": list(client.domains.order_by("id").values_list("id", flat=True)),
            },
            "available_scopes": sorted(MCP_SCOPES),
            "limits": {
                "default_page_size": settings.MCP_DEFAULT_PAGE_SIZE,
                "maximum_page_size": settings.MCP_MAX_PAGE_SIZE,
                "maximum_bulk_items": settings.MCP_MAX_BULK_ITEMS,
                "maximum_raw_data_bytes": settings.MCP_MAX_RAW_DATA_BYTES,
                "maximum_request_body_bytes": settings.MCP_MAX_REQUEST_BODY_BYTES,
                "maximum_archive_bytes": maximum_archive_bytes(),
                "maximum_export_snapshot_records": settings.MCP_MAX_EXPORT_SNAPSHOT_RECORDS,
                "idempotency_ttl_seconds": settings.MCP_IDEMPOTENCY_TTL_SECONDS,
                "maximum_media_bytes": settings.MCP_MAX_MEDIA_BYTES,
                "maximum_image_pixels": settings.MCP_MAX_IMAGE_PIXELS,
                "maximum_destructive_records": settings.MCP_MAX_DESTRUCTIVE_RECORDS,
                "destructive_preview_ttl_seconds": settings.MCP_DESTRUCTIVE_PREVIEW_TTL_SECONDS,
            },
            "pagination": {"ordering": ["datetime_created:desc", "id:desc"], "cursor": "opaque"},
            "caption": {
                "default_format": "plain",
                "accepted_formats": ["plain", "html"],
                "html_is_sanitized": True,
            },
            "identity": ["service_id", "item_id"],
            "updates": {"semantics": "patch", "conflict_field": "expected_revision"},
            "bulk": {"partial_failures": True, "idempotency_key_required": True},
            "authorization": {"capability": "domain:owner", "domain_grants_required": True},
            "jobs": {
                "durable": True,
                "worker_command": "run_mcp_jobs",
                "registered_operations": list(registered_operations()),
                "submission_tools": True,
            },
            "media": {
                "read_expansion": True,
                "writes": True,
                "sources": ["inline_base64", "public_http_url"],
                "formats": ["jpeg", "png", "webp", "gif", "mp4"],
                "authenticated_downloads": True,
            },
            "links": {"read_expansion": True, "writes": True, "enrichment_job": True},
            "services": {"writes": True, "type_immutable": True, "deletion": False},
            "deletion": {
                "resources": ["items", "media", "links"],
                "preview_required": True,
                "single_use_confirmation": True,
                "irreversible": True,
            },
            "importers": ["twitter_tweets_js"],
            "export_formats": ["json", "ndjson"],
        }
    return await _run(execute)


@mcp.tool(name="rearvue_v1_list_domains", structured_output=True)
async def list_domains() -> dict[str, Any]:
    """List domains granted to the authenticated client."""
    def execute():
        client = _client()
        require_scope(client, "domains:read")
        domains = client.domains.order_by("id")
        return {"ok": True, "domains": [serialize_domain(domain) for domain in domains]}
    return await _run(execute)


@mcp.tool(name="rearvue_v1_get_domain", structured_output=True)
async def get_domain(domain_id: int) -> dict[str, Any]:
    """Retrieve one granted domain by its stable integer ID."""
    def execute():
        client = _client()
        require_scope(client, "domains:read")
        return {"ok": True, "domain": serialize_domain(require_domain(client, domain_id))}
    return await _run(execute)


@mcp.tool(name="rearvue_v1_list_services", structured_output=True)
async def list_services(domain_id: int | None = None) -> dict[str, Any]:
    """List safe service summaries, never legacy config, state, or credentials."""
    def execute():
        client = _client()
        require_scope(client, "services:read")
        queryset = RVService.objects.filter(domain__mcp_clients=client).annotate(item_count=Count("rvitem"))
        if domain_id is not None:
            require_domain(client, domain_id)
            queryset = queryset.filter(domain_id=domain_id)
        return {
            "ok": True,
            "services": [serialize_service(service, service.item_count) for service in queryset.order_by("id")],
        }
    return await _run(execute)


@mcp.tool(name="rearvue_v1_get_service", structured_output=True)
async def get_service(service_id: int) -> dict[str, Any]:
    """Retrieve one safe service summary without configuration or credentials."""
    def execute():
        client = _client()
        require_scope(client, "services:read")
        service = RVService.objects.filter(pk=service_id, domain__mcp_clients=client).annotate(item_count=Count("rvitem")).first()
        if service is None:
            raise MCPServiceError("not_found", "Service not found.", path="service_id")
        return {"ok": True, "service": serialize_service(service, service.item_count)}
    return await _run(execute)


@mcp.tool(name="rearvue_v1_search_items", structured_output=True)
async def item_search(
    filters: ItemSearchFilters | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    include_media: bool = False,
    include_links: bool = False,
) -> dict[str, Any]:
    """Search granted normalized items with portable filters and deterministic cursor pagination."""
    return await _run(lambda: search_items(_client(), filters, cursor, limit, include_media, include_links))


@mcp.tool(name="rearvue_v1_get_item", structured_output=True)
async def get_item(item_id: int, include_media: bool = False, include_links: bool = False) -> dict[str, Any]:
    """Retrieve one granted normalized item, optionally expanding safe media and link metadata."""
    def execute():
        client = _client()
        require_scope(client, "items:read")
        item = _item_for_client(client, item_id)
        if include_media:
            item = type(item).objects.prefetch_related("rvmedia_set").get(pk=item.pk)
        if include_links:
            item = type(item).objects.prefetch_related("rvlink_set").get(pk=item.pk)
        return {"ok": True, "item": serialize_item(item, client, include_media, include_links)}
    return await _run(execute)


@mcp.tool(name="rearvue_v1_create_item", structured_output=True)
async def item_create(item: ItemCreateInput) -> dict[str, Any]:
    """Create a normalized item; service and source item identity must be new."""
    def execute():
        client = _client()
        created = create_item(client, item)
        return {"ok": True, "created": True, "item": serialize_item(created, client)}
    return await _run(execute)


@mcp.tool(name="rearvue_v1_upsert_item", structured_output=True)
async def item_upsert(item: ItemUpsertInput) -> dict[str, Any]:
    """Create by external identity or patch an existing item with expected_revision protection."""
    def execute():
        client = _client()
        result, created = upsert_item(client, item)
        return {"ok": True, "created": created, "item": serialize_item(result, client)}
    return await _run(execute)


@mcp.tool(name="rearvue_v1_update_item", structured_output=True)
async def item_update(item_id: int, patch: ItemPatchInput) -> dict[str, Any]:
    """Patch writable fields when expected_revision matches; identity fields are immutable."""
    def execute():
        client = _client()
        result = update_item(client, item_id, patch)
        return {"ok": True, "item": serialize_item(result, client)}
    return await _run(execute)


@mcp.tool(name="rearvue_v1_bulk_upsert_items", structured_output=True)
async def items_bulk_upsert(items: list[ItemUpsertInput], idempotency_key: str) -> dict[str, Any]:
    """Independently upsert up to 100 items and return a result for every submitted record."""
    return await _run(lambda: bulk_upsert_items(_client(), items, idempotency_key))


@mcp.tool(name="rearvue_v1_list_links", structured_output=True)
async def links_list(
    domain_id: int | None = None,
    item_id: int | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List links in granted domains with deterministic pagination."""
    return await _run(
        lambda: list_link_records(
            _client(), domain_id=domain_id, item_id=item_id, cursor=cursor, limit=limit
        )
    )


@mcp.tool(name="rearvue_v1_get_link", structured_output=True)
async def link_get(link_id: int) -> dict[str, Any]:
    """Retrieve one link without exposing local file paths."""
    return await _run(lambda: {"ok": True, "link": get_link_record(_client(), link_id)})


@mcp.tool(name="rearvue_v1_create_link", structured_output=True)
async def link_create(link: LinkCreateInput) -> dict[str, Any]:
    """Create a domain-scoped link to a public HTTP(S) URL."""
    return await _run(
        lambda: {"ok": True, "link": serialize_link(create_link(_client(), link))}
    )


@mcp.tool(name="rearvue_v1_update_link", structured_output=True)
async def link_update(link_id: int, patch: LinkPatchInput) -> dict[str, Any]:
    """Update safe link fields with optimistic revision protection."""
    return await _run(
        lambda: {"ok": True, "link": serialize_link(update_link(_client(), link_id, patch))}
    )


@mcp.tool(name="rearvue_v1_list_media", structured_output=True)
async def media_list(
    domain_id: int | None = None,
    item_id: int | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List safe media metadata and authenticated download references."""
    return await _run(
        lambda: list_media_records(
            _client(), domain_id=domain_id, item_id=item_id, cursor=cursor, limit=limit
        )
    )


@mcp.tool(name="rearvue_v1_get_media", structured_output=True)
async def media_get(media_id: int) -> dict[str, Any]:
    """Retrieve safe media metadata and an authenticated download reference."""
    return await _run(lambda: {"ok": True, "media": get_media_record(_client(), media_id)})


@mcp.tool(name="rearvue_v1_create_media", structured_output=True)
async def media_create(media: MediaCreateInput) -> dict[str, Any]:
    """Create validated media from bounded inline content or a public HTTP(S) URL."""
    return await _run(
        lambda: {"ok": True, "media": serialize_media(create_media(_client(), media))}
    )


@mcp.tool(name="rearvue_v1_update_media", structured_output=True)
async def media_update(media_id: int, patch: MediaPatchInput) -> dict[str, Any]:
    """Atomically replace media content with optimistic revision protection."""
    return await _run(
        lambda: {"ok": True, "media": serialize_media(update_media(_client(), media_id, patch))}
    )


@mcp.tool(name="rearvue_v1_create_service", structured_output=True)
async def service_create(service: ServiceCreateInput) -> dict[str, Any]:
    """Create a service with safe fields only; no credentials or provider state are accepted."""
    return await _run(
        lambda: {"ok": True, "service": safe_service_with_count(create_service(_client(), service))}
    )


@mcp.tool(name="rearvue_v1_update_service", structured_output=True)
async def service_update(service_id: int, patch: ServicePatchInput) -> dict[str, Any]:
    """Update a service name and enabled/moderation flags; type and secrets are immutable."""
    return await _run(
        lambda: {"ok": True, "service": safe_service_with_count(update_service(_client(), service_id, patch))}
    )


@mcp.tool(name="rearvue_v1_list_jobs", structured_output=True)
async def jobs_list(
    domain_id: int | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List durable jobs in granted domains without returning submitted payloads."""
    return await _run(
        lambda: list_job_records(
            _client(), domain_id=domain_id, status=status, cursor=cursor, limit=limit
        )
    )


@mcp.tool(name="rearvue_v1_get_job", structured_output=True)
async def job_get(job_id: int) -> dict[str, Any]:
    """Retrieve progress and terminal results for one durable job."""
    return await _run(lambda: {"ok": True, "job": get_job_record(_client(), job_id)})


@mcp.tool(name="rearvue_v1_submit_processing", structured_output=True)
async def processing_submit(
    domain_id: int,
    operation: str,
    selector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit one documented, domain-bounded processing operation."""
    return await _run(
        lambda: {
            "ok": True,
            "job": serialize_job(submit_processing_job(_client(), domain_id, operation, selector)),
        }
    )


@mcp.tool(name="rearvue_v1_export_json", structured_output=True)
async def export_json(
    filters: dict[str, Any] | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Incrementally export snapshot-bound normalized JSON records and a media manifest."""
    return await _run(lambda: export_json_page(_client(), filters, cursor, limit))


@mcp.tool(name="rearvue_v1_submit_export", structured_output=True)
async def export_submit(domain_id: int, updated_after: str | None = None) -> dict[str, Any]:
    """Submit an asynchronous NDJSON export for one granted domain."""
    return await _run(
        lambda: {"ok": True, "job": serialize_job(submit_export(_client(), domain_id, updated_after))}
    )


@mcp.tool(name="rearvue_v1_submit_twitter_archive", structured_output=True)
async def twitter_archive_submit(
    domain_id: int,
    service_id: int,
    archive_base64: str,
) -> dict[str, Any]:
    """Submit a bounded Twitter tweets.js archive to the shared asynchronous importer."""
    return await _run(
        lambda: {
            "ok": True,
            "job": serialize_job(
                submit_twitter_archive(_client(), domain_id, service_id, archive_base64)
            ),
        }
    )


@mcp.tool(name="rearvue_v1_preview_delete", structured_output=True)
async def delete_preview(domain_id: int, resource: str, ids: list[int]) -> dict[str, Any]:
    """Preview the exact bounded impact of an irreversible item, media, or link deletion."""
    return await _run(lambda: {"ok": True, "preview": preview_delete(_client(), domain_id, resource, ids)})


@mcp.tool(name="rearvue_v1_confirm_delete", structured_output=True)
async def delete_confirm(preview_id: int, confirmation_token: str) -> dict[str, Any]:
    """Confirm an unchanged deletion preview with its short-lived single-use token."""
    return await _run(lambda: confirm_delete(_client(), preview_id, confirmation_token))


application = mcp.streamable_http_app()
