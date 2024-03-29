

from feeds.utils import update_feeds

from feeds.models import Source, Post

from rvsite.models import RVItem, RVService, RVMedia, RVLink
from rearvue import utils

import requests
from PIL import Image

from bs4 import BeautifulSoup
from webpreview import webpreview
import datetime


def fix_rss_item(itemid):

    itemid = int(itemid)

    dbitem = RVItem.objects.get(id=itemid)

    try:
        mirror_rss(specific_item=dbitem)
        find_rss_links(specific_item=dbitem)
        return (True, "Yo!")
    except Exception as ex:
        return (False, str(ex))


def update_rss():

    rss_services = RVService.objects.filter(type="rss").filter(live=True)

    for service in rss_services:

        # make sure we are actually crawling this feed
        try:
            s = Source.objects.get(feed_url=service.auth_token)  # this is a cheesy place to put it but
        except Exception:
            s = Source()
            s.feed_url = service.auth_token
            s.save()
        update_feeds()

        for p in s.posts.all():

            try:
                item = RVItem.objects.filter(service=service).filter(item_id=p.guid)[0]
            except Exception:
                print("NEW!")
                item = RVItem(item_id=p.guid, service=service, domain=service.domain)

            item.title = p.title
            item.caption = p.body

            item.datetime_created = p.created
            item.date_created = datetime.date(year=item.datetime_created.year, month=item.datetime_created.month, day=item.datetime_created.day)

            item.remote_url = p.link

            item.raw_data = str(p.id)

            if p.enclosures.count == 0 and item.mirror_state == 0:
                item.mirror_state = 1
                item.save()
            else:
                item.save()
                if item.mirror_state == 0:
                    mirror_rss(specific_item=item)

        service.last_checked = datetime.datetime.now()
        service.save()


def mirror_rss(specific_item=None):

    if specific_item is not None:
        queue = [specific_item]
    else:
        queue = RVItem.objects.filter(mirror_state=0).filter(service__type="rss").filter(service__live=True)[:50]

    for item in queue:
        try:
            p = Post.objects.get(id=int(item.raw_data))

            for m in item.rvmedia_set.all():
                m.delete()

            for e in p.enclosures.all():

                rvm = RVMedia()
                rvm.item = item
                rvm.save()

                if e.type.startswith("image/"):
                    ret = requests.get(e.href, timeout=30, verify=False)
                    rvm.media_type = 1
                    ext = e.type.split("/")[-1]

                    if ret.ok:
                        output_path = rvm.make_original_path(ext)

                        target_path = utils.make_full_path(output_path)

                        utils.make_folder(target_path)

                        fh = open(target_path, "wb")
                        fh.write(ret.content)
                        fh.close()

                        # for rss original and priamary are the same
                        rvm.primary_media = rvm.original_media

                        img = Image.open(target_path)

                        ratio = float(img.size[0]) / float(img.size[1])

                        w = 300
                        h = int(300 / ratio)

                        print("resizing thumbnail", w, h)

                        img = img.resize((w, h), Image.BICUBIC)

                        output_path = rvm.make_thumbnail_path(ext)

                        target_path = utils.make_full_path(output_path)

                        img.save(target_path)
                elif e.type.startswith("video/"):
                    ret = requests.get(e.href, timeout=30, verify=False)
                    rvm.media_type = 2
                    ext = e.type.split("/")[-1]

                    if e.medium == "image":
                        rvm.media_type = 3

                    if ret.ok:

                        output_path = rvm.make_original_path(ext)

                        target_path = utils.make_full_path(output_path)

                        utils.make_folder(target_path)

                        fh = open(target_path, "wb")
                        fh.write(ret.content)
                        fh.close()

                rvm.save()

            item.mirror_state = 1
            item.save()
        except Exception as ex:
            print(ex)


def find_rss_links(specific_item=None):

    if specific_item is not None:
        queue = [specific_item]
    else:
        queue = RVItem.objects.filter(mirror_state=1).filter(service__type="rss").filter(service__live=True)[:50]

    for item in queue:
        try:
            last_link = ""

            # Find all the links
            # we're going to try and figure out
            # the most important one to highlight
            soup = BeautifulSoup(item.caption, "html5lib")
            for lnk in soup.findAll(name="a"):
                if lnk.has_attr("href"):
                    if lnk.text.startswith("http"):
                        last_link = lnk["href"]

            # Check for properly cited blockquotes
            if last_link == "":
                for lnk in soup.findAll(name="blockquote"):
                    if lnk.has_attr("cite"):
                        last_link = lnk["cite"]

            if last_link != "":
                print("linking up {}".format(last_link))

                try:
                    link = item.rvlink_set.filter(url=last_link)[0]
                except Exception:
                    print("NEW PREVIEW")
                    link = RVLink()
                    link.url = last_link
                    link.item = item

                print(link.url)
                link.url = utils.final_destination(link.url)
                p = webpreview(link.url, timeout=1000)

                if p.image is not None and p.image != "":
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

            item.mirror_state = 2
            item.save()

        except Exception as ex:
            print(ex)
