
import time
import datetime
import os

from rvsite.models import *
from rearvue import utils

from django.utils import timezone
import flickrapi

import json
import requests

from django.conf import settings

from PIL import Image
 
def update_flickr():

    f_services = RVService.objects.filter(type="flickr")
    
    for service in f_services:

        if utils.hours_since(service.last_checked) < 12:
            print("Skipping {s} (too soon)".format(s=service))
        else:
            print("Updating {s}".format(s=service))
        
            """
            def __init__(self, token, token_secret, access_level,
                         fullname=u'', username=u'', user_nsid=u''):
            """

            token = flickrapi.auth.FlickrAccessToken(token=service.auth_token, token_secret=service.auth_secret, access_level='read', username=service.username, user_nsid=service.userid)
    
            f = flickrapi.FlickrAPI(settings.FLICKR_KEY, settings.FLICKR_SECRET, token=token, format='parsed-json')

            if service.userid == "":
                try:
                    u = f.people.findByUsername( username = service.username )  
            
                    service.userid = u["user"]["id"]
                    service.save()                    
                except Exception as ex:        
                    print(ex)
                    return
        
        
            page = 0
            pages = 1
            max_upload_date = 0
            while page < pages:
                page += 1
                print("Getting page ", page)
  
                if service.max_update_id != "":
                    min_date = service.max_update_id
                else:
                    min_date = None
                
                pix = f.people.getPhotos(
                    extras="date_upload,date_taken,geo,machine_tags,url_t,url_o,url_l,url_z,url_m,description,media,geo",
                    user_id=service.userid,
                    page=page,
                    min_upload_date=min_date)["photos"]
            
                pages = int(pix["pages"])
            
                for i in pix["photo"]:
                            
                    dateupload = int(i["dateupload"])
                
                    if dateupload > max_upload_date:
                        max_upload_date = dateupload

                    try:
                        item = RVItem.objects.filter(service=service).filter(item_id=i["id"])[0]
                    except:
                        print("NEW!")
                        item = RVItem(item_id=i["id"],service=service,domain=service.domain)

                    item.title = i["title"]
                    item.caption = i["description"]["_content"]
                
                    item.public = (i["ispublic"] == 1)
                
                    if "datetaken"  in i:
                        taken_datetime  = datetime.datetime.strptime(i["datetaken"],'%Y-%m-%d %H:%M:%S')
                    else:
                        taken_datetime = datetime.datetime.fromtimestamp(dateupload)
                    
                    item.datetime_created = taken_datetime
                    item.date_created     = taken_datetime.date()
                
                    item.remote_url = "https://www.flickr.com/photos/%s/%s/" % (service.username, i["id"])

                    item.raw_data = json.dumps(i)
                
                    item.save()
        
            if max_upload_date:                
                service.max_update_id = str(max_upload_date)
            service.last_checked = timezone.now()
            service.save()


def mirror_flickr():

    f_services = RVService.objects.filter(type="flickr")
    
    for service in f_services:

        token = flickrapi.auth.FlickrAccessToken(token=service.auth_token, token_secret=service.auth_secret, access_level='read', username=service.username, user_nsid=service.userid)
        f = flickrapi.FlickrAPI(settings.FLICKR_KEY, settings.FLICKR_SECRET, token=token, format='parsed-json')
    
        queue = RVItem.objects.filter(mirror_state=0).filter(service=service)[:100]

        for item in queue:
            print(item.title.encode("utf-8"))
            data = json.loads(item.raw_data)

            rvm = RVMedia()
            rvm.item = item
            rvm.save()
            
            print(item.date_created)
            

            if data["media"] == "photo":
                ret = requests.get(data["url_o"],timeout=30, verify=False)
                ext = data["url_o"].split(".")[-1]
                rvm.media_type = 1
            else:
                size_list = f.photos.getSizes(photo_id=item.item_id)["sizes"]["size"]  

                ret = None
                for size in size_list:
                    if size["label"] == "Video Original":
                        ret = requests.get(size["source"],timeout=30, verify=False)
                        rvm.media_type = 2
                        ext = "mp4" # <------ need to go get that video        

            if ret.ok:
                output_path = rvm.make_original_path(ext)
        
                target_path = utils.make_full_path(output_path)
    
                utils.make_folder(target_path)

                fh = open(target_path,"wb")
                fh.write(ret.content)
                fh.close()
            
            sz = "url_l"
            if sz not in data:
                sz = "url_m"
            if sz not in data:
                sz = "url_o"
        
            ret2 = requests.get(data[sz],timeout=30, verify=False)

            ext = data[sz].split(".")[-1]

            if ret2.ok:
                output_path = rvm.make_primary_path(ext)
                    
                target_path = utils.make_full_path(output_path)
    
                utils.make_folder(target_path) # <- no way this doesn't exist?
    
                fh = open(target_path,"wb")
                fh.write(ret2.content)
                fh.close()
            
                img = Image.open(target_path)
            
                ratio = float(img.size[0]) / float(img.size[1])
            
                w = 300
                h = int(300 / ratio)
            
                print("resizing thumbnail" , w, h)
            
            
                img = img.resize((w,h),Image.BICUBIC)

                output_path = rvm.make_thumbnail_path(ext)
                    
                target_path = utils.make_full_path(output_path)

                img.save(target_path)
    
                rvm.save()
                item.mirror_state = 1
                item.save()
                
                
