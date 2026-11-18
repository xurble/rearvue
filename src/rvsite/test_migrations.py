import datetime
import importlib

from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class RVServiceJSONMigrationTests(TransactionTestCase):
    migrate_from = ("rvsite", "0013_rvservice_instagram_graph_fields")
    migrate_to = ("rvsite", "0014_rvservice_json_documents")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps

        User = old_apps.get_model("auth", "User")
        RVDomain = old_apps.get_model("rvsite", "RVDomain")
        RVService = old_apps.get_model("rvsite", "RVService")
        owner = User.objects.create(username="migration-owner")
        domain = RVDomain.objects.create(name="migration.example", owner=owner)
        self.expires_at = timezone.now().replace(microsecond=0) + datetime.timedelta(days=30)
        self.refreshed_at = timezone.now().replace(microsecond=0)

        RVService.objects.create(
            name="RSS",
            domain=domain,
            type="rss",
            auth_token="https://example.com/feed.xml",
            extra_data=b"legacy-rss",
        )
        RVService.objects.create(
            name="Flickr",
            domain=domain,
            type="flickr",
            username="photographer",
            userid="flickr-user-id",
            profile_pic="https://example.com/avatar.jpg",
            max_update_id="12345",
            auth_token="flickr-token",
            auth_secret="flickr-secret",
        )
        RVService.objects.create(
            name="Instagram",
            domain=domain,
            type="instagram",
            username="creator",
            userid="instagram-user-id",
            auth_token="instagram-token",
            instagram_token_expires_at=self.expires_at,
            instagram_last_token_refresh_at=self.refreshed_at,
        )

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_migrates_loose_fields_by_service_semantics(self):
        RVService = self.apps.get_model("rvsite", "RVService")

        rss = RVService.objects.get(name="RSS")
        self.assertEqual(rss.config, {"feed_url": "https://example.com/feed.xml"})
        self.assertEqual(rss.credentials, {})
        self.assertEqual(rss.state, {"legacy_extra_data_b64": "bGVnYWN5LXJzcw=="})

        flickr = RVService.objects.get(name="Flickr")
        self.assertEqual(
            flickr.config,
            {
                "username": "photographer",
                "user_id": "flickr-user-id",
                "profile_picture_url": "https://example.com/avatar.jpg",
            },
        )
        self.assertEqual(
            flickr.credentials,
            {"access_token": "flickr-token", "token_secret": "flickr-secret"},
        )
        self.assertEqual(flickr.state, {"max_update_id": "12345"})

        instagram = RVService.objects.get(name="Instagram")
        self.assertEqual(instagram.config["user_id"], "instagram-user-id")
        self.assertEqual(instagram.credentials["access_token"], "instagram-token")
        self.assertEqual(
            instagram.credentials["token_expires_at"], self.expires_at.isoformat()
        )
        self.assertEqual(
            instagram.credentials["last_token_refresh_at"],
            self.refreshed_at.isoformat(),
        )

    def test_reverse_migration_restores_legacy_fields(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        RVService = old_apps.get_model("rvsite", "RVService")

        rss = RVService.objects.get(name="RSS")
        self.assertEqual(rss.auth_token, "https://example.com/feed.xml")
        self.assertEqual(bytes(rss.extra_data), b"legacy-rss")

        flickr = RVService.objects.get(name="Flickr")
        self.assertEqual(flickr.username, "photographer")
        self.assertEqual(flickr.userid, "flickr-user-id")
        self.assertEqual(flickr.max_update_id, "12345")
        self.assertEqual(flickr.auth_token, "flickr-token")
        self.assertEqual(flickr.auth_secret, "flickr-secret")

        instagram = RVService.objects.get(name="Instagram")
        self.assertEqual(instagram.auth_token, "instagram-token")
        self.assertEqual(instagram.instagram_token_expires_at, self.expires_at)
        self.assertEqual(
            instagram.instagram_last_token_refresh_at, self.refreshed_at
        )


class RVItemIdentityMigrationTests(TransactionTestCase):
    migrate_from = ("rvsite", "0014_rvservice_json_documents")
    migrate_to = ("rvsite", "0015_rvitem_mcp_identity_revision")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        apps = executor.loader.project_state([self.migrate_from]).apps
        User = apps.get_model("auth", "User")
        RVDomain = apps.get_model("rvsite", "RVDomain")
        RVService = apps.get_model("rvsite", "RVService")
        RVItem = apps.get_model("rvsite", "RVItem")
        owner = User.objects.create(username="identity-migration-owner")
        domain = RVDomain.objects.create(name="duplicates.example", owner=owner)
        service = RVService.objects.create(name="Twitter", domain=domain, type="twitter")
        values = {
            "service": service,
            "domain": domain,
            "item_id": "duplicate",
            "date_created": datetime.date(2025, 1, 1),
            "datetime_created": timezone.now(),
        }
        RVItem.objects.create(**values)
        RVItem.objects.create(**values)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        apps = executor.loader.project_state([self.migrate_from]).apps
        apps.get_model("rvsite", "RVItem").objects.all().delete()
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_duplicate_external_identities_stop_the_migration(self):
        executor = MigrationExecutor(connection)
        with self.assertRaisesRegex(RuntimeError, "Resolve duplicate"):
            executor.migrate([self.migrate_to])

    def test_duplicate_preflight_precedes_schema_changes(self):
        migration_module = importlib.import_module(
            "rvsite.migrations.0015_rvitem_mcp_identity_revision"
        )
        first_operation = migration_module.Migration.operations[0]

        self.assertIsInstance(first_operation, migrations.RunPython)
        self.assertIs(
            first_operation.code,
            migration_module.reject_duplicate_external_identities,
        )
