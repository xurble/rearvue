from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [("rvsite", "0015_rvitem_mcp_identity_revision")]
    operations = [
        migrations.CreateModel(
            name="MCPClient",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=128, unique=True)),
                ("token_prefix", models.CharField(db_index=True, editable=False, max_length=16)),
                ("token_hash", models.CharField(editable=False, max_length=64, unique=True)),
                ("scopes", models.JSONField(blank=True, default=list)),
                ("enabled", models.BooleanField(default=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("domains", models.ManyToManyField(blank=True, related_name="mcp_clients", to="rvsite.rvdomain")),
            ],
            options={"ordering": ("name",)},
        ),
        migrations.CreateModel(
            name="MCPAuditRecord",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("domain_name", models.CharField(blank=True, max_length=32)),
                ("operation", models.CharField(max_length=64)),
                ("outcome", models.CharField(choices=[("success", "Success"), ("partial", "Partial"), ("failure", "Failure"), ("conflict", "Conflict")], max_length=16)),
                ("affected_ids", models.JSONField(blank=True, default=list)),
                ("affected_count", models.PositiveIntegerField(default=0)),
                ("idempotency_key", models.CharField(blank=True, max_length=128)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("client", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="rvmcp.mcpclient")),
                ("domain", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to="rvsite.rvdomain")),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
        migrations.CreateModel(
            name="MCPIdempotencyRecord",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("operation", models.CharField(max_length=64)),
                ("key", models.CharField(max_length=128)),
                ("request_hash", models.CharField(max_length=64)),
                ("response", models.JSONField(default=dict)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("client", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="rvmcp.mcpclient")),
            ],
        ),
        migrations.AddConstraint(
            model_name="mcpidempotencyrecord",
            constraint=models.UniqueConstraint(fields=("client", "operation", "key"), name="rvmcp_idempotency_client_operation_key_unique"),
        ),
    ]
