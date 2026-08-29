import base64

from django.db import migrations, models
from django.utils.dateparse import parse_datetime


def _put(mapping, key, value):
    if value not in (None, "", b""):
        mapping[key] = value


def forwards(apps, schema_editor):
    RVService = apps.get_model("rvsite", "RVService")

    for service in RVService.objects.all().iterator():
        config = {}
        credentials = {}
        state = {}

        _put(config, "username", service.username)
        _put(config, "user_id", service.userid)
        _put(config, "profile_picture_url", service.profile_pic)
        _put(state, "max_update_id", service.max_update_id)

        if service.type == "rss":
            _put(config, "feed_url", service.auth_token)
            _put(credentials, "secret", service.auth_secret)
        elif service.type in ("flickr", "instagram"):
            _put(credentials, "access_token", service.auth_token)
            _put(credentials, "token_secret", service.auth_secret)
        else:
            _put(credentials, "access_token", service.auth_token)
            _put(credentials, "secret", service.auth_secret)

        if service.instagram_token_expires_at:
            credentials["token_expires_at"] = service.instagram_token_expires_at.isoformat()
        if service.instagram_last_token_refresh_at:
            credentials["last_token_refresh_at"] = service.instagram_last_token_refresh_at.isoformat()
        if service.extra_data:
            state["legacy_extra_data_b64"] = base64.b64encode(
                bytes(service.extra_data)
            ).decode("ascii")

        service.config = config
        service.credentials = credentials
        service.state = state
        service.save(update_fields=["config", "credentials", "state"])


def backwards(apps, schema_editor):
    RVService = apps.get_model("rvsite", "RVService")

    for service in RVService.objects.all().iterator():
        config = service.config or {}
        credentials = service.credentials or {}
        state = service.state or {}

        service.username = config.get("username", "")
        service.userid = config.get("user_id", "")
        service.profile_pic = config.get("profile_picture_url", "")
        service.max_update_id = state.get("max_update_id", "")
        if service.type == "rss":
            service.auth_token = config.get("feed_url", "")
            service.auth_secret = credentials.get("secret", "")
        else:
            service.auth_token = credentials.get("access_token", "")
            service.auth_secret = credentials.get(
                "token_secret", credentials.get("secret", "")
            )

        expires = credentials.get("token_expires_at")
        refreshed = credentials.get("last_token_refresh_at")
        service.instagram_token_expires_at = parse_datetime(expires) if expires else None
        service.instagram_last_token_refresh_at = (
            parse_datetime(refreshed) if refreshed else None
        )
        encoded_extra_data = state.get("legacy_extra_data_b64", "")
        service.extra_data = (
            base64.b64decode(encoded_extra_data) if encoded_extra_data else b""
        )
        service.save(
            update_fields=[
                "username",
                "userid",
                "profile_pic",
                "max_update_id",
                "auth_token",
                "auth_secret",
                "instagram_token_expires_at",
                "instagram_last_token_refresh_at",
                "extra_data",
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("rvsite", "0013_rvservice_instagram_graph_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="rvservice",
            name="config",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="rvservice",
            name="credentials",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="rvservice",
            name="state",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(model_name="rvservice", name="username"),
        migrations.RemoveField(model_name="rvservice", name="userid"),
        migrations.RemoveField(model_name="rvservice", name="profile_pic"),
        migrations.RemoveField(model_name="rvservice", name="max_update_id"),
        migrations.RemoveField(model_name="rvservice", name="auth_token"),
        migrations.RemoveField(model_name="rvservice", name="auth_secret"),
        migrations.RemoveField(model_name="rvservice", name="extra_data"),
        migrations.RemoveField(
            model_name="rvservice", name="instagram_token_expires_at"
        ),
        migrations.RemoveField(
            model_name="rvservice", name="instagram_last_token_refresh_at"
        ),
        migrations.AlterField(
            model_name="rvservice",
            name="type",
            field=models.CharField(
                choices=[
                    ("rss", "RSS"),
                    ("twitter", "Twitter archive"),
                    ("flickr", "Flickr"),
                    ("instagram", "Instagram"),
                ],
                max_length=128,
            ),
        ),
    ]
