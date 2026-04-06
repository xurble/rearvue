from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rvsite", "0012_normalize_flickr_service_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="rvservice",
            name="instagram_token_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="rvservice",
            name="instagram_last_token_refresh_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="rvservice",
            name="auth_token",
            field=models.TextField(blank=True, default=""),
        ),
    ]
