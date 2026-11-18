import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("rvmcp", "0002_v1_1_jobs_and_owner_scope"),
    ]

    operations = [
        migrations.CreateModel(
            name="MCPExportSnapshot",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("filters", models.JSONField(default=dict)),
                ("binding_hash", models.CharField(max_length=64)),
                ("snapshot_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "client",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="export_snapshots",
                        to="rvmcp.mcpclient",
                    ),
                ),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
        migrations.CreateModel(
            name="MCPExportSnapshotRecord",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ordinal", models.PositiveBigIntegerField()),
                ("kind", models.CharField(max_length=32)),
                ("source_id", models.PositiveBigIntegerField()),
                ("payload", models.JSONField(default=dict)),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="records",
                        to="rvmcp.mcpexportsnapshot",
                    ),
                ),
            ],
            options={"ordering": ("ordinal",)},
        ),
        migrations.AddConstraint(
            model_name="mcpexportsnapshotrecord",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "ordinal"),
                name="rvmcp_export_snapshot_ordinal_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="mcpexportsnapshotrecord",
            index=models.Index(fields=["snapshot", "ordinal"], name="rvmcp_export_page_idx"),
        ),
    ]
