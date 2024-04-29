from django.db import models
from django.utils.text import slugify

from django.contrib.auth.models import User

# Create your models here.
import datetime


class RVDomain(models.Model):
    name = models.CharField(max_length=32, unique=True)
    alt_domain = models.CharField(max_length=128, blank=True, default='', db_index=True)

    min_year = models.IntegerField(default=0)
    max_year = models.IntegerField(default=0)

    owner = models.ForeignKey(User, on_delete=models.CASCADE)

    display_name = models.CharField(max_length=128, default='RearVue')
    poster_image = models.ForeignKey('RVItem', null=True, blank=True, on_delete=models.CASCADE)

    blurb = models.TextField(null=True, blank=True, default='')

    last_updated = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return "%s - %s" % (self.name, self.display_name)


class RVService(models.Model):

    name = models.CharField(max_length=512)
    domain = models.ForeignKey(RVDomain, on_delete=models.CASCADE)
    type = models.CharField(max_length=128)
    last_checked = models.DateTimeField(default=datetime.datetime(2015, 1, 10, 17, 26, 51, 977260))  # old date makes it get checked right away
    username = models.CharField(max_length=128, blank=True, default='')
    userid = models.CharField(max_length=128, blank=True, default='')
    profile_pic = models.CharField(max_length=512, blank=True, default='')
    max_update_id = models.CharField(max_length=256, blank=True, default='')
    auth_token = models.CharField(max_length=256, blank=True, default='')
    auth_secret = models.CharField(max_length=256, blank=True, default='')
    live = models.BooleanField(default=True)
    hide_unmoderated = models.BooleanField(default=False)

    def __str__(self):

        return "%s (%s) %s" % (self.name, self.type, self.username)


class RVItem(models.Model):

    service = models.ForeignKey(RVService, on_delete=models.CASCADE)
    domain = models.ForeignKey(RVDomain, on_delete=models.CASCADE)

    slug = models.SlugField(null=True, blank=True, db_index=True)

    item_id = models.CharField(max_length=128, db_index=True)

    date_retrieved = models.DateTimeField(auto_now_add=True)
    date_created = models.DateField(db_index=True)
    datetime_created = models.DateTimeField(db_index=True)

    remote_url = models.CharField(max_length=512, blank=True, default='')

    title = models.CharField(max_length=512, blank=True, default='')
    caption = models.TextField(blank=True, default='')

    public = models.BooleanField(default=True)

    raw_data = models.TextField(blank=True, default='')

    mirror_state = models.IntegerField(default=0)

    moderated = models.BooleanField(default=False)
    edited = models.BooleanField(default=False)

    class Meta:
        ordering = ("-id", )

    def __str__(self):
        return "%s - %s (%d)" % (self.display_title, self.service.name, self.mirror_state)

    def save(self, *args, **kwargs):

        super().save(*args, **kwargs)

        if self.slug is None:

            if self.title == '':
                base_title = f"post-{self.id}"
            else:
                base_title = self.title

            slug = slugify(base_title)
            ct = 1
            while RVItem.objects.filter(slug=slug).count() > 0:
                ct += 1
                slug = slugify(f"{base_title}-{ct}")

            self.slug = slug

        super().save(*args, **kwargs)

    def get_slug(self):
        if self.slug is None:
            self.save()
        return self.slug

    @property
    def display_title(self):
        if self.title != "":
            return self.title
        else:
            return str(self.date_created)

    @property
    def display_caption(self) -> str:
        if self.service.type == "twitter":
            if self.date_created < datetime.date(year=2009, month=1, day=1):
                first_character = f"{self.caption[0]}".lower()
                if first_character == f"{self.caption[0]}" and first_character in "abcdefghijklmnopqrstuvwxyz":
                    return f"@{self.service.username} {self.caption}"
        return self.caption

    @property
    def first_character(self):
        if self.title != "":
            return self.title[0]
        elif self.caption != "":
            return self.caption[0]
        else:
            return "📖"

    @property
    def date_created_display(self):
        return "{} {} {}".format(self.date_created.day, self.created_month_name, self.date_created.year)

    @property
    def created_month_name(self):
        from rearvue.utils import MONTH_LIST
        return MONTH_LIST[int(self.date_created.month)]

    @property
    def thumbnail(self):
        try:
            return self.rvmedia_set.first().thumbnail
        except Exception:
            return ""

    @property
    def media_type(self):
        return self.rvmedia_set.first().media_type

    @property
    def primary_media(self):
        return self.rvmedia_set.first().primary_media

    @property
    def original_media(self):
        return self.rvmedia_set.first().original_media

    @property
    def orginal_links(self):
        return self.rvlink_set.filter(is_context=False)

    @property
    def context_links(self):
        return self.rvlink_set.filter(is_context=True)

    @property
    def media_list(self):
        items = list(self.rvmedia_set.all())
        idx = 0
        for i in items:
            i.idx = idx
            idx += 1
        return items


