"""
Instagram via Instagram API with Instagram Login (graph.instagram.com).

Public entry points used elsewhere in the app:
``update_instagram``, ``mirror_instagram``, ``fix_instagram_item``, ``sync_all_instagram_services``.

See: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login
"""

from __future__ import annotations

import datetime
import json
import logging
from collections.abc import Iterator
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
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

# Graph returns at most ~10k recent media for an IG user.
MAX_MEDIA_ITEMS = 10_000

MEDIA_FIELDS = (
    "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,"
    "children{id,media_type,media_url,thumbnail_url}"
)


def graph_instagram_base() -> str:
    ver = getattr(settings, "INSTAGRAM_GRAPH_API_VERSION", "v22.0")
    return f"https://graph.instagram.com/{ver}"


def _normalize_mirror_media_type(media_type: str | None) -> str:
    if not media_type:
        return "IMAGE"
    if media_type == "REELS":
        return "VIDEO"
    return media_type


def map_graph_media_to_raw_data(media: dict[str, Any]) -> dict[str, Any]:
    """Map a single IG Media JSON object to the raw_data shape used by mirroring."""
    mid = str(media["id"])
    caption = media.get("caption") or ""
    mtype = media.get("media_type") or "IMAGE"
    permalink = media.get("permalink") or ""
    ts = media.get("timestamp") or ""
    children_in = (media.get("children") or {}).get("data") or []

    if mtype == "CAROUSEL_ALBUM":
        child_payloads: list[dict[str, Any]] = []
        for ch in children_in:
            cm = _normalize_mirror_media_type(ch.get("media_type"))
            child_payloads.append(
                {
                    "media_url": ch.get("media_url"),
                    "media_type": cm,
                    "thumbnail_url": ch.get("thumbnail_url"),
                }
            )
        first_url = (
            child_payloads[0]["media_url"] if child_payloads else media.get("media_url")
        )
        first_thumb = (
            child_payloads[0].get("thumbnail_url")
            if child_payloads
            else media.get("thumbnail_url")
        )
        return {
            "id": mid,
            "caption": caption,
            "media_type": "CAROUSEL_ALBUM",
            "media_url": first_url,
            "thumbnail_url": first_thumb,
            "permalink": permalink,
            "timestamp": ts,
            "children": {"data": child_payloads},
        }

    nm = _normalize_mirror_media_type(mtype)
    return {
        "id": mid,
        "caption": caption,
        "media_type": nm,
        "media_url": media.get("media_url"),
        "thumbnail_url": media.get("thumbnail_url"),
        "permalink": permalink,
        "timestamp": ts,
        "children": {"data": []},
    }


def parse_graph_timestamp(ts: str) -> datetime.datetime:
    if not ts:
        return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    # e.g. 2024-01-15T12:00:00+0000
    if ts.endswith("+0000"):
        ts = ts[:-5] + "+00:00"
    dt = datetime.datetime.fromisoformat(ts)
    return dt.replace(tzinfo=None)


def _graph_get(url: str, params: dict | None = None) -> dict:
    # When `url` is a full paging.next URL, do not pass extra params.
    r = (
        requests.get(url, params=params, timeout=60)
        if params is not None
        else requests.get(url, timeout=60)
    )
    try:
        data = r.json()
    except json.JSONDecodeError:
        r.raise_for_status()
        raise
    if not r.ok:
        err = data.get("error", {})
        msg = err.get("message", r.text)
        code = err.get("code", r.status_code)
        raise RuntimeError(f"Instagram Graph error {code}: {msg}")
    return data


def refresh_long_lived_token(access_token: str) -> dict[str, Any]:
    """Extend a valid long-lived Instagram user token (server-side)."""
    url = "https://graph.instagram.com/refresh_access_token"
    return _graph_get(
        url,
        {
            "grant_type": "ig_refresh_token",
            "access_token": access_token,
        },
    )


def exchange_short_lived_for_long_lived(short_lived_token: str) -> dict[str, Any]:
    url = "https://graph.instagram.com/access_token"
    return _graph_get(
        url,
        {
            "grant_type": "ig_exchange_token",
            "client_secret": settings.INSTAGRAM_SECRET,
            "access_token": short_lived_token,
        },
    )


def maybe_refresh_service_token(service: RVService) -> None:
    """Refresh long-lived token if nearing expiry (best-effort)."""
    access_token = service.credentials.get("access_token", "")
    if not access_token:
        return
    now = timezone.now()
    expires_value = service.credentials.get("token_expires_at")
    expires = parse_datetime(expires_value) if expires_value else None
    if expires and expires > now + datetime.timedelta(days=14):
        return
    last_value = service.credentials.get("last_token_refresh_at")
    last = parse_datetime(last_value) if last_value else None
    if last and (now - last) < datetime.timedelta(hours=24):
        return
    try:
        out = refresh_long_lived_token(access_token)
        credentials = {**service.credentials, "access_token": out["access_token"]}
        sec = int(out.get("expires_in", 0))
        if sec:
            credentials["token_expires_at"] = (
                now + datetime.timedelta(seconds=sec)
            ).isoformat()
        credentials["last_token_refresh_at"] = now.isoformat()
        service.credentials = credentials
        service.save(update_fields=["credentials"])
        logger.info("Refreshed Instagram token for service id=%s", service.id)
    except (
        requests.RequestException,
        KeyError,
        TypeError,
        ValueError,
        RuntimeError,
    ) as exc:
        log_safe_exception(
            logger,
            "Instagram token refresh failed service_id=%s",
            service.id,
            exc=exc,
            level=logging.WARNING,
        )


