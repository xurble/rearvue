from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from rearvue.utils import admin_page, get_public_url, page
from rvsite.models import RVDomain, RVItem, RVLink, RVService


class PublicUrlTests(SimpleTestCase):
    @patch("rearvue.utils._resolve_host_is_public", return_value=True)
    @patch("rearvue.utils.requests.get")
    def test_get_public_url_validates_redirect_target(self, request_get, _resolve):
        request_get.return_value = SimpleNamespace(
            status_code=302,
            headers={"Location": "http://127.0.0.1/private"},
        )

        with self.assertRaisesMessage(ValueError, "Redirect led"):
            get_public_url("https://example.com/start")

    @patch("rearvue.utils._resolve_host_is_public", return_value=True)
    @patch("rearvue.utils.requests.get")
    def test_get_public_url_returns_non_redirect_response(self, request_get, _resolve):
        response = SimpleNamespace(status_code=200, headers={})
        request_get.return_value = response

        self.assertIs(get_public_url("https://example.com/image.jpg"), response)
        request_get.assert_called_once_with(
            "https://example.com/image.jpg",
            timeout=30,
            verify=True,
            allow_redirects=False,
            headers=None,
        )


class DomainOriginTests(SimpleTestCase):
    @override_settings(DEFAULT_DOMAIN_PROTOCOL="https")
    def test_public_origin_falls_back_to_domain_name(self):
        domain = RVDomain(name="example.com", alt_domain="")

        self.assertEqual(domain.public_origin, "https://example.com")

    @override_settings(DEFAULT_DOMAIN_PROTOCOL="https")
    def test_public_origin_preserves_explicit_origin(self):
        domain = RVDomain(name="example.com", alt_domain="http://archive.example.com/")

        self.assertEqual(domain.public_origin, "http://archive.example.com")


class ItemLinkTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user(username="owner")
        domain = RVDomain.objects.create(name="example.com", owner=owner)
        service = RVService.objects.create(
            name="Example feed",
            domain=domain,
            type=RVService.Type.RSS,
            last_checked=datetime(2026, 8, 30, tzinfo=UTC),
        )
        self.item = RVItem(
            service=service,
            domain=domain,
            item_id="example-item",
            date_created=date(2026, 8, 30),
            datetime_created=datetime(2026, 8, 30, 12, tzinfo=UTC),
        )
        self.item.save()

    def test_original_links_excludes_context_links(self):
        original_link = RVLink.objects.create(
            item=self.item,
            url="https://example.com/original",
            is_context=False,
        )
        RVLink.objects.create(
            item=self.item,
            url="https://example.com/context",
            is_context=True,
        )

        self.assertQuerySetEqual(self.item.original_links, [original_link])

    def test_misspelled_original_links_alias_is_not_supported(self):
        self.assertFalse(hasattr(self.item, "orginal_links"))


class ItemPersistenceTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user(username="item-owner")
        self.domain = RVDomain.objects.create(name="items.example.com", owner=owner)
        self.service = RVService.objects.create(
            name="Example feed",
            domain=self.domain,
            type=RVService.Type.RSS,
            last_checked=datetime(2026, 8, 30, tzinfo=UTC),
        )

    def create_item(self, **changes):
        values = {
            "service": self.service,
            "domain": self.domain,
            "item_id": "manager-created-item",
            "date_created": date(2026, 8, 30),
            "datetime_created": datetime(2026, 8, 30, 12, tzinfo=UTC),
            "title": "Manager-created title",
        }
        values.update(changes)
        return RVItem.objects.create(**values)

    def test_manager_create_generates_slug_without_duplicate_insert(self):
        item = self.create_item()

        self.assertEqual(item.slug, "manager-created-title")
        self.assertEqual(RVItem.objects.count(), 1)
        self.assertEqual(RVItem.objects.get().slug, item.slug)

    def test_update_preserves_slug_and_row(self):
        item = self.create_item()
        original_slug = item.slug

        item.title = "Updated title"
        item.save()

        self.assertEqual(RVItem.objects.count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.title, "Updated title")
        self.assertEqual(item.slug, original_slug)


class AccessDecoratorTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(username="owner")
        self.domain = RVDomain.objects.create(name="example.com", owner=self.user)

    def test_page_returns_404_for_unknown_host(self):
        decorated = page(lambda request: None)
        request = self.factory.get("/", HTTP_HOST="unknown.example")

        self.assertEqual(decorated(request).status_code, 404)

    def test_admin_page_rejects_non_superuser_owner(self):
        decorated = admin_page(lambda request: None)
        request = self.factory.get("/rvadmin/", HTTP_HOST=self.domain.name)
        request.user = self.user

        self.assertEqual(decorated(request).status_code, 403)
