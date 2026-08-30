from django.db import migrations, models
from django.db.models import Count


def reject_duplicate_external_identities(apps, schema_editor):
    RVItem = apps.get_model("rvsite", "RVItem")
    duplicates = list(
        RVItem.objects.values("service_id", "item_id")
        .annotate(record_count=Count("id"))
        .filter(record_count__gt=1)
        .order_by("service_id", "item_id")[:20]
    )
    if duplicates:
        sample = ", ".join(
            f"(service={row['service_id']}, item_id={row['item_id']!r}, count={row['record_count']})"
            for row in duplicates
        )
        raise RuntimeError(
            "Cannot enforce unique RVItem external identities. Resolve duplicate "
            f"(service, item_id) records before retrying the migration. Sample: {sample}"
        )


class Migration(migrations.Migration):
    dependencies = [("rvsite", "0014_rvservice_json_documents")]
    operations = [
        migrations.AddField(model_name="rvitem", name="revision", field=models.PositiveBigIntegerField(default=1)),
        migrations.AddField(model_name="rvitem", name="updated_at", field=models.DateTimeField(auto_now=True)),
        migrations.RunPython(reject_duplicate_external_identities, migrations.RunPython.noop),
        migrations.AddConstraint(model_name="rvitem", constraint=models.UniqueConstraint(fields=("service", "item_id"), name="rvitem_service_item_id_unique")),
        migrations.AddIndex(model_name="rvitem", index=models.Index(fields=["domain", "datetime_created", "id"], name="rvitem_domain_created_idx")),
        migrations.AddIndex(model_name="rvitem", index=models.Index(fields=["domain", "service", "datetime_created"], name="rvitem_domain_service_idx")),
        migrations.AddIndex(model_name="rvitem", index=models.Index(fields=["domain", "public", "moderated"], name="rvitem_domain_visibility_idx")),
    ]
