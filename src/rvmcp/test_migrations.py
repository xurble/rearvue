from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MCPOwnerScopeMigrationTests(TransactionTestCase):
    migrate_from = ("rvmcp", "0001_initial")
    rvsite_from = ("rvsite", "0015_rvitem_mcp_identity_revision")
    migrate_to = ("rvmcp", "0002_v1_1_jobs_and_owner_scope")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from, self.rvsite_from])
        apps = executor.loader.project_state([self.migrate_from, self.rvsite_from]).apps
        User = apps.get_model("auth", "User")
        RVDomain = apps.get_model("rvsite", "RVDomain")
        MCPClient = apps.get_model("rvmcp", "MCPClient")
        owner = User.objects.create(username="mcp-scope-migration-owner")
        domain = RVDomain.objects.create(name="scope-migration.example", owner=owner)
        client = MCPClient.objects.create(
            name="legacy-client",
            token_prefix="12345678",
            token_hash="a" * 64,
            scopes=["items:read"],
        )
        client.domains.add(domain)
        MCPClient.objects.create(
            name="unprivileged-client",
            token_prefix="87654321",
            token_hash="b" * 64,
            scopes=[],
        )
        self.domain_id = domain.id

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_replaces_legacy_scopes_and_preserves_domain_grants(self):
        MCPClient = self.apps.get_model("rvmcp", "MCPClient")
        client = MCPClient.objects.get(name="legacy-client")
        self.assertEqual(client.scopes, ["domain:owner"])
        self.assertEqual(list(client.domains.values_list("id", flat=True)), [self.domain_id])
        self.assertEqual(MCPClient.objects.get(name="unprivileged-client").scopes, [])
