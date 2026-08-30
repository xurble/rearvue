from django.db import models
from django.utils.text import slugify

from django.contrib.auth.models import User

# Create your models here.
import datetime
from urllib.parse import urlparse

from django.conf import settings


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

    @property
    def public_origin(self):
        alt_domain = self.alt_domain.strip().rstrip("/")
        if alt_domain and urlparse(alt_domain).scheme in ("http", "https"):
            return alt_domain
        host = alt_domain or self.name
        return f"{settings.DEFAULT_DOMAIN_PROTOCOL}://{host}"


class RVService(models.Model):

    class Type(models.TextChoices):
        RSS = "rss", "RSS"
        TWITTER = "twitter", "Twitter archive"
        FLICKR = "flickr", "Flickr"
        INSTAGRAM = "instagram", "Instagram"

    name = models.CharField(max_length=512)
    domain = models.ForeignKey(RVDomain, on_delete=models.CASCADE)
    type = models.CharField(max_length=128, choices=Type.choices)
    last_checked = models.DateTimeField(default=datetime.datetime(2015, 1, 10, 17, 26, 51, 977260))  # old date makes it get checked right away
    live = models.BooleanField(default=True)
    hide_unmoderated = models.BooleanField(default=False)
    config = models.JSONField(blank=True, default=dict)
    credentials = models.JSONField(blank=True, default=dict)
    state = models.JSONField(blank=True, default=dict)

    def __str__(self):

        return "%s (%s) %s" % (self.name, self.type, self.config.get("username", ""))


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

    revision = models.PositiveBigIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-id", )
        constraints = [
            models.UniqueConstraint(
                fields=("service", "item_id"),
                name="rvitem_service_item_id_unique",
            ),
        ]
        indexes = [
            models.Index(fields=("domain", "datetime_created", "id"), name="rvitem_domain_created_idx"),
            models.Index(fields=("domain", "service", "datetime_created"), name="rvitem_domain_service_idx"),
            models.Index(fields=("domain", "public", "moderated"), name="rvitem_domain_visibility_idx"),
        ]

    def __str__(self):
        return "%s - %s (%d)" % (self.display_title, self.service.name, self.mirror_state)

    def save(self, *args, **kwargs):
        is_update = self.pk is not None
        if is_update:
            self.revision = models.F("revision") + 1
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"revision", "updated_at"}

        super().save(*args, **kwargs)

        if is_update:
            self.refresh_from_db(fields=("revision", "updated_at"))

        if not self.slug:

            if self.title == '':
                base_title = f"post-{self.id}"
            else:
                base_title = self.title

            base_title = slugify(base_title)
            base_title = base_title[:47]
            slug = base_title

            ct = 1
            while RVItem.objects.filter(slug=slug).count() > 0:
                ct += 1
                slug = f"{base_title}-{ct}"

            type(self).objects.filter(pk=self.pk).update(slug=slug)
            self.slug = slug

    def get_slug(self):
        if not self.slug:
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
            if self.date_created < datetime.date(year=2009, month=1, day=1) and self.caption:
                first_character = f"{self.caption[0]}".lower()
                if first_character == f"{self.caption[0]}" and first_character in "abcdefghijklmnopqrstuvwxyz":
                    return f"@{self.service.config.get('username', '')} {self.caption}"
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
        media = self.rvmedia_set.first()
        return media.thumbnail if media else ""

    @property
    def media_type(self):
        m = self.rvmedia_set.first()
        return m.media_type if m else 0

    @property
    def primary_media(self):
        m = self.rvmedia_set.first()
        return m.primary_media if m else ""

    @property
    def original_media(self):
        m = self.rvmedia_set.first()
        return m.original_media if m else ""

    @property
    def original_links(self):
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

        self.image = "media/%s/%d/%02d/%02d/%s_%d_link.%s" % (
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
