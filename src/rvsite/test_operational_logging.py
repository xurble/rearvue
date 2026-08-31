import json
import logging
from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from feeds.models import Enclosure, Post, Source
from flickrapi.exceptions import FlickrError
from PIL import Image

from rvservices.flickr_service import mirror_flickr, update_flickr
from rvservices.instagram_graph_service import mirror_instagram
from rvservices.results import OperationResult, log_safe_exception
from rvservices.rss_service import fix_rss_item, mirror_rss
from rvservices.twitter_service import (
    find_twitter_links,
    fix_twitter_item,
    import_archive,
    mirror_twitter,
)
from rvsite.models import RVDomain, RVItem, RVMedia, RVService


class ServiceFailureLoggingTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user(username="operations-owner")
        self.domain = RVDomain.objects.create(
            name="operations.example.com", owner=owner
        )

    def create_service(self, service_type, **changes):
        values = {
            "name": f"{service_type} service",
            "domain": self.domain,
            "type": service_type,
            "last_checked": datetime(2015, 1, 1, tzinfo=UTC),
        }
        values.update(changes)
        return RVService.objects.create(**values)

    def create_item(self, service, **changes):
        values = {
            "service": service,
            "domain": self.domain,
            "item_id": "provider-item-1",
            "date_created": date(2026, 8, 30),
            "datetime_created": datetime(2026, 8, 30, 12, tzinfo=UTC),
        }
        values.update(changes)
        return RVItem.objects.create(**values)

    def test_missing_archive_item_is_created_with_explicit_upsert(self):
        service = self.create_service("twitter")
        archive = [
            {
                "tweet": {
                    "id": "new-tweet",
                    "full_text": "A new archive item",
                    "created_at": "Sun Aug 30 12:00:00 +0000 2026",
                    "entities": {"user_mentions": [], "urls": []},
                }
            }
        ]

        result = import_archive(service, archive)

        self.assertEqual(result, OperationResult(processed=1))
        self.assertTrue(
            RVItem.objects.filter(service=service, item_id="new-tweet").exists()
        )

    def test_database_error_is_recorded_as_failure_not_missing_row(self):
        service = self.create_service("twitter")
        archive = [
            {
                "tweet": {
                    "id": "new-tweet",
                    "full_text": "A new archive item",
                    "created_at": "Sun Aug 30 12:00:00 +0000 2026",
                    "entities": {"user_mentions": [], "urls": []},
                }
            }
        ]

        with (
            patch(
                "rvservices.twitter_service.RVItem.objects.get_or_create",
                side_effect=DatabaseError("database unavailable"),
            ),
            self.assertLogs("rvservices.twitter_service", level="ERROR") as logs,
        ):
            result = import_archive(service, archive)

        self.assertEqual(result, OperationResult(failed=1))
        self.assertIn("error_type=DatabaseError", " ".join(logs.output))
        self.assertFalse(RVItem.objects.filter(item_id="new-tweet").exists())

    @patch(
        "rvservices.twitter_service.utils.final_destination",
        side_effect=lambda url: url,
    )
    @patch(
        "rvservices.twitter_service.utils.make_link",
        return_value=(True, ""),
    )
    def test_archive_url_discovery_stops_at_html_delimiters(
        self, make_link, final_destination
    ):
        service = self.create_service("twitter")
        item = self.create_item(
            service,
            caption="Visit https://example.com/path<br>after",
            raw_data=json.dumps({"entities": {"urls": []}}),
            mirror_state=1,
        )

        result = find_twitter_links(specific_item=item)

        item.refresh_from_db()
        self.assertEqual(result, OperationResult(processed=1))
        final_destination.assert_called_once_with("https://example.com/path")
        make_link.assert_called_once_with("https://example.com/path", item)
        self.assertEqual(
            item.caption,
            'Visit <a href="https://example.com/path">example.com/path</a><br>after',
        )
        self.assertEqual(item.mirror_state, 2)

    def test_malformed_twitter_payload_preserves_state_and_redacts_raw_data(self):
        service = self.create_service("twitter")
        secret = "auth_token=top-secret"
        item = self.create_item(service, raw_data=f"not json {secret}", mirror_state=0)

        with self.assertLogs("rvservices.twitter_service", level="ERROR") as logs:
            result = mirror_twitter(specific_item=item)

        item.refresh_from_db()
        output = " ".join(logs.output)
        self.assertEqual(result, OperationResult(failed=1))
        self.assertEqual(item.mirror_state, 0)
        self.assertIn("error_type=JSONDecodeError", output)
        self.assertNotIn(secret, output)

    @patch(
        "rvservices.instagram_graph_service.utils.validate_public_http_url",
        side_effect=lambda url: url,
    )
    @patch("rvservices.instagram_graph_service.requests.get")
    def test_provider_timeout_preserves_instagram_state_without_token_leak(
        self, request_get, _validate
    ):
        request_get.side_effect = requests.Timeout(
            "https://graph.instagram.com/media?access_token=top-secret"
        )
        service = self.create_service(
            "instagram",
            credentials={"access_token": "top-secret"},
            config={"user_id": "123"},
        )
        item = self.create_item(
            service,
            raw_data=json.dumps(
                {
                    "id": "provider-item-1",
                    "media_type": "IMAGE",
                    "media_url": "https://cdn.example.com/photo.jpg",
                }
            ),
            mirror_state=0,
        )

        with self.assertLogs(
            "rvservices.instagram_graph_service", level="ERROR"
        ) as logs:
            result = mirror_instagram(specific_item=item)

        item.refresh_from_db()
        output = " ".join(logs.output)
        self.assertEqual(result, OperationResult(failed=1))
        self.assertEqual(item.mirror_state, 0)
        self.assertIn("error_type=Timeout", output)
        self.assertNotIn("top-secret", output)
        self.assertFalse(item.rvmedia_set.exists())

    @patch("rvservices.twitter_service.utils.get_public_url")
    def test_media_decode_failure_preserves_twitter_state(self, get_public_url):
        response = SimpleNamespace(
            content=b"not an image",
            raise_for_status=lambda: None,
        )
        get_public_url.return_value = response
        service = self.create_service("twitter")
        item = self.create_item(
            service,
            raw_data=json.dumps(
                {
                    "entities": {
                        "media": [
                            {
                                "type": "photo",
                                "media_url_https": "https://cdn.example.com/photo.jpg",
                            }
                        ]
                    }
                }
            ),
            mirror_state=0,
        )

        with (
            TemporaryDirectory() as data_store,
            override_settings(DATA_STORE=data_store),
            self.assertLogs("rvservices.twitter_service", level="ERROR") as logs,
        ):
            result = mirror_twitter(specific_item=item)
            remaining_files = [path for path in Path(data_store).rglob("*") if path.is_file()]

        item.refresh_from_db()
        self.assertEqual(result, OperationResult(failed=1))
        self.assertEqual(item.mirror_state, 0)
        self.assertIn("error_type=UnidentifiedImageError", " ".join(logs.output))
        self.assertFalse(item.rvmedia_set.exists())
        self.assertEqual(remaining_files, [])

    @patch("rvservices.twitter_service.utils.get_public_url")
    def test_media_write_failure_preserves_twitter_state(self, get_public_url):
        response = SimpleNamespace(
            content=b"image bytes",
            raise_for_status=lambda: None,
        )
        get_public_url.return_value = response
        service = self.create_service("twitter")
        item = self.create_item(
            service,
            raw_data=json.dumps(
                {
                    "entities": {
                        "media": [
                            {
                                "type": "photo",
                                "media_url_https": "https://cdn.example.com/photo.jpg",
                            }
                        ]
                    }
                }
            ),
            mirror_state=0,
        )

        with (
            patch("builtins.open", side_effect=OSError(28, "disk full")),
            self.assertLogs("rvservices.twitter_service", level="ERROR") as logs,
        ):
            result = mirror_twitter(specific_item=item)

        item.refresh_from_db()
        self.assertEqual(result, OperationResult(failed=1))
        self.assertEqual(item.mirror_state, 0)
        self.assertIn("error_type=OSError errno=28", " ".join(logs.output))
        self.assertFalse(item.rvmedia_set.exists())

    @patch(
        "rvservices.twitter_service.utils.get_public_url",
        side_effect=requests.Timeout("provider timeout"),
    )
    def test_failed_twitter_repair_preserves_existing_media_and_retries(
        self, _get_public_url
    ):
        service = self.create_service("twitter")
        item = self.create_item(
            service,
            raw_data=json.dumps(
                {
                    "entities": {
                        "media": [
                            {
                                "type": "photo",
                                "media_url_https": "https://cdn.example.com/photo.jpg",
                            }
                        ]
                    }
                }
            ),
            mirror_state=2,
        )
        existing = RVMedia.objects.create(
            item=item,
            media_type=1,
            original_media="media/existing-original.jpg",
            primary_media="media/existing-primary.jpg",
            thumbnail="media/existing-thumbnail.jpg",
        )

        success, _message = fix_twitter_item(item.id)

        item.refresh_from_db()
        self.assertFalse(success)
        self.assertEqual(item.mirror_state, 0)
        self.assertQuerySetEqual(item.rvmedia_set.all(), [existing])

    def test_successful_twitter_repair_atomically_replaces_existing_media(self):
        service = self.create_service("twitter")
        item = self.create_item(
            service,
            raw_data=json.dumps(
                {
                    "entities": {
                        "media": [
                            {
                                "type": "photo",
                                "media_url_https": "https://cdn.example.com/photo.jpg",
                            }
                        ]
                    }
                }
            ),
            mirror_state=2,
        )
        existing = RVMedia.objects.create(
            item=item,
            media_type=1,
            original_media="media/existing-original.jpg",
        )

        def create_replacement(target_item, _media_data):
            RVMedia.objects.create(
                item=target_item,
                media_type=1,
                original_media="media/replacement-original.jpg",
            )

        with patch(
            "rvservices.twitter_service._mirror_twitter_media",
            side_effect=create_replacement,
        ):
            result = mirror_twitter(specific_item=item)

        item.refresh_from_db()
        self.assertEqual(result, OperationResult(processed=1))
        self.assertEqual(item.mirror_state, 1)
        self.assertFalse(RVMedia.objects.filter(id=existing.id).exists())
        self.assertEqual(
            item.rvmedia_set.get().original_media,
            "media/replacement-original.jpg",
        )

    @patch(
        "rvservices.rss_service.utils.get_public_url",
        side_effect=requests.Timeout("provider timeout"),
    )
    def test_failed_rss_repair_preserves_existing_media_and_retries(
        self, _get_public_url
    ):
        service = self.create_service("rss")
        source = Source.objects.create(feed_url="https://example.com/feed.xml")
        post = Post.objects.create(
            source=source,
            title="RSS item",
            body="",
            found=timezone.now(),
            created=timezone.now(),
            guid="rss-repair-item",
            index=1,
        )
        Enclosure.objects.create(
            post=post,
            href="https://cdn.example.com/photo.jpg",
            type="image/jpeg",
        )
        item = self.create_item(
            service,
            raw_data=str(post.id),
            mirror_state=2,
        )
        existing = RVMedia.objects.create(
            item=item,
            media_type=1,
            original_media="media/existing-original.jpg",
            primary_media="media/existing-primary.jpg",
            thumbnail="media/existing-thumbnail.jpg",
        )

        success, _message = fix_rss_item(item.id)

        item.refresh_from_db()
        self.assertFalse(success)
        self.assertEqual(item.mirror_state, 0)
        self.assertQuerySetEqual(item.rvmedia_set.all(), [existing])

    def test_missing_rss_post_does_not_starve_later_items(self):
        service = self.create_service("rss")
        source = Source.objects.create(feed_url="https://example.com/feed.xml")
        post = Post.objects.create(
            source=source,
            title="RSS item",
            body="",
            found=timezone.now(),
            created=timezone.now(),
            guid="valid-rss-item",
            index=1,
        )
        valid_item = self.create_item(
            service,
            item_id="valid-rss-item",
            raw_data=str(post.id),
            mirror_state=0,
        )
        stale_item = self.create_item(
            service,
            item_id="stale-rss-item",
            raw_data=str(post.id + 10_000),
            mirror_state=0,
        )

        result = mirror_rss()

        valid_item.refresh_from_db()
        stale_item.refresh_from_db()
        self.assertEqual(result, OperationResult(processed=1, failed=1))
        self.assertEqual(valid_item.mirror_state, 1)
        self.assertEqual(stale_item.mirror_state, 0)

    @patch(
        "rvservices.twitter_service.Image.open",
        side_effect=Image.DecompressionBombError("oversized image"),
    )
    @patch("rvservices.twitter_service.utils.get_public_url")
    def test_decompression_bomb_is_an_operational_item_failure(
        self, get_public_url, _image_open
    ):
        get_public_url.return_value = SimpleNamespace(
            content=b"image bytes",
            raise_for_status=lambda: None,
        )
        service = self.create_service("twitter")
        item = self.create_item(
            service,
            raw_data=json.dumps(
                {
                    "entities": {
                        "media": [
                            {
                                "type": "photo",
                                "media_url_https": "https://cdn.example.com/photo.jpg",
                            }
                        ]
                    }
                }
            ),
            mirror_state=0,
        )

        with (
            TemporaryDirectory() as data_store,
            override_settings(DATA_STORE=data_store),
        ):
            result = mirror_twitter(specific_item=item)

        item.refresh_from_db()
        self.assertEqual(result, OperationResult(failed=1))
        self.assertEqual(item.mirror_state, 0)

    @patch("rvservices.flickr_service._flickr_client")
    def test_flickr_provider_error_does_not_advance_checkpoint(self, client_factory):
        service = self.create_service(
            "flickr",
            config={"user_id": "provider-user"},
            state={"max_update_id": "100"},
        )
        original_checked = service.last_checked
        client = Mock()
        client.people.getPhotos.side_effect = requests.Timeout(
            "Authorization: Bearer top-secret"
        )
        client_factory.return_value = client

        with self.assertLogs("rvservices.flickr_service", level="ERROR") as logs:
            result = update_flickr()

        service.refresh_from_db()
        output = " ".join(logs.output)
        self.assertEqual(result, OperationResult(failed=1))
        self.assertEqual(service.state, {"max_update_id": "100"})
        self.assertEqual(service.last_checked, original_checked)
        self.assertIn("error_type=Timeout", output)
        self.assertNotIn("top-secret", output)

    @patch("rvservices.flickr_service._flickr_client")
    def test_flickr_media_provider_error_remains_item_scoped(self, client_factory):
        service = self.create_service(
            "flickr",
            config={"user_id": "provider-user"},
        )
        item = self.create_item(
            service,
            raw_data=json.dumps(
                {
                    "media": "video",
                    "url_m": "https://cdn.example.com/preview.jpg",
                }
            ),
            mirror_state=0,
        )
        client = Mock()
        client.photos.getSizes.side_effect = FlickrError("provider error")
        client_factory.return_value = client

        result = mirror_flickr()

        item.refresh_from_db()
        self.assertEqual(result, OperationResult(failed=1))
        self.assertEqual(item.mirror_state, 0)
        self.assertFalse(item.rvmedia_set.exists())

    def test_safe_exception_logging_omits_all_sensitive_exception_values(self):
        exception = ValueError(
            "auth_token=one auth_secret=two Authorization=Bearer-three "
            "https://example.com/oauth/callback?code=four&state=five "
            "raw_data=six"
        )

        with self.assertLogs("rvservices.redaction-test", level="ERROR") as logs:
            log_safe_exception(
                logging.getLogger("rvservices.redaction-test"),
                "Provider operation failed service_id=%s item_id=%s",
                10,
                20,
                exc=exception,
            )

        output = " ".join(logs.output)
        self.assertIn("service_id=10 item_id=20 error_type=ValueError", output)
        for secret in ("one", "two", "three", "four", "five", "six"):
            self.assertNotIn(secret, output)


