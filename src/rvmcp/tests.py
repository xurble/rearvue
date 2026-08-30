import json
import threading
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from starlette.testclient import TestClient

from rvsite.models import RVDomain, RVItem, RVService

from .auth import MCPAuthenticationMiddleware, current_client_id
from .models import MCPAuditRecord, MCPClient, MCPIdempotencyRecord
from .server import get_domain, get_item, get_service, item_search, list_domains, list_services
from .services import (
    MCPServiceError,
    authenticate_token,
    bulk_upsert_items,
    create_item,
    idempotent_result,
    search_items,
    serialize_item,
    update_item,
    upsert_item,
)


ALL_SCOPES = ["domains:read", "services:read", "items:read", "items:raw", "items:write"]


class MCPTestMixin:
    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_user(username="owner")
        self.domain = RVDomain.objects.create(name="archive.example", owner=user)
        self.other_domain = RVDomain.objects.create(name="private.example", owner=user)
        self.service = RVService.objects.create(
            name="Twitter",
            domain=self.domain,
            type=RVService.Type.TWITTER,
            config={"username": "visible-but-legacy"},
            credentials={"access_token": "must-never-leak"},
            state={"opaque": "must-never-leak"},
        )
        self.other_service = RVService.objects.create(
            name="Private RSS",
            domain=self.other_domain,
            type=RVService.Type.RSS,
            credentials={"secret": "private"},
        )
        self.client = MCPClient(name="agent", scopes=ALL_SCOPES)
        self.token = self.client.rotate_token()
        self.client.full_clean()
        self.client.save()
        self.client.domains.add(self.domain)

    def payload(self, item_id="source-1", **changes):
        payload = {
            "service_id": self.service.id,
            "item_id": item_id,
            "datetime_created": "2025-01-02T03:04:05+00:00",
            "title": "A title",
            "caption": "hello",
            "raw_data": {"source": "payload"},
        }
        payload.update(changes)
        return payload


class MCPClientAuthenticationTests(MCPTestMixin, TestCase):
    def test_authenticates_hashed_token_without_storing_plaintext(self):
        self.assertEqual(authenticate_token(self.token), self.client)
        self.assertNotEqual(self.client.token_hash, self.token)
        self.assertIsNone(authenticate_token(self.token + "wrong"))

    def test_disabled_and_expired_clients_are_rejected(self):
        self.client.enabled = False
        self.client.save(update_fields=["enabled"])
        self.assertIsNone(authenticate_token(self.token))
        self.client.enabled = True
        self.client.expires_at = datetime.now(datetime_timezone.utc) - timedelta(seconds=1)
        self.client.save(update_fields=["enabled", "expires_at"])
        self.assertIsNone(authenticate_token(self.token))


