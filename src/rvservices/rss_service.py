import datetime
import logging

from bs4 import BeautifulSoup
from django.utils import timezone
from feeds.models import Post, Source
from feeds.utils import update_feeds
from PIL import Image
from webpreview import webpreview

from rearvue import utils
from rvservices.results import (
    OPERATIONAL_EXCEPTIONS,
    OperationResult,
    log_safe_exception,
)
from rvsite.models import RVItem, RVLink, RVMedia, RVService

logger = logging.getLogger(__name__)


def fix_rss_item(itemid):
    try:
        dbitem = RVItem.objects.get(id=int(itemid))
    except (ValueError, RVItem.DoesNotExist):
        return (False, "RSS item not found")

    result = mirror_rss(specific_item=dbitem)
    if not result.failed:
        result += find_rss_links(specific_item=dbitem)
    if result.failed:
        return (False, "RSS item update failed; see operational logs")
    return (True, "Item updated successfully")


def update_rss():
    services = list(RVService.objects.filter(type="rss", live=True))
    if not services:
        return OperationResult()

    for service in services:
        Source.objects.get_or_create(feed_url=service.config.get("feed_url", ""))

    try:
        update_feeds()
    except Exception as exc:  # noqa: BLE001 - third-party batch boundary.
        log_safe_exception(
            logger,
            "RSS provider update failed service_ids=%s",
            [service.id for service in services],
            exc=exc,
        )
        return OperationResult(failed=len(services))

    result = OperationResult()
    for service in services:
        service_result = _ingest_rss_service(service)
        result += service_result
        if not service_result.failed:
            service.last_checked = timezone.now()
            service.save(update_fields=["last_checked"])
    return result


def _ingest_rss_service(service):
    source = Source.objects.filter(feed_url=service.config.get("feed_url", "")).first()
    if source is None:
        logger.error("RSS source missing after update service_id=%s", service.id)
        return OperationResult(failed=1)

    result = OperationResult()
    for post in source.posts.all():
        try:
            created_at = post.created
            item, _created = RVItem.objects.get_or_create(
                service=service,
                item_id=post.guid,
                defaults={
                    "domain": service.domain,
                    "date_created": created_at.date(),
                    "datetime_created": created_at,
                },
            )
            item.title = post.title
            item.caption = post.body
            item.datetime_created = created_at
            item.date_created = datetime.date(
                year=item.datetime_created.year,
                month=item.datetime_created.month,
                day=item.datetime_created.day,
            )
            item.remote_url = post.link
            item.raw_data = str(post.id)
            if post.enclosures.count() == 0 and item.mirror_state == 0:
                item.mirror_state = 1
            item.save()
            result += OperationResult(processed=1)
        except OPERATIONAL_EXCEPTIONS as exc:
            log_safe_exception(
                logger,
                "RSS item ingest failed service_id=%s source_post_id=%s",
                service.id,
                post.id,
                exc=exc,
            )
            result += OperationResult(failed=1)
    return result


def mirror_rss(specific_item=None):
    if specific_item is not None:
        queue = [specific_item]
    else:
        queue = RVItem.objects.filter(
            mirror_state=0,
            service__type="rss",
            service__live=True,
        )[:50]

    result = OperationResult()
    for item in queue:
        try:
            post = Post.objects.get(id=int(item.raw_data))
            item.rvmedia_set.all().delete()

            for enclosure in post.enclosures.all():
                if not enclosure.type.startswith(("image/", "video/")):
                    continue
                _mirror_rss_enclosure(item, enclosure)

            item.mirror_state = 1
            item.save(update_fields=["mirror_state"])
            result += OperationResult(processed=1)
        except OPERATIONAL_EXCEPTIONS as exc:
            item.rvmedia_set.all().delete()
            log_safe_exception(
                logger,
                "RSS mirror failed service_id=%s item_id=%s",
                item.service_id,
                item.id,
                exc=exc,
            )
            result += OperationResult(failed=1)
    return result


def _mirror_rss_enclosure(item, enclosure):
    response = utils.get_public_url(enclosure.href, timeout=30)
    response.raise_for_status()

    media = RVMedia.objects.create(item=item)
    extension = enclosure.type.split("/")[-1]
    output_path = media.make_original_path(extension)
    target_path = utils.make_full_path(output_path)
    utils.make_folder(target_path)
    with open(target_path, "wb") as output:
        output.write(response.content)

    if enclosure.type.startswith("image/"):
        media.media_type = 1
        media.primary_media = media.original_media
        with Image.open(target_path) as image:
            ratio = float(image.size[0]) / float(image.size[1])
            width = 300
            height = max(1, int(300 / ratio))
            logger.debug(
                "Resizing RSS thumbnail item_id=%s width=%s height=%s",
                item.id,
                width,
                height,
            )
            image = image.resize((width, height), Image.BICUBIC)
            thumbnail_path = media.make_thumbnail_path(extension)
            image.save(utils.make_full_path(thumbnail_path))
    else:
        media.media_type = 3 if enclosure.medium == "image" else 2

    media.save()


def find_rss_links(specific_item=None):
    if specific_item is not None:
        queue = [specific_item]
    else:
        queue = RVItem.objects.filter(
            mirror_state=1,
            service__type="rss",
            service__live=True,
        )[:50]

    result = OperationResult()
    for item in queue:
        try:
            last_link = _find_last_rss_link(item.caption)
            if last_link:
                _update_rss_link(item, last_link)
            item.mirror_state = 2
            item.save(update_fields=["mirror_state"])
            result += OperationResult(processed=1)
        except OPERATIONAL_EXCEPTIONS as exc:
            log_safe_exception(
                logger,
                "RSS link discovery failed service_id=%s item_id=%s",
                item.service_id,
                item.id,
                exc=exc,
            )
            result += OperationResult(failed=1)
    return result


def _find_last_rss_link(caption):
    last_link = ""
    soup = BeautifulSoup(caption, "html5lib")
    for link in soup.find_all(name="a"):
        if link.has_attr("href") and link.text.startswith("http"):
            last_link = link["href"]

    if not last_link:
        for blockquote in soup.find_all(name="blockquote"):
            if blockquote.has_attr("cite"):
                last_link = blockquote["cite"]
    return last_link


def _update_rss_link(item, url):
    link = item.rvlink_set.filter(url=url).first()
    if link is None:
        link = RVLink(url=url, item=item)

    if not utils.validate_public_http_url(link.url):
        raise ValueError("RSS link URL scheme or host is not allowed")
    link.url = utils.final_destination(link.url)
    if not utils.validate_public_http_url(link.url):
        raise ValueError("RSS link redirect target is not allowed")

    preview = webpreview(link.url, timeout=1000)
    if preview.image:
        response = utils.get_public_url(preview.image, timeout=30)
        if not response.ok:
            preview.image = ""

    if preview.title == "Access denied":
        return

    link.title = preview.title or ""
    link.image = preview.image or ""
    link.description = preview.description or ""
    link.save()
