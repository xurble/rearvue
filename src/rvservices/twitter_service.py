import datetime
import json
import logging
import re
from urllib.parse import urlparse

from PIL import Image

from rearvue import utils
from rvservices.results import (
    OPERATIONAL_EXCEPTIONS,
    OperationResult,
    log_safe_exception,
)
from rvsite.models import RVItem, RVMedia

logger = logging.getLogger(__name__)


def fix_twitter_item(itemid):
    try:
        dbitem = RVItem.objects.get(id=int(itemid))
    except (ValueError, RVItem.DoesNotExist):
        return (False, "Twitter item not found")

    result = mirror_twitter(specific_item=dbitem)
    if not result.failed:
        result += find_twitter_links(specific_item=dbitem)
    if result.failed:
        return (False, "Twitter item update failed; see operational logs")
    return (True, "Item updated successfully")


def import_archive(service, data):
    result = OperationResult()
    for post in data:
        try:
            tweet = post["tweet"]
            text = tweet["full_text"]
            if text.startswith(("RT @", "@")):
                continue
            _import_tweet(service, tweet)
            result += OperationResult(processed=1)
        except OPERATIONAL_EXCEPTIONS as exc:
            log_safe_exception(
                logger,
                "Twitter archive item import failed service_id=%s",
                service.id,
                exc=exc,
            )
            result += OperationResult(failed=1)
    return result


def _import_tweet(service, tweet):
    created_at = datetime.datetime.strptime(
        tweet["created_at"], "%a %b %d %H:%M:%S %z %Y"
    )
    item, _created = RVItem.objects.get_or_create(
        service=service,
        item_id=tweet["id"],
        defaults={
            "domain": service.domain,
            "date_created": created_at.date(),
            "datetime_created": created_at,
        },
    )
    item.caption = tweet["full_text"]

    for mention in reversed(tweet["entities"]["user_mentions"]):
        start, end = (int(value) for value in mention["indices"])
        replacement = (
            "<a title='{name}' href='https://twitter.com/{username}/'>@{username}</a>"
        ).format(name=mention["name"], username=mention["screen_name"])
        item.caption = item.caption[:start] + replacement + item.caption[end:]

    for url in tweet["entities"]["urls"]:
        if url["expanded_url"].startswith("https://twitter.com/"):
            item.caption = item.caption.replace(url["url"], "")
            item.caption += (
                '<blockquote class="twitter-tweet">'
                f'<a href="{url["expanded_url"]}"></a>'
                "</blockquote>"
            )
        else:
            replacement = '<a href="{expanded}">{display}</a>'.format(
                expanded=url["expanded_url"], display=url["display_url"]
            )
            item.caption = item.caption.replace(url["url"], replacement)

    item.datetime_created = created_at
    item.date_created = item.datetime_created.date()
    item.remote_url = "https://twitter.com/{username}/status/{id}".format(
        username=service.config.get("username", ""), id=tweet["id"]
    )

    for media in tweet["entities"].get("media", []):
        if "url" in media:
            item.caption = item.caption.replace(media["url"], "")

    pattern = r"((http|https)://twitpic\.com/(\w+))"
    for match in re.findall(pattern, item.caption):
        parsed = urlparse(match[0])
        host = parsed.hostname or ""
        if host == "twitpic.com" or host.endswith(".twitpic.com"):
            tweet["entities"].setdefault("media", []).append(
                {"type": "photo", "media_url_https": match[0]}
            )
            item.caption = item.caption.replace(match[0], "")

    item.caption = item.caption.replace("\n", "<br>")
    item.raw_data = json.dumps(tweet)
    if "media" not in tweet["entities"]:
        item.mirror_state = 1
    item.save()


def find_twitter_links(specific_item=None):
    if specific_item is not None:
        queue = [specific_item]
    else:
        queue = RVItem.objects.filter(
            mirror_state=1,
            service__type="twitter",
            service__live=True,
        )[:100]

    result = OperationResult()
    for item in queue:
        try:
            tweet = json.loads(item.raw_data)
            urls = tweet["entities"].get("urls", [])
            if not urls:
                urls = _discover_archive_urls(item, tweet)

            item.rvlink_set.filter(is_context=False).delete()
            for url in urls:
                expanded_url = url["expanded_url"]
                if expanded_url.startswith("https://twitter.com"):
                    continue
                linked, _message = utils.make_link(expanded_url, item)
                if not linked:
                    raise RuntimeError("Twitter link preview failed")

            item.mirror_state = 2
            item.save(update_fields=["caption", "mirror_state"])
            result += OperationResult(processed=1)
        except OPERATIONAL_EXCEPTIONS as exc:
            log_safe_exception(
                logger,
                "Twitter link discovery failed service_id=%s item_id=%s",
                item.service_id,
                item.id,
                exc=exc,
            )
            result += OperationResult(failed=1)
    return result


