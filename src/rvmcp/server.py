import logging
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db.models import Count
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from rvsite.models import RVDomain, RVService

from .auth import current_client_id
from .contracts import ItemCreateInput, ItemPatchInput, ItemSearchFilters, ItemUpsertInput
from .models import MCPClient, MCP_SCOPES
from .services import (
    MCPServiceError,
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
    _item_for_client,
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
            "contract_version": "1.0",
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
                "idempotency_ttl_seconds": settings.MCP_IDEMPOTENCY_TTL_SECONDS,
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
            "media": {"read_expansion": True, "writes": False},
            "links": {"read_expansion": True, "writes": False},
            "importers": [],
            "export_formats": [],
            "deferred_issue": 90,
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


application = mcp.streamable_http_app()
