
from datetime import datetime
import ipaddress
import logging
import os
import random
import socket
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup
from django.conf import settings
from django.http import HttpResponseNotFound, HttpResponseForbidden
import requests
from webpreview import webpreview

from rvsite.models import RVDomain, RVLink

logger = logging.getLogger(__name__)

MONTH_LIST = ["X", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

MAX_REDIRECT_HOPS = 10
REDIRECT_STATUSES = {301, 302, 303, 307, 308}

BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "metadata",
    "metadata.google.internal",
})


def _ip_is_public_safe(ip):
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_host_is_public(host):
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if not _ip_is_public_safe(ip):
            return False
    return bool(infos)


def validate_public_http_url(url):
    """
    Return a canonical URL string if it is safe for the server to request, else None.
    Only http/https to public addresses are allowed (SSRF mitigation).
    """
    if not url or not isinstance(url, str):
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return None
    host = parsed.hostname
    if not host:
        return None
    lh = host.lower().rstrip(".")
    if lh in BLOCKED_HOSTNAMES:
        return None
    if lh.endswith(".local") or lh.endswith(".localhost"):
        return None
    try:
        ip = ipaddress.ip_address(host)
        if not _ip_is_public_safe(ip):
            return None
        return parsed.geturl()
    except ValueError:
        pass
    if not _resolve_host_is_public(host):
        return None
    return parsed.geturl()


def make_full_path(local_path):

    return os.path.join(settings.DATA_STORE, local_path)


def make_folder(full_path_to_file):

    folder = os.path.dirname(full_path_to_file)

    if not os.path.exists(folder):
        os.makedirs(folder)


def _resolve_domain(host_header):
    try:
        return RVDomain.objects.get(name=host_header)
    except RVDomain.DoesNotExist:
        pass
    qs = RVDomain.objects.filter(alt_domain__icontains=host_header).order_by("id")
    if qs.exists():
        return qs.first()
    return None


def page(func):

    def _page(*args, **kwargs):

        request = args[0]

        domain = request.META.get("HTTP_HOST", "")

        request.domain = _resolve_domain(domain)
        if request.domain is None:
            logger.warning("Unknown host: %s", domain)
            return HttpResponseNotFound()

        request.vals = {}
        request.vals["domain"] = request.domain
        request.vals["year_range"] = list(range(request.domain.min_year, request.domain.max_year+1))

        return func(*args, **kwargs)

    return _page


def admin_page(func):

    def _page(*args, **kwargs):

        request = args[0]

        domain = request.META.get("HTTP_HOST", "")

        request.domain = _resolve_domain(domain)
        if request.domain is None:
            logger.warning("Unknown host (admin): %s", domain)
            return HttpResponseNotFound()

        if not request.user.is_superuser:
            return HttpResponseForbidden()

        request.vals = {}
        request.vals["domain"] = request.domain

        return func(*args, **kwargs)

    return _page


def sample_of(source_list, requirement):

    if requirement > len(source_list):
        requirement = len(source_list)

    return random.sample(source_list, requirement)


def hours_since(date):

    td = (datetime.now()-date.replace(tzinfo=None))
    return (td.days * 24) + int(td.seconds // 3600)


def get_extension(from_url):

    path = from_url.split("?")[0]
    if "." not in path:
        return ""
    return path.rsplit(".", 1)[-1]


def make_link(link_url, item, is_context=False):

    try:
        if not validate_public_http_url(link_url):
            return (False, "URL scheme or host is not allowed")

        dest = final_destination(link_url)
        if not validate_public_http_url(dest):
            return (False, "Redirect led to a URL that is not allowed")

        link = RVLink()
        link.url = dest
        link.item = item
        link.is_context = is_context

        p = webpreview(link.url, timeout=1000)

        if p.image is not None and p.image != "" and p.image != "None":
            if validate_public_http_url(p.image):
                ret = requests.get(p.image, timeout=30)
                if not ret.ok:
                    p.image = ""
            else:
                p.image = ""

        # Cloudflare :(
        if p.title != "Access denied":

            link.title = p.title
            link.image = p.image
            link.description = p.description

            if link.title is None:
                link.title = ""
            if link.image is None:
                link.image = ""
            if link.description is None:
                link.description = ""

            link.save()
            return (True, "👍")
        else:
            return (False, "Access Denied")
    except Exception as ex:
        return (False, str(ex))


def final_destination(url):
    """
    Follow redirects and meta refreshes. Does not fetch non-public URLs (returns input unchanged).
    """
    validated = validate_public_http_url(url)
    if not validated:
        return url

    current = validated
    hop = 0
    ua = getattr(settings, "FEEDS_USER_AGENT", "RearVue")

    while hop < MAX_REDIRECT_HOPS:
        hop += 1
        rr = requests.get(
            current,
            timeout=30,
            verify=True,
            allow_redirects=False,
            headers={"User-Agent": ua},
        )

        if rr.status_code in REDIRECT_STATUSES:
            loc = rr.headers.get("Location")
            if not loc:
                return current
            nxt = urljoin(current, loc)
            nxt_safe = validate_public_http_url(nxt)
            if not nxt_safe:
                return current
            current = nxt_safe
            continue

        if rr.url and rr.url != current:
            nxt_safe = validate_public_http_url(rr.url)
            if nxt_safe:
                current = nxt_safe
                continue

        try:
            body = rr.content.decode("utf-8")
        except Exception:
            body = str(rr.content)
        soup = BeautifulSoup(body, "html5lib")

        keepgoing = False
        for m in soup.find_all("meta"):
            if m.get("http-equiv", "").lower() == "refresh" and m.get("content") and "url=" in m["content"]:
                raw = m["content"].split("url=", 1)[1].strip().strip("'\"")
                nxt = urljoin(current, raw)
                nxt_safe = validate_public_http_url(nxt)
                if not nxt_safe:
                    return current
                current = nxt_safe
                keepgoing = True
                break

        if not keepgoing:
            return current

    return current