def fetch_me(access_token: str) -> dict[str, Any]:
    base = graph_instagram_base()
    return _graph_get(
        f"{base}/me", {"fields": "id,username", "access_token": access_token}
    )


def fetch_media_object(access_token: str, media_id: str) -> dict[str, Any]:
    base = graph_instagram_base()
    return _graph_get(
        f"{base}/{media_id}",
        {"fields": MEDIA_FIELDS, "access_token": access_token},
    )


def iter_user_media(access_token: str, user_id: str) -> Iterator[dict[str, Any]]:
    base = graph_instagram_base()
    url = f"{base}/{user_id}/media"
    params: dict[str, Any] = {
        "fields": MEDIA_FIELDS,
        "access_token": access_token,
        "limit": 100,
    }
    count = 0
    while url and count < MAX_MEDIA_ITEMS:
        if params is not None:
            data = _graph_get(url, params=params)
            params = None
        else:
            data = _graph_get(url)
        for m in data.get("data", []):
            yield m
            count += 1
            if count >= MAX_MEDIA_ITEMS:
                return
        paging = data.get("paging") or {}
        url = paging.get("next")
        if not url:
            break


def _download_binary(url: str) -> bytes:
    safe = utils.validate_public_http_url(url)
    if not safe:
        raise ValueError("Blocked or invalid Instagram media URL")
    ret = requests.get(safe, timeout=120, verify=True)
    ret.raise_for_status()
    return ret.content


def _media_items_from_raw(data: dict[str, Any]) -> list[dict[str, Any]]:
    if data.get("media_type") == "CAROUSEL_ALBUM":
        children = data.get("children") or {}
        items = children.get("data") or []
        return items if items else []
    return [data]


def mirror_instagram(specific_item=None):
    """Download Instagram media (Graph raw_data) into RVMedia files."""
    if specific_item is not None:
        queue = [specific_item]
    else:
        queue = list(
            RVItem.objects.filter(
                mirror_state=0,
                service__type="instagram",
                service__live=True,
            )[:50]
        )

    result = OperationResult()
    for item in queue:
        previous_media_ids = snapshot_media_ids(item)
        try:
            logger.info(
                "Mirroring Instagram service_id=%s item_id=%s",
                item.service_id,
                item.id,
            )
            data = json.loads(item.raw_data or "{}")

            media_items = _media_items_from_raw(data)
            if not media_items:
                raise ValueError("Instagram item has no media payloads")

            for media_item in media_items:
                rvm = RVMedia()
                rvm.item = item
                rvm.save()

                media_url = media_item.get("media_url")
                if not media_url:
                    raise ValueError("Instagram media payload has no media URL")

                content = _download_binary(media_url)

                mtype = media_item.get("media_type") or "IMAGE"

                if mtype == "IMAGE":
                    ext = "jpg"
                    rvm.media_type = 1
                    output_path = rvm.make_original_path(ext)
                    rvm.save(update_fields=["media_type", "original_media"])
                    target_path = utils.make_full_path(output_path)
                    utils.make_folder(target_path)
                    with open(target_path, "wb") as fh:
                        fh.write(content)

                    img = Image.open(target_path)
                    ratio = float(img.size[0]) / float(img.size[1])
                    w = 300
                    h = max(1, int(300 / ratio))
                    img = img.resize((w, h), Image.BICUBIC)
                    output_path = rvm.make_thumbnail_path(ext)
                    rvm.save(update_fields=["thumbnail"])
                    target_path = utils.make_full_path(output_path)
                    img.save(target_path)

                elif mtype == "VIDEO":
                    ext = "mp4"
                    rvm.media_type = 2
                    output_path = rvm.make_original_path(ext)
                    rvm.save(update_fields=["media_type", "original_media"])
                    target_path = utils.make_full_path(output_path)
                    utils.make_folder(target_path)
                    with open(target_path, "wb") as fh:
                        fh.write(content)

                    thumbnail_url = media_item.get("thumbnail_url")
                    if thumbnail_url:
                        tdata = _download_binary(thumbnail_url)
                        output_path = rvm.make_thumbnail_path("jpg")
                        rvm.save(update_fields=["thumbnail"])
                        target_path = utils.make_full_path(output_path)
                        utils.make_folder(target_path)
                        with open(target_path, "wb") as fh:
                            fh.write(tdata)
                else:
                    raise ValueError("Unsupported Instagram media type")

                rvm.primary_media = rvm.original_media
                rvm.save()

            if not item.rvmedia_set.exists():
                raise ValueError("Instagram item produced no mirrored media")
            complete_media_replacement(item, previous_media_ids)
            result += OperationResult(processed=1)

        except OPERATIONAL_EXCEPTIONS as exc:
            fail_media_replacement(item, previous_media_ids)
            log_safe_exception(
                logger,
                "Instagram mirror failed service_id=%s item_id=%s",
                item.service_id,
                item.id,
                exc=exc,
            )
            result += OperationResult(failed=1)
    return result