class UpdateContentCommandTests(TestCase):
    @patch(
        "rvservices.twitter_service.find_twitter_links",
        return_value=OperationResult(processed=2),
    )
    @patch(
        "rvservices.twitter_service.mirror_twitter",
        return_value=OperationResult(processed=1, failed=1),
    )
    def test_failed_phase_continues_then_exits_unsuccessfully(
        self, _mirror, find_links
    ):
        output = StringIO()

        with self.assertRaises(CommandError):
            call_command(
                "update_content",
                skip_rss=True,
                skip_instagram=True,
                skip_flickr=True,
                skip_cleanup=True,
                stdout=output,
            )

        find_links.assert_called_once_with()
        command_output = output.getvalue()
        self.assertIn("twitter-mirror=1", command_output)
        self.assertIn("twitter-links completed: processed=2", command_output)
        self.assertNotIn("Content update completed;", command_output)

    @patch(
        "rvservices.twitter_service.find_twitter_links",
        return_value=OperationResult(processed=2),
    )
    @patch(
        "rvservices.twitter_service.mirror_twitter",
        return_value=OperationResult(processed=1),
    )
    def test_successful_run_has_success_summary(self, _mirror, _find_links):
        output = StringIO()

        call_command(
            "update_content",
            skip_rss=True,
            skip_instagram=True,
            skip_flickr=True,
            skip_cleanup=True,
            stdout=output,
        )

        self.assertIn(
            "Content update completed; failed phases/counts: none",
            output.getvalue(),
        )
