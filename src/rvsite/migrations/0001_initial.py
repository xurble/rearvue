# -*- coding: utf-8 -*-


from django.db import models, migrations
from django.conf import settings
import datetime


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='RVDomain',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('name', models.CharField(unique=True, max_length=32)),
                ('alt_domain', models.CharField(default=b'', max_length=128, db_index=True, blank=True)),
                ('min_year', models.IntegerField(default=0)),
                ('max_year', models.IntegerField(default=0)),
                ('display_name', models.CharField(default=b'RearVue', max_length=128)),
                ('blurb', models.TextField(default=b'', null=True, blank=True)),
                ('owner', models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE)),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='RVItem',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('item_id', models.CharField(max_length=128, db_index=True)),
                ('date_retrieved', models.DateTimeField(auto_now_add=True)),
                ('date_created', models.DateField(db_index=True)),
                ('datetime_created', models.DateTimeField(db_index=True)),
                ('remote_url', models.CharField(default=b'', max_length=512, blank=True)),
                ('title', models.CharField(default=b'', max_length=512, blank=True)),
                ('caption', models.TextField(default=b'', blank=True)),
                ('public', models.BooleanField(default=True)),
                ('original_media', models.CharField(default=b'', max_length=256, blank=True)),
                ('primary_media', models.CharField(default=b'', max_length=256, blank=True)),
                ('media_type', models.IntegerField(default=0, choices=[(0, b'None'), (1, b'Image'), (2, b'Video')])),
                ('thumbnail', models.CharField(default=b'', max_length=256, blank=True)),
                ('raw_data', models.TextField(default=b'', blank=True)),
                ('mirror_state', models.IntegerField(default=0)),
                ('domain', models.ForeignKey(to='rvsite.RVDomain', on_delete=models.CASCADE)),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='RVService',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('name', models.CharField(max_length=512)),
                ('type', models.CharField(max_length=128)),
                ('last_checked', models.DateTimeField(default=datetime.datetime(2015, 1, 10, 17, 26, 51, 977260))),
                ('username', models.CharField(default=b'', max_length=128, blank=True)),
                ('userid', models.CharField(default=b'', max_length=128, blank=True)),
                ('profile_pic', models.CharField(default=b'', max_length=512, blank=True)),
                ('max_update_id', models.CharField(default=b'', max_length=256, blank=True)),
                ('auth_token', models.CharField(default=b'', max_length=256, blank=True)),
                ('auth_secret', models.CharField(default=b'', max_length=256, blank=True)),
                ('domain', models.ForeignKey(to='rvsite.RVDomain', on_delete=models.CASCADE)),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.AddField(
            model_name='rvitem',
            name='service',
            field=models.ForeignKey(to='rvsite.RVService', on_delete=models.CASCADE),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='rvdomain',
            name='poster_image',
            field=models.ForeignKey(blank=True, to='rvsite.RVItem', null=True, on_delete=models.SET_NULL),
            preserve_default=True,
        ),
    ]