def _discover_archive_urls(item, tweet):
    urls = []
    pattern = (
        r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|"
        r"(?:%[0-9a-fA-F][0-9a-fA-F]))+"
    )
    for url in re.findall(pattern, item.caption):
        if url.startswith("https://twitter.com"):
            continue
        final = utils.final_destination(url)
        short = url.split("://", 1)[1]
        urls.append({"expanded_url": final})
        if f">{short}</a>" not in item.caption:
            item.caption = item.caption.replace(url, f'<a href="{final}">{short}</a>')
    tweet["entities"]["urls"] = urls
    return urls


def mirror_twitter(specific_item=None):
    if specific_item is not None:
        queue = [specific_item]
    else:
        queue = RVItem.objects.filter(
            mirror_state=0,
            service__type="twitter",
            service__live=True,
        )[:100]

    result = OperationResult()
    for item in queue:
        try:
            tweet = json.loads(item.raw_data)
            item.rvmedia_set.all().delete()
            entities = tweet.get("extended_entities") or tweet["entities"]
            media_items = entities["media"]
            for media_data in media_items:
                _mirror_twitter_media(item, media_data)

            item.mirror_state = 1
            item.save(update_fields=["mirror_state"])
            result += OperationResult(processed=1)
        except OPERATIONAL_EXCEPTIONS as exc:
            item.rvmedia_set.all().delete()
            log_safe_exception(
                logger,
                "Twitter mirror failed service_id=%s item_id=%s",
                item.service_id,
                item.id,
                exc=exc,
            )
            result += OperationResult(failed=1)
    return result


def _mirror_twitter_media(item, media_data):
    media = RVMedia.objects.create(item=item)
    media_type = media_data["type"]

    if media_type == "photo":
        source_url = media_data["media_url_https"]
        response = utils.get_public_url(source_url, timeout=30)
        response.raise_for_status()

        if "twitpic" in source_url:
            page = response.content.decode("utf-8")
            marker = '<meta name="twitter:image" value="'
            source_url = page.split(marker, 1)[1].split('"', 1)[0]
            response = utils.get_public_url(source_url, timeout=30)
            response.raise_for_status()

        extension = utils.get_extension(source_url) or "jpg"
        media.media_type = 1
        target_path = _write_media_content(
            media.make_original_path(extension), response.content
        )
        media.primary_media = media.original_media

        with Image.open(target_path) as image:
            ratio = float(image.size[0]) / float(image.size[1])
            width = 300
            height = max(1, int(300 / ratio))
            logger.debug(
                "Resizing Twitter thumbnail item_id=%s width=%s height=%s",
                item.id,
                width,
                height,
            )
            image = image.resize((width, height), Image.BICUBIC)
            image.save(utils.make_full_path(media.make_thumbnail_path(extension)))

    elif media_type in ("animated_gif", "video"):
        variants = [
            variant
            for variant in media_data["video_info"]["variants"]
            if variant["content_type"].startswith("video/")
        ]
        if not variants:
            raise ValueError("Twitter video has no downloadable variant")
        best = max(variants, key=lambda variant: int(variant.get("bitrate", 0)))
        response = utils.get_public_url(best["url"], timeout=30)
        response.raise_for_status()
        extension = best["content_type"].split("/", 1)[1]
        media.media_type = 2
        _write_media_content(media.make_original_path(extension), response.content)
        media.primary_media = media.original_media

        thumbnail_url = media_data["media_url_https"]
        thumbnail = utils.get_public_url(thumbnail_url, timeout=30)
        thumbnail.raise_for_status()
        thumbnail_extension = utils.get_extension(thumbnail_url) or "jpg"
        _write_media_content(
            media.make_thumbnail_path(thumbnail_extension), thumbnail.content
        )
    else:
        raise ValueError("Unsupported Twitter media type")

    media.save()


def _write_media_content(local_path, content):
    target_path = utils.make_full_path(local_path)
    utils.make_folder(target_path)
    with open(target_path, "wb") as output:
        output.write(content)
    return target_path
