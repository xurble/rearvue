import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("rvsite", "0016_revisioned_export_models")]

    operations = [
        migrations.AlterField(
            model_name="rvdomain",
            name="poster_image",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="rvsite.rvitem",
            ),
        ),
    ]
