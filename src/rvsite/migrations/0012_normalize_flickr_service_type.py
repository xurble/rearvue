# Normalize RVService.type for Flickr (was "Flickr", must match flickr_service filters).

from django.db import migrations


def normalize_flickr_types(apps, schema_editor):
    RVService = apps.get_model("rvsite", "RVService")
    RVService.objects.filter(type="Flickr").update(type="flickr")


class Migration(migrations.Migration):

    dependencies = [
        ("rvsite", "0011_rvservice_extra_data"),
    ]

    operations = [
        migrations.RunPython(normalize_flickr_types, migrations.RunPython.noop),
    ]