def update_instagram() -> OperationResult:
    """Management command / cron entry: sync all live Instagram services."""
    return sync_all_instagram_services()


def fix_instagram_item(itemid):
    """Re-fetch one item from Instagram Graph and re-mirror."""
    itemid = int(itemid)
    dbitem = RVItem.objects.get(id=itemid)

    if dbitem.service.type != "instagram":
        return (False, "Not an instagram item")

    svc = dbitem.service
    if not svc.credentials.get("access_token"):
        return (False, "Instagram service has no access token")

    try:
        maybe_refresh_service_token(svc)
        svc.refresh_from_db()

        data = json.loads(dbitem.raw_data or "{}")
        media_id = data.get("id")
        if not media_id:
            return (False, "No media id in item raw_data")

        media = fetch_media_object(svc.credentials["access_token"], str(media_id))
        post_data = map_graph_media_to_raw_data(media)

        cap = post_data.get("caption") or ""
        dbitem.title = (cap[:512]) if cap else ""
        dbitem.datetime_created = parse_graph_timestamp(
            post_data.get("timestamp") or ""
        )
        dbitem.date_created = dbitem.datetime_created.date()
        dbitem.remote_url = (post_data.get("permalink") or "")[:512]
        dbitem.raw_data = json.dumps(post_data)
        dbitem.mirror_state = 0
        dbitem.save()

        result = mirror_instagram(specific_item=dbitem)
        if result.failed:
            return (False, "Instagram item mirror failed; see operational logs")
        return (True, "Item updated successfully!")
    except OPERATIONAL_EXCEPTIONS as exc:
        log_safe_exception(
            logger,
            "Instagram item repair failed service_id=%s item_id=%s",
            svc.id,
            dbitem.id,
            exc=exc,
        )
        return (False, "Instagram item update failed; see operational logs")


def sync_instagram_service(service: RVService) -> OperationResult:
    """
    Fetch media from Instagram Graph and upsert RVItem rows; mirror new/pending media.
    Returns processed and failed media counts for this run.
    """
    if utils.hours_since(service.last_checked) < 12:
        logger.info("Skipping Instagram service id=%s (checked < 12h ago)", service.id)
        return OperationResult()

    access_token = service.credentials.get("access_token", "")
    user_id = service.config.get("user_id", "")
    if not access_token or not user_id:
        logger.warning("Instagram service id=%s missing token or userid", service.id)
        return OperationResult(failed=1)

    maybe_refresh_service_token(service)
    service.refresh_from_db()

    result = OperationResult()
    for media in iter_user_media(service.credentials["access_token"], user_id):
        post_id = str(media["id"])
        # Media are returned newest-first; stop when we reach an already-mirrored post.
        if RVItem.objects.filter(
            service=service, item_id=post_id, mirror_state=1
        ).exists():
            break
        try:
            post_data = map_graph_media_to_raw_data(media)
            created_at = parse_graph_timestamp(post_data.get("timestamp") or "")
            item, _created = RVItem.objects.get_or_create(
                service=service,
                item_id=post_id,
                defaults={
                    "domain": service.domain,
                    "date_created": created_at.date(),
                    "datetime_created": created_at,
                },
            )
            cap = post_data.get("caption") or ""
            if cap:
                item.title = cap[:512]
            else:
                item.title = ""

            item.datetime_created = created_at
            item.date_created = item.datetime_created.date()
            item.remote_url = (post_data.get("permalink") or "")[:512]
            item.raw_data = json.dumps(post_data)
            item.save()

            item_result = OperationResult(processed=1)
            if item.mirror_state == 0:
                item_result += mirror_instagram(specific_item=item)
            result += item_result
        except OPERATIONAL_EXCEPTIONS as exc:
            log_safe_exception(
                logger,
                "Instagram item ingest failed service_id=%s",
                service.id,
                exc=exc,
            )
            result += OperationResult(failed=1)

    if not result.failed:
        service.last_checked = timezone.now()
        service.save(update_fields=["last_checked"])
    return result


def sync_all_instagram_services() -> OperationResult:
    result = OperationResult()
    for service in RVService.objects.filter(type="instagram", live=True):
        try:
            service_result = sync_instagram_service(service)
            result += service_result
            logger.info(
                "Instagram sync service_id=%s processed=%s failed=%s",
                service.id,
                service_result.processed,
                service_result.failed,
            )
        except Exception as exc:  # noqa: BLE001 - service isolation boundary.
            log_safe_exception(
                logger,
                "Instagram sync failed service_id=%s",
                service.id,
                exc=exc,
            )
            result += OperationResult(failed=1)
    return result
