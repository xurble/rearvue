
from datetime import datetime
import os
import random

from bs4 import BeautifulSoup
from django.conf import settings
from django.http import HttpResponseNotFound, HttpResponseForbidden
import requests
from webpreview import webpreview

from rvsite.models import RVDomain, RVLink

MONTH_LIST = ["X", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def make_full_path(local_path):

    return os.path.join(settings.DATA_STORE, local_path)


def make_folder(full_path_to_file):

    folder = os.path.dirname(full_path_to_file)

    if not os.path.exists(folder):
        os.makedirs(folder)


def page(func):

    def _page(*args, **kwargs):

        request = args[0]

        domain = request.META["HTTP_HOST"]

        try:
            request.domain = RVDomain.objects.get(name=domain)
        except Exception:
            try:
                request.domain = RVDomain.objects.filter(alt_domain__icontains=domain)[0]
            except Exception as ex:
                print(ex)
                return HttpResponseNotFound()

        request.vals = {}
        request.vals["domain"] = request.domain
        request.vals["year_range"] = list(range(request.domain.min_year, request.domain.max_year+1))

        return func(*args, **kwargs)

    return _page


def admin_page(func):

    def _page(*args, **kwargs):

        request = args[0]

        domain = request.META["HTTP_HOST"]

        try:
            request.domain = RVDomain.objects.get(name=domain)
        except Exception:
            try:
                request.domain = RVDomain.objects.filter(alt_domain__icontains=domain)[0]
            except Exception as ex:
                print(ex)
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

    noquery = from_url.split("?")[-1]
    return noquery.split(".")[-1]


def make_link(link_url, item, is_context=False):

    try:

        link = RVLink()
        link.url = final_destination(link_url)
        link.item = item
        link.is_context = is_context

        print(link.url)

        p = webpreview(link.url, timeout=1000)

        if p.image is not None and p.image != "" and p.image != "None":
            ret = requests.get(p.image, timeout=30)
            if not ret.ok:
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

    result = url

    while True:
        print(url)

        rr = requests.get(url, timeout=30, verify=False)
        if rr.url != url:
            # we got a redirect and followed it, see how that works out for us
            result = rr.url
            url = rr.url
        else:
            # we got some content
            try:
                page = rr.content.decode("utf-8")
            except Exception:
                page = str(rr.content)
            soup = BeautifulSoup(page, "html5lib")

            keepgoing = False
            # meta refresh?
            for m in soup.findAll("meta"):
                if m.has_attr("http-equiv") and m["http-equiv"] == "refresh":
                    if m.has_attr("content") and "url=" in m["content"]:
                        result = m["content"].split("url=")[1]
                        url = result
                        keepgoing = True
                        break

            if not keepgoing:
                return result
