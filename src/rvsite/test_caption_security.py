from datetime import UTC, date, datetime

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from feeds.models import Post, Source

from rvservices.results import OperationResult
from rvservices.rss_service import _ingest_rss_service
from rvservices.twitter_service import import_archive
from rvsite.captions import sanitize_caption_html
from rvsite.models import RVDomain, RVItem, RVService


class CaptionSanitizerTests(SimpleTestCase):
    def test_removes_active_content_and_unsafe_urls(self):
        caption = (
            '<script>alert(1)</script><a href="javascript:alert(2)" onclick="run()">link</a>'
            '<svg><a xlink:href="data:text/html,bad">svg</a></svg>'
            "<math><mtext><img src=x onerror=run()></mtext></math>"
        )

        cleaned = sanitize_caption_html(caption)

        self.assertNotIn("<script", cleaned)
        self.assertNotIn("javascript:", cleaned)
        self.assertNotIn("onclick", cleaned)
        self.assertNotIn("onerror", cleaned)
        self.assertNotIn("<svg", cleaned)
        self.assertNotIn("<math", cleaned)
        self.assertNotIn("<img", cleaned)
        self.assertIn("<a>link</a>", cleaned)

    def test_preserves_only_supported_formatting_and_protects_blank_targets(self):
        caption = (
            '<a href="https://example.com/path" title="Example" target="_blank" '
            'rel="opener" style="color:red">link</a><br>'
            '<blockquote class="twitter-tweet" id="unsafe">quote</blockquote>'
        )

        cleaned = sanitize_caption_html(caption)

        self.assertEqual(
            cleaned,
            '<a href="https://example.com/path" title="Example" target="_blank" '
            'rel="noopener noreferrer">link</a><br>'
            '<blockquote class="twitter-tweet">quote</blockquote>',
        )

    def test_plain_text_and_malformed_markup_are_safely_serialized(self):
        cleaned = sanitize_caption_html(
            'Fish & chips < 3 <a href="https://example.com">ok'
        )

        self.assertEqual(
            cleaned,
            'Fish &amp; chips &lt; 3 <a href="https://example.com">ok</a>',
        )


class CaptionIngestAndRenderingTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user(username="caption-owner")
        self.domain = RVDomain.objects.create(name="example.com", owner=owner)

    def create_service(self, service_type, **changes):
        values = {
            "name": f"{service_type} service",
            "domain": self.domain,
            "type": service_type,
            "last_checked": datetime(2015, 1, 1, tzinfo=UTC),
        }
        values.update(changes)
        return RVService.objects.create(**values)

    def test_existing_stored_caption_is_sanitized_on_public_render(self):
        service = self.create_service(RVService.Type.RSS)
        item = RVItem.objects.create(
            service=service,
            domain=self.domain,
            item_id="legacy-unsafe",
            date_created=date(2026, 9, 12),
            datetime_created=datetime(2026, 9, 12, 12, tzinfo=UTC),
            caption=(
                '<script>alert(1)</script><a href="javascript:alert(2)" onclick="run()">bad</a>'
                '<a href="https://example.net" target="_blank">good</a><br>'
            ),
        )

        response = self.client.get(
            reverse("show_item", args=[2026, 9, 12, item.slug]),
            HTTP_HOST=self.domain.name,
        )
        rendered_caption = BeautifulSoup(response.content, "html.parser").select_one(
            "div.body.link"
        )
        content = str(rendered_caption)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<script", content)
        self.assertNotIn("javascript:", content)
        self.assertNotIn("onclick", content)
        good_link = rendered_caption.find("a", string="good")
        self.assertEqual(good_link["href"], "https://example.net")
        self.assertEqual(good_link["target"], "_blank")
        self.assertEqual(set(good_link["rel"]), {"noopener", "noreferrer"})
        self.assertIsNotNone(rendered_caption.find("br"))

    def test_rss_body_is_sanitized_during_ingest(self):
        service = self.create_service(
            RVService.Type.RSS,
            config={"feed_url": "https://feed.example/rss.xml"},
        )
        source = Source.objects.create(
            feed_url=service.config["feed_url"],
            due_poll=datetime(1900, 1, 1, tzinfo=UTC),
        )
        Post.objects.create(
            source=source,
            title="Unsafe feed item",
            body='<script>bad()</script><a href="javascript:bad">bad</a><br><a href="https://safe.example">safe</a>',
            link="https://feed.example/item",
            found=datetime(2026, 9, 12, 12, tzinfo=UTC),
            created=datetime(2026, 9, 12, 12, tzinfo=UTC),
            guid="rss-unsafe",
            index=1,
        )

        result = _ingest_rss_service(service)
        caption = RVItem.objects.get(service=service, item_id="rss-unsafe").caption

        self.assertEqual(result, OperationResult(processed=1))
        self.assertNotIn("<script", caption)
        self.assertNotIn("javascript:", caption)
        self.assertIn('<a href="https://safe.example">safe</a>', caption)
        self.assertIn("<br>", caption)

    def test_twitter_archive_fields_are_sanitized_during_ingest(self):
        service = self.create_service(
            RVService.Type.TWITTER,
            config={"username": "archive-owner"},
        )
        archive = [
            {
                "tweet": {
                    "id": "tweet-unsafe",
                    "full_text": "<script>bad()</script> hello\nhttps://t.co/good",
                    "created_at": "Sat Sep 12 12:00:00 +0000 2026",
                    "entities": {
                        "user_mentions": [],
                        "urls": [
                            {
                                "url": "https://t.co/good",
                                "expanded_url": "https://example.net/post",
                                "display_url": "example.net/post",
                            }
                        ],
                    },
                }
            }
        ]

        result = import_archive(service, archive)
        caption = RVItem.objects.get(service=service, item_id="tweet-unsafe").caption

        self.assertEqual(result, OperationResult(processed=1))
        self.assertNotIn("<script", caption)
        self.assertIn("<br>", caption)
        self.assertIn(
            '<a href="https://example.net/post">example.net/post</a>', caption
        )