class MCPItemServiceTests(MCPTestMixin, TestCase):
    def test_create_derives_domain_sanitizes_caption_and_bounds_raw_data(self):
        item = create_item(
            self.client,
            self.payload(caption='<script>x()</script><a href="javascript:bad">safe</a>', caption_format="html"),
        )

        self.assertEqual(item.domain, self.domain)
        self.assertNotIn("script", item.caption)
        self.assertNotIn("javascript", item.caption)
        self.assertEqual(item.revision, 1)
        self.assertEqual(json.loads(item.raw_data), {"source": "payload"})
        self.domain.refresh_from_db()
        self.assertEqual((self.domain.min_year, self.domain.max_year), (2025, 2025))
        self.assertEqual(MCPAuditRecord.objects.get().affected_ids, [item.id])

    def test_plain_caption_is_escaped_for_legacy_safe_template(self):
        item = create_item(self.client, self.payload(caption="<b>not html</b>\nnext"))
        self.assertEqual(item.caption, "&lt;b&gt;not html&lt;/b&gt;<br>next")

    @override_settings(MCP_MAX_RAW_DATA_BYTES=10)
    def test_rejects_oversized_raw_data(self):
        with self.assertRaisesMessage(MCPServiceError, "raw_data exceeds"):
            create_item(self.client, self.payload(raw_data={"long": "value"}))

    def test_unique_external_identity_is_enforced(self):
        create_item(self.client, self.payload())
        with self.assertRaises(MCPServiceError) as caught:
            create_item(self.client, self.payload())
        self.assertEqual(caught.exception.code, "identity_conflict")

    def test_model_wide_revision_and_mcp_conflict_protection(self):
        item = create_item(self.client, self.payload())
        item.title = "Admin edit"
        item.save(update_fields=["title"])
        self.assertEqual(item.revision, 2)

        with self.assertRaises(MCPServiceError) as caught:
            update_item(self.client, item.id, {"title": "stale", "expected_revision": 1})
        self.assertEqual(caught.exception.code, "revision_conflict")
        self.assertEqual(caught.exception.details, {"current_revision": 2})

        updated = update_item(self.client, item.id, {"title": "current", "expected_revision": 2})
        self.assertEqual(updated.revision, 3)
        self.assertEqual(updated.title, "current")

    def test_upsert_is_noop_when_equal_and_requires_revision_to_change(self):
        item = create_item(self.client, self.payload())
        same, created = upsert_item(self.client, self.payload())
        self.assertFalse(created)
        self.assertEqual(same.revision, item.revision)

        with self.assertRaises(MCPServiceError) as caught:
            upsert_item(self.client, self.payload(title="changed"))
        self.assertEqual(caught.exception.code, "expected_revision_required")

    def test_domain_isolation_hides_items_and_services(self):
        hidden = RVItem.objects.create(
            service=self.other_service,
            domain=self.other_domain,
            item_id="hidden",
            date_created=datetime(2024, 1, 1).date(),
            datetime_created=datetime(2024, 1, 1, tzinfo=datetime_timezone.utc),
        )
        result = search_items(self.client)
        self.assertNotIn(hidden.id, [item["id"] for item in result["items"]])
        with self.assertRaises(MCPServiceError) as caught:
            search_items(self.client, {"domain_ids": [self.other_domain.id]})
        self.assertEqual(caught.exception.code, "not_found")

    def test_search_uses_deterministic_cursor_and_filter_binding(self):
        for index in range(3):
            create_item(
                self.client,
                self.payload(
                    item_id=f"item-{index}",
                    datetime_created=f"2025-01-0{index + 1}T00:00:00+00:00",
                    title=f"match {index}",
                ),
            )
        first = search_items(self.client, {"text": "match"}, limit=2)
        second = search_items(self.client, {"text": "match"}, cursor=first["next_cursor"], limit=2)
        ids = [item["id"] for item in first["items"] + second["items"]]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)
        with self.assertRaises(MCPServiceError) as caught:
            search_items(self.client, {"text": "different"}, cursor=first["next_cursor"], limit=2)
        self.assertEqual(caught.exception.code, "invalid_cursor")

    def test_raw_data_requires_dedicated_read_scope(self):
        item = create_item(self.client, self.payload())
        self.assertIn("raw_data", serialize_item(item, self.client))
        self.client.scopes.remove("items:raw")
        self.client.save(update_fields=["scopes"])
        self.assertNotIn("raw_data", serialize_item(item, self.client))

    def test_bulk_reports_every_record_and_replays_idempotently(self):
        records = [self.payload("good"), self.payload("bad", service_id=self.other_service.id)]
        first = bulk_upsert_items(self.client, records, "bulk-key")
        second = bulk_upsert_items(self.client, records, "bulk-key")

        self.assertEqual(first, second)
        self.assertEqual(first["submitted_count"], 2)
        self.assertEqual((first["succeeded_count"], first["failed_count"]), (1, 1))
        self.assertEqual([result["index"] for result in first["results"]], [0, 1])
        self.assertEqual(MCPIdempotencyRecord.objects.count(), 1)
        audit = MCPAuditRecord.objects.get(operation="bulk_upsert_items")
        self.assertEqual(audit.outcome, MCPAuditRecord.Outcome.PARTIAL)

        changed = [self.payload("different")]
        with self.assertRaises(MCPServiceError) as caught:
            bulk_upsert_items(self.client, changed, "bulk-key")
        self.assertEqual(caught.exception.code, "idempotency_conflict")

    def test_scope_is_required(self):
        self.client.scopes = ["items:read"]
        self.client.save(update_fields=["scopes"])
        with self.assertRaises(MCPServiceError) as caught:
            create_item(self.client, self.payload())
        self.assertEqual(caught.exception.code, "forbidden")
        failed_audit = MCPAuditRecord.objects.get(operation="create_item")
        self.assertEqual(failed_audit.outcome, MCPAuditRecord.Outcome.FAILURE)
        self.assertEqual(failed_audit.details, {"code": "forbidden"})

    def test_failed_mutation_validation_is_audited_without_payload_content(self):
        payload = self.payload(caption="sensitive caption")
        del payload["datetime_created"]
        with self.assertRaises(MCPServiceError):
            create_item(self.client, payload)

        failed_audit = MCPAuditRecord.objects.get(operation="create_item")
        self.assertEqual(failed_audit.outcome, MCPAuditRecord.Outcome.FAILURE)
        self.assertEqual(
            failed_audit.details,
            {"code": "validation_error", "path": "datetime_created"},
        )
        self.assertNotIn("sensitive", json.dumps(failed_audit.details))

    @override_settings(MCP_MAX_RAW_DATA_BYTES=10)
    def test_failed_singular_mutation_retains_authorized_domain(self):
        with self.assertRaises(MCPServiceError):
            create_item(self.client, self.payload(raw_data={"long": "value"}))
        failed_audit = MCPAuditRecord.objects.get(operation="create_item")
        self.assertEqual(failed_audit.domain, self.domain)
        self.assertEqual(failed_audit.domain_name, self.domain.name)

    def test_upsert_recovers_when_concurrent_writer_wins_identity_race(self):
        from rvmcp import services

        original_create = services._create_item

        def concurrent_winner(client, payload):
            original_create(client, payload)
            raise MCPServiceError("identity_conflict", "simulated concurrent winner")

        with patch("rvmcp.services._create_item", side_effect=concurrent_winner):
            item, created = upsert_item(self.client, self.payload("raced"))

        self.assertFalse(created)
        self.assertEqual(item.item_id, "raced")
        self.assertEqual(RVItem.objects.filter(service=self.service, item_id="raced").count(), 1)
        self.assertEqual(MCPAuditRecord.objects.get().outcome, MCPAuditRecord.Outcome.SUCCESS)

    def test_idempotency_claim_exists_before_callback_side_effects(self):
        callback_calls = []

        def callback():
            callback_calls.append("called")
            self.assertTrue(
                MCPIdempotencyRecord.objects.filter(
                    client=self.client, operation="probe", key="claimed-first"
                ).exists()
            )
            return {"ok": True}

        first = idempotent_result(self.client, "probe", "claimed-first", {"value": 1}, callback)
        second = idempotent_result(self.client, "probe", "claimed-first", {"value": 1}, callback)
        self.assertEqual(first, second)
        self.assertEqual(callback_calls, ["called"])

    def test_all_failed_bulk_audit_retains_single_authorized_domain(self):
        result = bulk_upsert_items(
            self.client,
            [self.payload("invalid", datetime_created="not-a-datetime")],
            "failed-domain",
        )
        self.assertEqual(result["failed_count"], 1)
        failed_audit = MCPAuditRecord.objects.get(operation="bulk_upsert_items")
        self.assertEqual(failed_audit.domain, self.domain)
        self.assertEqual(failed_audit.details["domain_ids"], [self.domain.id])

    def test_read_scopes_are_enforced_for_each_operation_family(self):
        self.client.scopes = []
        self.client.save(update_fields=["scopes"])
        token = current_client_id.set(self.client.id)
        try:
            results = [
                async_to_sync(list_domains)(),
                async_to_sync(get_domain)(self.domain.id),
                async_to_sync(list_services)(),
                async_to_sync(get_service)(self.service.id),
                async_to_sync(item_search)(),
                async_to_sync(get_item)(999),
            ]
        finally:
            current_client_id.reset(token)
        self.assertTrue(all(result["error"]["code"] == "forbidden" for result in results))

    def test_get_operations_do_not_reveal_other_domain_records(self):
        hidden = RVItem.objects.create(
            service=self.other_service,
            domain=self.other_domain,
            item_id="hidden-get",
            date_created=datetime(2024, 1, 1).date(),
            datetime_created=datetime(2024, 1, 1, tzinfo=datetime_timezone.utc),
        )
        token = current_client_id.set(self.client.id)
        try:
            service_result = async_to_sync(get_service)(self.other_service.id)
            item_result = async_to_sync(get_item)(hidden.id)
        finally:
            current_client_id.reset(token)
        self.assertEqual(service_result["error"]["code"], "not_found")
        self.assertEqual(item_result["error"]["code"], "not_found")

    def test_service_tool_never_exposes_legacy_documents(self):
        token = current_client_id.set(self.client.id)
        try:
            result = async_to_sync(get_service)(self.service.id)
        finally:
            current_client_id.reset(token)
        serialized = json.dumps(result)
        self.assertTrue(result["ok"])
        self.assertNotIn("credentials", serialized)
        self.assertNotIn("must-never-leak", serialized)
        self.assertNotIn("visible-but-legacy", serialized)


