import base64
import io
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from rvsite.models import RVDomain, RVItem, RVLink, RVMedia, RVService

from .archive_import import import_twitter_archive, submit_twitter_archive
from .capabilities import (
    create_link,
    create_media,
    create_service,
    get_link,
    get_media,
    list_jobs,
    list_links,
    list_media,
    submit_processing_job,
    update_link,
    update_media,
    update_service,
)
from .destruction import confirm_delete, preview_delete
from .exports import export_json_page, submit_export
from .jobs import run_one_job
from .models import MCPAuditRecord, MCPClient, MCPDestructivePreview, MCPJob
from .services import MCPServiceError


class MCPCapabilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="capability-owner", is_superuser=True)
        self.domain = RVDomain.objects.create(name="archive.example", owner=self.user)
        self.other_domain = RVDomain.objects.create(name="private.example", owner=self.user)
        self.service = RVService.objects.create(
            name="Twitter",
            domain=self.domain,
            type=RVService.Type.TWITTER,
            credentials={"secret": "never-return"},
        )
        self.other_service = RVService.objects.create(
            name="Private",
            domain=self.other_domain,
            type=RVService.Type.RSS,
        )
        self.item = RVItem.objects.create(
            service=self.service,
            domain=self.domain,
            item_id="item-1",
            date_created="2025-01-01",
            datetime_created="2025-01-01T00:00:00Z",
        )
        self.other_item = RVItem.objects.create(
            service=self.other_service,
            domain=self.other_domain,
            item_id="private-item",
            date_created="2025-01-01",
            datetime_created="2025-01-01T00:00:00Z",
        )
        self.client_record = MCPClient(name="capability-agent", scopes=["domain:owner"])
        self.token = self.client_record.rotate_token()
        self.client_record.save()
        self.client_record.domains.add(self.domain)

    def png_base64(self, color="red"):
        output = io.BytesIO()
        Image.new("RGB", (2, 2), color=color).save(output, format="PNG")
        return base64.b64encode(output.getvalue()).decode()

    @patch("rvmcp.capabilities.validate_public_http_url", side_effect=lambda value: value)
    def test_link_crud_is_revisioned_audited_and_domain_scoped(self, _validate):
        link = create_link(
            self.client_record,
            {"item_id": self.item.id, "url": "https://example.com", "title": "Example"},
        )
        self.assertEqual(get_link(self.client_record, link.id)["title"], "Example")
        self.assertEqual(list_links(self.client_record, item_id=self.item.id)["links"][0]["id"], link.id)
        link = update_link(
            self.client_record,
            link.id,
            {"expected_revision": link.revision, "description": "Changed"},
        )
        self.assertEqual((link.description, link.revision), ("Changed", 2))
        private = RVLink.objects.create(item=self.other_item, url="https://private.example")
        with self.assertRaises(MCPServiceError) as caught:
            get_link(self.client_record, private.id)
        self.assertEqual(caught.exception.code, "not_found")
        self.assertEqual(
            set(MCPAuditRecord.objects.values_list("operation", flat=True)),
            {"create_link", "update_link"},
        )

    def test_media_inline_create_replace_and_authenticated_download(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(
            DATA_STORE=directory,
            MCP_GENERATED_ROOT=str(Path(directory) / "mcp-generated"),
        ):
            media = create_media(
                self.client_record,
                {"item_id": self.item.id, "content_base64": self.png_base64()},
            )
            serialized = get_media(self.client_record, media.id)
            self.assertEqual(serialized["mime_type"], "image/png")
            old_path = Path(directory) / media.original_media
            self.assertTrue(old_path.is_file())
            with self.captureOnCommitCallbacks(execute=True):
                media = update_media(
                    self.client_record,
                    media.id,
                    {"expected_revision": media.revision, "content_base64": self.png_base64("blue")},
                )
            self.assertFalse(old_path.exists())
            self.assertEqual(list_media(self.client_record, item_id=self.item.id)["media"][0]["id"], media.id)

            unauthenticated = self.client.get(f"/mcp-download/media/{media.id}/")
            self.assertEqual(unauthenticated.status_code, 401)
            response = self.client.get(
                f"/mcp-download/media/{media.id}/",
                HTTP_AUTHORIZATION=f"Bearer {self.token}",
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(b"".join(response.streaming_content).startswith(b"\x89PNG"))

    def test_media_rejects_unsupported_content_and_other_domain(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(
            DATA_STORE=directory,
            MCP_GENERATED_ROOT=str(Path(directory) / "mcp-generated"),
        ):
            with self.assertRaises(MCPServiceError) as caught:
                create_media(
                    self.client_record,
                    {"item_id": self.item.id, "content_base64": base64.b64encode(b"not-media").decode()},
                )
            self.assertEqual(caught.exception.code, "unsupported_media")
            with self.assertRaises(MCPServiceError) as caught:
                create_media(
                    self.client_record,
                    {"item_id": self.other_item.id, "content_base64": self.png_base64()},
                )
            self.assertEqual(caught.exception.code, "not_found")

    @patch("rvmcp.capabilities.validate_public_http_url", return_value=None)
    def test_link_and_remote_media_reject_non_public_or_oversized_sources(self, _validate):
        with self.assertRaises(MCPServiceError) as caught:
            create_link(
                self.client_record,
                {"item_id": self.item.id, "url": "http://127.0.0.1/private"},
            )
        self.assertEqual(caught.exception.code, "unsafe_url")

        with self.assertRaises(MCPServiceError) as caught:
            create_media(
                self.client_record,
                {"item_id": self.item.id, "source_url": "http://169.254.169.254/latest"},
            )
        self.assertEqual(caught.exception.code, "unsafe_url")

    @patch("rvmcp.capabilities.validate_public_http_url", side_effect=lambda value: value)
    def test_remote_media_stream_stops_at_configured_byte_limit(self, _validate):
        response = Mock()
        response.headers = {}
        response.iter_content.return_value = [b"12345"]
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(
                DATA_STORE=directory,
                MCP_GENERATED_ROOT=str(Path(directory) / "mcp-generated"),
                MCP_MAX_MEDIA_BYTES=4,
            ),
            patch("rvmcp.capabilities.get_public_url", return_value=response),
            self.assertRaises(MCPServiceError) as caught,
        ):
            create_media(
                self.client_record,
                {"item_id": self.item.id, "source_url": "https://cdn.example/media"},
            )
        self.assertEqual(caught.exception.code, "limit_exceeded")
        response.close.assert_called_once()

    def test_media_download_rejects_symlink_even_inside_generated_storage(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(
            DATA_STORE=directory,
            MCP_GENERATED_ROOT=str(Path(directory) / "mcp-generated"),
        ):
            media = create_media(
                self.client_record,
                {"item_id": self.item.id, "content_base64": self.png_base64()},
            )
            stored = Path(directory) / media.original_media
            outside = Path(directory) / "outside.png"
            outside.write_bytes(b"private")
            stored.unlink()
            stored.symlink_to(outside)
            response = self.client.get(
                f"/mcp-download/media/{media.id}/",
                HTTP_AUTHORIZATION=f"Bearer {self.token}",
            )
            self.assertEqual(response.status_code, 404)

    def test_service_mutation_accepts_only_non_secret_fields_and_keeps_type_immutable(self):
        service = create_service(
            self.client_record,
            {"domain_id": self.domain.id, "name": "RSS", "type": "rss", "live": False},
        )
        service = update_service(
            self.client_record,
            service.id,
            {"expected_revision": service.revision, "name": "Renamed", "hide_unmoderated": True},
        )
        self.assertEqual((service.name, service.type, service.hide_unmoderated), ("Renamed", "rss", True))
        with self.assertRaises(MCPServiceError):
            update_service(
                self.client_record,
                service.id,
                {"expected_revision": service.revision, "credentials": {"token": "bad"}},
            )

    def test_job_tools_are_domain_scoped_and_reject_arbitrary_operations(self):
        job = submit_processing_job(self.client_record, self.domain.id, "domain_metadata_refresh")
        self.assertEqual(list_jobs(self.client_record)["jobs"][0]["id"], job.id)
        self.assertNotIn("payload", list_jobs(self.client_record)["jobs"][0])
        with self.assertRaises(MCPServiceError):
            submit_processing_job(self.client_record, self.domain.id, "shell", {"command": "id"})
        MCPJob.objects.create(
            client=self.client_record,
            domain=self.other_domain,
            operation="domain_metadata_refresh",
        )
        self.assertEqual(len(list_jobs(self.client_record)["jobs"]), 1)

    def test_json_export_is_snapshot_bound_resumable_and_secret_free(self):
        RVLink.objects.create(item=self.item, url="https://example.com", title="Link")
        RVMedia.objects.create(item=self.item, media_type=1, original_media="secret/path.jpg")
        cursor = None
        exported = []
        snapshot = None
        while True:
            page = export_json_page(
                self.client_record,
                {"domain_ids": [self.domain.id]},
                cursor=cursor,
                limit=2,
            )
            snapshot = snapshot or page["snapshot"]
            self.assertEqual(page["snapshot"], snapshot)
            exported.extend(page["records"])
            if cursor is None:
                RVItem.objects.create(
                    service=self.service,
                    domain=self.domain,
                    item_id="after-snapshot",
                    date_created="2026-01-01",
                    datetime_created="2026-01-01T00:00:00Z",
                )
            cursor = page["next_cursor"]
            if cursor is None:
                break
        encoded = json.dumps(exported)
        self.assertNotIn("never-return", encoded)
        self.assertNotIn("secret/path.jpg", encoded)
        self.assertNotIn("after-snapshot", encoded)
        self.assertEqual(
            {entry["kind"] for entry in exported},
            {"domains", "services", "items", "media_manifest", "links"},
        )
        first = export_json_page(self.client_record, {"domain_ids": [self.domain.id]}, limit=1)
        with self.assertRaises(MCPServiceError) as caught:
            export_json_page(
                self.client_record,
                {"domain_ids": [self.domain.id], "kinds": ["items"]},
                cursor=first["next_cursor"],
                limit=1,
            )
        self.assertEqual(caught.exception.code, "invalid_cursor")

    def test_json_export_preserves_record_values_when_source_changes_between_pages(self):
        later = RVItem.objects.create(
            service=self.service,
            domain=self.domain,
            item_id="item-2",
            date_created="2025-01-02",
            datetime_created="2025-01-02T00:00:00Z",
            title="before snapshot",
        )
        first = export_json_page(
            self.client_record,
            {"domain_ids": [self.domain.id], "kinds": ["items"]},
            limit=1,
        )

        later.title = "after snapshot"
        later.save(update_fields=["title"])
        second = export_json_page(
            self.client_record,
            {"domain_ids": [self.domain.id], "kinds": ["items"]},
            cursor=first["next_cursor"],
            limit=1,
        )

        self.assertEqual(second["records"][0]["record"]["id"], later.id)
        self.assertEqual(second["records"][0]["record"]["title"], "before snapshot")
        self.assertIsNone(second["next_cursor"])

    def test_async_export_creates_authenticated_checked_artifact(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(
            DATA_STORE=directory,
            MCP_GENERATED_ROOT=str(Path(directory) / "mcp-generated"),
        ):
            job = submit_export(self.client_record, self.domain.id)
            self.assertTrue(run_one_job("export-worker"))
            job.refresh_from_db()
            self.assertEqual(job.status, MCPJob.Status.SUCCEEDED)
            self.assertTrue(job.artifact_sha256)
            response = self.client.get(
                f"/mcp-download/jobs/{job.id}/",
                HTTP_AUTHORIZATION=f"Bearer {self.token}",
            )
            self.assertEqual(response.status_code, 200)
            body = b"".join(response.streaming_content)
            self.assertEqual(len(body), job.artifact_size)

    def twitter_archive(self):
        return (
            "window.YTD.tweets.part0 = "
            + json.dumps(
                [
                    {
                        "tweet": {
                            "id": "123",
                            "created_at": "Wed Oct 10 20:19:24 +0000 2018",
                            "full_text": "<script>alert(1)</script> hello",
                            "entities": {},
                        }
                    },
                    {"bad": "record"},
                ]
            )
        )

    def test_twitter_archive_shared_import_is_idempotent_sanitized_and_job_backed(self):
        result = import_twitter_archive(self.service, self.twitter_archive())
        self.assertEqual((result["processed_count"], result["failed_count"]), (1, 1))
        item = RVItem.objects.get(service=self.service, item_id="123")
        self.assertNotIn("<script>", item.caption)
        self.assertIn("&lt;script&gt;", item.caption)
        import_twitter_archive(self.service, self.twitter_archive())
        self.assertEqual(RVItem.objects.filter(service=self.service, item_id="123").count(), 1)

        archive = base64.b64encode(self.twitter_archive().encode()).decode()
        job = submit_twitter_archive(self.client_record, self.domain.id, self.service.id, archive)
        self.assertTrue(run_one_job("archive-worker"))
        job.refresh_from_db()
        self.assertEqual(job.status, MCPJob.Status.SUCCEEDED)
        self.assertEqual(len(job.failures), 1)
        self.assertNotIn("archive_base64", json.dumps(job.result))

        with override_settings(MCP_MAX_ARCHIVE_RECORDS=1):
            with self.assertRaises(MCPServiceError) as caught:
                import_twitter_archive(self.service, self.twitter_archive())
            self.assertEqual(caught.exception.code, "limit_exceeded")

    @override_settings(ALLOWED_HOSTS=["archive.example"])
    @patch("rvadmin.views.import_twitter_archive")
    def test_admin_twitter_upload_uses_shared_importer(self, shared_import):
        shared_import.return_value = {
            "processed_count": 1,
            "skipped_count": 0,
            "failed_count": 0,
        }
        self.client.force_login(self.user)
        archive = SimpleUploadedFile("tweets.js", self.twitter_archive().encode(), content_type="text/javascript")
        response = self.client.post(
            f"/rvadmin/twitter_connect/{self.service.id}/",
            {"action": "archive", "archive": archive},
            HTTP_HOST=self.domain.name,
        )
        self.assertEqual(response.status_code, 302)
        called_service, called_archive = shared_import.call_args.args
        self.assertEqual(called_service, self.service)
        self.assertTrue(called_archive.startswith(b"window.YTD.tweets.part0"))

    def test_confirmed_delete_is_single_use_and_revalidates_impact(self):
        link = RVLink.objects.create(item=self.item, url="https://example.com")
        preview = preview_delete(self.client_record, self.domain.id, "links", [link.id])
        with self.assertRaises(MCPServiceError) as caught:
            confirm_delete(self.client_record, preview["id"], "wrong")
        self.assertEqual(caught.exception.code, "invalid_confirmation")
        result = confirm_delete(
            self.client_record,
            preview["id"],
            preview["confirmation_token"],
        )
        self.assertEqual(result["deleted"]["ids"], [link.id])
        self.assertFalse(RVLink.objects.filter(pk=link.id).exists())
        with self.assertRaises(MCPServiceError) as caught:
            confirm_delete(self.client_record, preview["id"], preview["confirmation_token"])
        self.assertEqual(caught.exception.code, "confirmation_used")

        link = RVLink.objects.create(item=self.item, url="https://example.net")
        preview = preview_delete(self.client_record, self.domain.id, "links", [link.id])
        link.title = "changed"
        link.save(update_fields=["title"])
        with self.assertRaises(MCPServiceError) as caught:
            confirm_delete(self.client_record, preview["id"], preview["confirmation_token"])
        self.assertEqual(caught.exception.code, "impact_changed")

    def test_item_delete_clears_poster_and_reports_controlled_cleanup(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(
            DATA_STORE=directory,
            MCP_GENERATED_ROOT=str(Path(directory) / "mcp-generated"),
        ):
            media = create_media(
                self.client_record,
                {"item_id": self.item.id, "content_base64": self.png_base64()},
            )
            stored = Path(directory) / media.original_media
            self.domain.poster_image = self.item
            self.domain.save(update_fields=["poster_image"])
            preview = preview_delete(self.client_record, self.domain.id, "items", [self.item.id])
            with self.captureOnCommitCallbacks(execute=True):
                confirm_delete(
                    self.client_record,
                    preview["id"],
                    preview["confirmation_token"],
                )
            self.domain.refresh_from_db()
            self.assertIsNone(self.domain.poster_image)
            self.assertFalse(stored.exists())
            audit_record = MCPAuditRecord.objects.filter(operation="confirm_delete").latest("id")
            self.assertEqual(audit_record.details["cleanup_failures"], [])

    @override_settings(MCP_DESTRUCTIVE_PREVIEW_TTL_SECONDS=1)
    def test_delete_preview_is_bounded_expiring_and_domain_scoped(self):
        private = RVLink.objects.create(item=self.other_item, url="https://private.example")
        with self.assertRaises(MCPServiceError) as caught:
            preview_delete(self.client_record, self.other_domain.id, "links", [private.id])
        self.assertEqual(caught.exception.code, "not_found")
        preview = preview_delete(
            self.client_record,
            self.domain.id,
            "items",
            [self.item.id],
        )
        MCPDestructivePreview.objects.filter(pk=preview["id"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        with self.assertRaises(MCPServiceError) as caught:
            confirm_delete(self.client_record, preview["id"], preview["confirmation_token"])
        self.assertEqual(caught.exception.code, "confirmation_expired")
        with override_settings(MCP_MAX_DESTRUCTIVE_RECORDS=0):
            with self.assertRaises(MCPServiceError) as caught:
                preview_delete(self.client_record, self.domain.id, "items", [self.item.id])
            self.assertEqual(caught.exception.code, "limit_exceeded")
