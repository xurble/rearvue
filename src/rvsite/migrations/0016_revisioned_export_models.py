from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("rvsite", "0015_rvitem_mcp_identity_revision")]

    operations = [
        migrations.AddField(
            model_name="rvdomain",
            name="revision",
            field=models.PositiveBigIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="rvdomain",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="rvservice",
            name="revision",
            field=models.PositiveBigIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="rvservice",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="rvlink",
            name="revision",
            field=models.PositiveBigIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="rvlink",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="rvmedia",
            name="revision",
            field=models.PositiveBigIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="rvmedia",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]