class MCPAuthenticationMiddlewareTests(MCPTestMixin, TestCase):
    def run_request(self, headers=None, enabled=True):
        messages = []

        async def downstream(scope, receive, send):
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def execute():
            received = False

            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                return {"type": "http.disconnect"}

            async def send(message):
                messages.append(message)

            middleware = MCPAuthenticationMiddleware(downstream)
            await middleware(
                {"type": "http", "method": "POST", "path": "/", "headers": headers or []},
                receive,
                send,
            )

        with override_settings(MCP_ENABLED=enabled):
            async_to_sync(execute)()
        return messages[0]["status"], dict(messages[0].get("headers", []))

    def test_disabled_server_is_not_discoverable(self):
        status, _ = self.run_request(enabled=False)
        self.assertEqual(status, 404)

    def test_requires_bearer_token(self):
        status, headers = self.run_request()
        self.assertEqual(status, 401)
        self.assertIn(b"www-authenticate", headers)

    def test_rejects_unlisted_browser_origin(self):
        status, _ = self.run_request(
            headers=[
                (b"authorization", f"Bearer {self.token}".encode()),
                (b"origin", b"https://untrusted.example"),
            ]
        )
        self.assertEqual(status, 403)

    @override_settings(MCP_ALLOWED_ORIGINS=["https://trusted.example"])
    def test_accepts_authenticated_allowed_origin(self):
        status, _ = self.run_request(
            headers=[
                (b"authorization", f"Bearer {self.token}".encode()),
                (b"origin", b"https://trusted.example"),
            ]
        )
        self.assertEqual(status, 204)


