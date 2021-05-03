
import os
from django.conf import settings

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rearvue.settings")

django.setup()

from rvsite.models import *

from django.db.models import Max,Min


from rvservices.instagram_service import update_instagram,mirror_instagram
from rvservices.flickr_service import update_flickr,mirror_flickr


if __name__ == "__main__":

    print("Updating Instagram")
    try:
        update_instagram()
    except Exception as ex:
        print(ex)

    print("Mirroring Instagram")
    try:
        mirror_instagram()
    except Exception as ex:
        print(ex)
    
    
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
    
            domain.save()
        except Exception as ex:
            print(ex)
            pass
            