class RVLink(models.Model):

    item = models.ForeignKey(RVItem, on_delete=models.CASCADE)

    url = models.CharField(max_length=512)
    title = models.CharField(max_length=512, blank=True, default='')
    description = models.TextField(blank=True, default='')
    image = models.CharField(max_length=512, blank=True, default='')
    is_context = models.BooleanField(default=False)

    def make_image_path(self, file_type):

        if "?" in file_type:
            file_type = file_type.split("?")[0]

        self.original_media = "media/%s/%d/%02d/%02d/%s_%d_link.%s" % (
                                    self.item.domain.name,
                                    self.item.date_created.year,
                                    self.item.date_created.month,
                                    self.item.date_created.day,
                                    self.item.service.type,
                                    self.id,
                                    file_type
                                )
        return self.image


class RVMedia(models.Model):

    item = models.ForeignKey(RVItem, on_delete=models.CASCADE)

    original_media = models.CharField(max_length=256, blank=True, default='')

    primary_media = models.CharField(max_length=256, blank=True, default='')
    media_type = models.IntegerField(default=0, choices=((0, "None"), (1, "Image"), (2, "Video"), (3, "Autoplaying Video")))
    thumbnail = models.CharField(max_length=256, blank=True, default='')

    @property
    def medium(self) -> str:
        if self.media_type in [1, 3]:
            return "image"
        elif self.media_type == 2:
            return "video"
        else:
            return "unknown"

    @property
    def mime_type(self) -> str:
        ext = self.original_media.split(".")[-1]
        if self.media_type == 1:
            return f"image/{ext}"
        elif self.media_type in [2, 3]:
            return f"video/{ext}"
        else:
            return "unknown"

    def make_original_path(self, file_type):
        if "?" in file_type:
            file_type = file_type.split("?")[0]

        self.original_media = "media/%s/%d/%02d/%02d/%s_%d_o.%s" % (
                                            self.item.domain.name,
                                            self.item.date_created.year,
                                            self.item.date_created.month,
                                            self.item.date_created.day,
                                            self.item.service.type,
                                            self.id,
                                            file_type
                                        )
        return self.original_media

    def make_primary_path(self, file_type):
        if "?" in file_type:
            file_type = file_type.split("?")[0]

        self.primary_media = "media/%s/%d/%02d/%02d/%s_%d_p.%s" % (
                                            self.item.domain.name,
                                            self.item.date_created.year,
                                            self.item.date_created.month,
                                            self.item.date_created.day,
                                            self.item.service.type,
                                            self.id,
                                            file_type
                                        )
        return self.primary_media

    def make_thumbnail_path(self, file_type):
        if "?" in file_type:
            file_type = file_type.split("?")[0]

        self.thumbnail = "media/%s/%d/%02d/%02d/%s_%d_t.%s" % (
                                            self.item.domain.name,
                                            self.item.date_created.year,
                                            self.item.date_created.month,
                                            self.item.date_created.day,
                                            self.item.service.type,
                                            self.id,
                                            file_type
                                        )
        return self.thumbnail
