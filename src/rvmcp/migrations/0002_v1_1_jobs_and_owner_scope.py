import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


LEGACY_SCOPES = ["domains:read", "services:read", "items:read", "items:raw", "items:write"]


def migrate_owner_scope(apps, schema_editor):
    MCPClient = apps.get_model("rvmcp", "MCPClient")
    for client in MCPClient.objects.all().iterator():
        scopes = client.scopes if isinstance(client.scopes, list) else []
        client.scopes = ["domain:owner"] if set(scopes).intersection(LEGACY_SCOPES) else []
        client.save(update_fields=["scopes"])


def restore_legacy_scopes(apps, schema_editor):
    MCPClient = apps.get_model("rvmcp", "MCPClient")
    for client in MCPClient.objects.all().iterator():
        scopes = client.scopes if isinstance(client.scopes, list) else []
        client.scopes = LEGACY_SCOPES if "domain:owner" in scopes else []
        client.save(update_fields=["scopes"])


class Migration(migrations.Migration):
    dependencies = [
        ("rvmcp", "0001_initial"),
        ("rvsite", "0016_revisioned_export_models"),
    ]

    operations = [
        migrations.RunPython(migrate_owner_scope, restore_legacy_scopes),
        migrations.CreateModel(
            name="MCPJob",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("operation", models.CharField(max_length=64)),
                ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("cancelled", "Cancelled")], db_index=True, default="queued", max_length=16)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("progress_current", models.PositiveBigIntegerField(default=0)),
                ("progress_total", models.PositiveBigIntegerField(default=0)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("warnings", models.JSONField(blank=True, default=list)),
                ("failures", models.JSONField(blank=True, default=list)),
                ("attempt_count", models.PositiveSmallIntegerField(default=0)),
                ("max_attempts", models.PositiveSmallIntegerField(default=3)),
                ("run_after", models.DateTimeField(db_index=True, default=timezone.now)),
                ("lease_owner", models.CharField(blank=True, max_length=128)),
                ("lease_token", models.CharField(blank=True, editable=False, max_length=64)),
                ("leased_until", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("heartbeat_at", models.DateTimeField(blank=True, null=True)),
                ("artifact_path", models.CharField(blank=True, max_length=512)),
                ("artifact_sha256", models.CharField(blank=True, max_length=64)),
                ("artifact_size", models.PositiveBigIntegerField(default=0)),
                ("artifact_expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("client", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="mcp_jobs", to="rvmcp.mcpclient")),
                ("domain", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="mcp_jobs", to="rvsite.rvdomain")),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
        migrations.CreateModel(
            name="MCPDestructivePreview",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("operation", models.CharField(max_length=64)),
                ("selector", models.JSONField(default=dict)),
                ("impact", models.JSONField(default=dict)),
                ("impact_hash", models.CharField(max_length=64)),
                ("token_hash", models.CharField(editable=False, max_length=64, unique=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("client", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="destructive_previews", to="rvmcp.mcpclient")),
                ("domain", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="destructive_previews", to="rvsite.rvdomain")),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
        migrations.AddIndex(
            model_name="mcpjob",
            index=models.Index(fields=["status", "run_after", "id"], name="rvmcp_job_claim_idx"),
        ),
        migrations.AddIndex(
            model_name="mcpjob",
            index=models.Index(fields=["domain", "created_at", "id"], name="rvmcp_job_domain_idx"),
        ),
    ]