class MCPTransportContractTests(MCPTestMixin, TransactionTestCase):
    def test_streamable_http_initializes_and_publishes_versioned_tools(self):
        from rearvue.asgi import application

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/event-stream",
        }
        with TestClient(application) as transport:
            with override_settings(MCP_ENABLED=False):
                self.assertEqual(transport.post("/mcp").status_code, 404)
            initialized = transport.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "RearVue tests", "version": "1"},
                    },
                },
            )
            self.assertEqual(initialized.status_code, 200)
            protocol_version = initialized.json()["result"]["protocolVersion"]
            listed = transport.post(
                "/mcp",
                headers={**headers, "MCP-Protocol-Version": protocol_version},
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            discovered = transport.post(
                "/mcp",
                headers={**headers, "MCP-Protocol-Version": protocol_version},
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "rearvue_v1_discover", "arguments": {}},
                },
            )

        self.assertEqual(listed.status_code, 200)
        tools = listed.json()["result"]["tools"]
        names = {tool["name"] for tool in tools}
        self.assertEqual(
            names,
            {
                "rearvue_v1_discover",
                "rearvue_v1_list_domains",
                "rearvue_v1_get_domain",
                "rearvue_v1_list_services",
                "rearvue_v1_get_service",
                "rearvue_v1_search_items",
                "rearvue_v1_get_item",
                "rearvue_v1_create_item",
                "rearvue_v1_upsert_item",
                "rearvue_v1_update_item",
                "rearvue_v1_bulk_upsert_items",
            },
        )
        create_schema = next(tool for tool in tools if tool["name"] == "rearvue_v1_create_item")["inputSchema"]
        self.assertEqual(
            set(create_schema["$defs"]["ItemCreateInput"]["required"]),
            {"service_id", "item_id", "datetime_created"},
        )
        self.assertEqual(discovered.status_code, 200)
        structured = discovered.json()["result"]["structuredContent"]
        self.assertEqual(structured["contract_version"], "1.0")


class MCPConcurrencyTests(MCPTestMixin, TransactionTestCase):
    reset_sequences = True

    def run_concurrently(self, callback):
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                results.append(callback())
            except Exception as exc:  # captured for assertion in the main test thread
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        return results

    def test_same_key_concurrent_requests_execute_callback_once(self):
        callback_calls = []
        callback_lock = threading.Lock()

        def request():
            client = MCPClient.objects.get(pk=self.client.pk)

            def callback():
                with callback_lock:
                    callback_calls.append("called")
                return {"ok": True, "winner": "stable"}

            return idempotent_result(
                client,
                "concurrent_probe",
                "same-key",
                {"value": 1},
                callback,
            )

        results = self.run_concurrently(request)
        self.assertEqual(results, [{"ok": True, "winner": "stable"}] * 2)
        self.assertEqual(callback_calls, ["called"])
        self.assertEqual(
            MCPIdempotencyRecord.objects.filter(operation="concurrent_probe", key="same-key").count(),
            1,
        )

    def test_concurrent_upserts_create_one_identity_and_share_winner(self):
        payload = self.payload("concurrent-item")

        def request():
            client = MCPClient.objects.get(pk=self.client.pk)
            item, created = upsert_item(client, payload)
            return item.id, created

        results = self.run_concurrently(request)
        self.assertEqual(sorted(created for _, created in results), [False, True])
        self.assertEqual(len({item_id for item_id, _ in results}), 1)
        self.assertEqual(
            RVItem.objects.filter(service=self.service, item_id="concurrent-item").count(),
            1,
        )
