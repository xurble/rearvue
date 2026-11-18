import datetime
import json
import logging

import flickrapi
from django.conf import settings
from django.utils import timezone
from PIL import Image

from rearvue import utils
from rvservices.results import (
    OPERATIONAL_EXCEPTIONS,
    OperationResult,
    complete_media_replacement,
    fail_media_replacement,
    log_safe_exception,
    snapshot_media_ids,
)
from rvsite.models import RVItem, RVMedia, RVService

logger = logging.getLogger(__name__)


def _flickr_client(service):
    token = flickrapi.auth.FlickrAccessToken(
        token=service.credentials.get("access_token", ""),
        token_secret=service.credentials.get("token_secret", ""),
        access_level="read",
        username=service.config.get("username", ""),
        user_nsid=service.config.get("user_id", ""),
    )
    return flickrapi.FlickrAPI(
        settings.FLICKR_KEY,
        settings.FLICKR_SECRET,
        token=token,
        format="parsed-json",
    )


def update_flickr():
    result = OperationResult()
    for service in RVService.objects.filter(type="flickr", live=True):
        if utils.hours_since(service.last_checked) < 12:
            logger.info(
                "Skipping Flickr service_id=%s checked_within_hours=12", service.id
            )
            continue

        try:
            result += _update_flickr_service(service)
        except Exception as exc:  # noqa: BLE001 - provider isolation boundary.
            log_safe_exception(
                logger,
                "Flickr update failed service_id=%s",
                service.id,
                exc=exc,
            )
            result += OperationResult(failed=1)
    return result


def _update_flickr_service(service):
    client = _flickr_client(service)
    config = service.config
    if not config.get("user_id"):
        user = client.people.findByUsername(username=config.get("username", ""))
        config = {**config, "user_id": user["user"]["id"]}
        service.config = config
        service.save(update_fields=["config"])

    page = 0
    pages = 1
    max_upload_date = 0
    result = OperationResult()
    while page < pages:
        page += 1
        logger.debug("Fetching Flickr page service_id=%s page=%s", service.id, page)
        minimum_date = service.state.get("max_update_id") or None
        response = client.people.getPhotos(
            extras=(
                "date_upload,date_taken,geo,machine_tags,url_t,url_o,url_l,"
                "url_z,url_m,description,media,geo"
            ),
            user_id=config["user_id"],
            page=page,
            min_upload_date=minimum_date,
        )["photos"]
        pages = int(response["pages"])

        for photo in response["photo"]:
            try:
                upload_date = int(photo["dateupload"])
                max_upload_date = max(max_upload_date, upload_date)
                _upsert_flickr_item(service, config, photo, upload_date)
                result += OperationResult(processed=1)
            except OPERATIONAL_EXCEPTIONS as exc:
                log_safe_exception(
                    logger,
                    "Flickr item ingest failed service_id=%s",
                    service.id,
                    exc=exc,
                )
                result += OperationResult(failed=1)

    if not result.failed:
        if max_upload_date:
            service.state = {
                **service.state,
                "max_update_id": str(max_upload_date),
            }
        service.last_checked = timezone.now()
        service.save(update_fields=["state", "last_checked"])
    return result


def _upsert_flickr_item(service, config, photo, upload_date):
    if "datetaken" in photo:
        taken_datetime = datetime.datetime.strptime(
            photo["datetaken"], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=datetime.UTC)
    else:
        taken_datetime = datetime.datetime.fromtimestamp(upload_date, tz=datetime.UTC)
    item, _created = RVItem.objects.get_or_create(
        service=service,
        item_id=photo["id"],
        defaults={
            "domain": service.domain,
            "date_created": taken_datetime.date(),
            "datetime_created": taken_datetime,
        },
    )
    item.title = photo["title"]
    item.caption = photo["description"]["_content"]
    item.public = photo["ispublic"] == 1
    item.datetime_created = taken_datetime
    item.date_created = taken_datetime.date()
    item.remote_url = "https://www.flickr.com/photos/{username}/{id}/".format(
        username=config.get("username", ""), id=photo["id"]
    )
    item.raw_data = json.dumps(photo)
    item.save()


def mirror_flickr():
    result = OperationResult()
    for service in RVService.objects.filter(type="flickr", live=True):
        try:
            client = _flickr_client(service)
        except Exception as exc:  # noqa: BLE001 - client setup boundary.
            log_safe_exception(
                logger,
                "Flickr client setup failed service_id=%s",
                service.id,
                exc=exc,
            )
            result += OperationResult(failed=1)
            continue

        queue = RVItem.objects.filter(mirror_state=0, service=service)[:100]
        for item in queue:
            previous_media_ids = snapshot_media_ids(item)
            try:
                _mirror_flickr_item(client, item)
                complete_media_replacement(item, previous_media_ids)
                result += OperationResult(processed=1)
            except OPERATIONAL_EXCEPTIONS as exc:
                fail_media_replacement(item, previous_media_ids)
                log_safe_exception(
                    logger,
                    "Flickr mirror failed service_id=%s item_id=%s",
                    service.id,
                    item.id,
                    exc=exc,
                )
                result += OperationResult(failed=1)
    return result


def _mirror_flickr_item(client, item):
    data = json.loads(item.raw_data)
    media = RVMedia.objects.create(item=item)

    if data["media"] == "photo":
        original_url = data["url_o"]
        original = utils.get_public_url(original_url, timeout=30)
        original.raise_for_status()
        original_extension = utils.get_extension(original_url)
        media.media_type = 1
    else:
        sizes = client.photos.getSizes(photo_id=item.item_id)["sizes"]["size"]
        video = next(
            (size for size in sizes if size["label"] == "Video Original"),
            None,
        )
        if video is None:
            raise ValueError("Flickr video has no original source")
        original = utils.get_public_url(video["source"], timeout=30)
        original.raise_for_status()
        original_extension = "mp4"
        media.media_type = 2

    original_path = media.make_original_path(original_extension)
    media.save(update_fields=["media_type", "original_media"])
    _write_media_content(original_path, original.content)

    primary_key = next(
        (key for key in ("url_l", "url_m", "url_o") if key in data),
        None,
    )
    if primary_key is None:
        raise ValueError("Flickr item has no primary image source")
    primary_url = data[primary_key]
    primary = utils.get_public_url(primary_url, timeout=30)
    primary.raise_for_status()
    primary_extension = utils.get_extension(primary_url) or "jpg"
    local_primary_path = media.make_primary_path(primary_extension)
    media.save(update_fields=["primary_media"])
    primary_path = _write_media_content(local_primary_path, primary.content)

    with Image.open(primary_path) as image:
        ratio = float(image.size[0]) / float(image.size[1])
        width = 300
        height = max(1, int(300 / ratio))
        logger.debug(
            "Resizing Flickr thumbnail item_id=%s width=%s height=%s",
            item.id,
            width,
            height,
        )
        image = image.resize((width, height), Image.BICUBIC)
        thumbnail_path = media.make_thumbnail_path(primary_extension)
        media.save(update_fields=["thumbnail"])
        image.save(utils.make_full_path(thumbnail_path))
    media.save()


def _write_media_content(local_path, content):
    return utils.write_media_content(local_path, content)
