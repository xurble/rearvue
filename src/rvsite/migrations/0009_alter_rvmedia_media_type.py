# Generated by Django 4.2.3 on 2023-07-28 04:43

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rvsite', '0008_rename_context_rvlink_is_context'),
    ]

    operations = [
        migrations.AlterField(
            model_name='rvmedia',
            name='media_type',
            field=models.IntegerField(choices=[(0, 'None'), (1, 'Image'), (2, 'Video'), (3, 'Autoplaying Video')], default=0),
        ),
    ]
