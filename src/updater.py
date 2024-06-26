
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rearvue.settings")
django.setup()


from rvsite.models import RVItem, RVDomain

from django.db.models import Max, Min
from django.utils import timezone
from django.template.loader import render_to_string

from rvservices.instagram_service import update_instagram
from rvservices.flickr_service import update_flickr, mirror_flickr
from rvservices.rss_service import update_rss, mirror_rss, find_rss_links
from rvservices.twitter_service import find_twitter_links, mirror_twitter

from rearvue.utils import make_full_path

import ssl


ssl._create_default_https_context = ssl._create_unverified_context


if __name__ == "__main__":

    print("Updating RSS")
    try:
        update_rss()
    except Exception as ex:
        print(ex)

    try:
        mirror_rss()
        find_rss_links()
    except Exception as ex:
        print(ex)

    print("Updating Twitter")
    try:
        mirror_twitter()
        find_twitter_links()
    except Exception as ex:
        print(ex)

    print("Updating Instagram")
    try:
        update_instagram()
    except Exception as ex:
        print(ex)

    # print("Mirroring Instagram")
    # try:
    #    mirror_instagram()
    # except Exception as ex:
    #    print(ex)

    print("Updating Flickr")
    try:
        update_flickr()
    except Exception as ex:
        print(ex)

    print("Mirroring Flickr")
    try:
        mirror_flickr()
    except Exception as ex:
        print(ex)

    print("Cleaning up domains")
    # this fixes the min and max years on all services
    for domain in RVDomain.objects.all():

        print("doing min/max on ", domain)

        try:
            max_year = RVItem.objects.filter(service__domain=domain).aggregate(Max('datetime_created'))["datetime_created__max"]
            min_year = RVItem.objects.filter(service__domain=domain).aggregate(Min('datetime_created'))["datetime_created__min"]

            domain.max_year = max_year.year
            domain.min_year = min_year.year

            domain.last_updated = timezone.now()

            domain.save()
        except Exception as ex:
            print(ex)
            pass

        rss_path = make_full_path(f"media/{domain.name}/rss.xml")
        with open(rss_path, "w") as out:
            vals = {
                "domain": domain,
                "items": RVItem.objects.filter(service__domain=domain).filter(public=True).order_by("-datetime_created")[:25]
            }
            out.write(render_to_string("rss.xml", vals))
