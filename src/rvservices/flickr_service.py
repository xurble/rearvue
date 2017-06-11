
import time
import datetime
import os

from rvsite.models import *
from rearvue import utils


from flickr import FlickrAPI

import json
import requests

from django.conf import settings

from PIL import Image



 
def update_flickr():

    f_services = RVService.objects.filter(type="flickr")
    
    for service in f_services:
    
        print "Updating %s" % service
        
    
        f = FlickrAPI(api_key=settings.FLICKR_KEY, api_secret=settings.FLICKR_SECRET, oauth_token=service.auth_token, oauth_token_secret=service.auth_secret)

        if service.userid == "":
            try:
                u = f.get("flickr.people.findByUsername", params = { "username":service.username} )  
            
                service.userid = u["user"]["id"]
                service.save()                    
            except Exception as ex:        
                print ex
                return
        
        
        
         
        
        
        page = 0
        pages = 1
        max_upload_date = 0
        while page < pages:
            page += 1
            print "Getting page ", page
            params = {
                "extras": "date_upload,date_taken,geo,machine_tags,url_t,url_o,url_l,url_z,url_m,description,media,geo",
                "user_id" : service.userid,
                "page": page
            }
            if service.max_update_id != "":
                params["min_upload_date"] = service.max_update_id
            
            pix = f.get("flickr.people.getPhotos", params = params)["photos"]
            
            pages = int(pix["pages"])
            
            for i in pix["photo"]:
                            
                dateupload = int(i["dateupload"])
                
                if dateupload > max_upload_date:
                    max_upload_date = dateupload

                try:
                    item = RVItem.objects.filter(service=service).filter(item_id=i["id"])[0]
                except:
                    print "NEW!"
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
                
                item.remote_url = "https://www.flickr.com/photos/%s/%s/" % (service.username,i["id"])

                item.raw_data = json.dumps(i)
                
                item.save()
        
        if max_upload_date:                
            service.max_update_id = str(max_upload_date)
            service.save()


def mirror_flickr():

    
    queue = RVItem.objects.filter(mirror_state=0).filter(service__type="flickr")[:50]
    
    for item in queue:
        print item.title.encode("utf-8")
        data = json.loads(item.raw_data)

        if data["media"] == "photo":
            ret = requests.get(data["url_o"])
            ext = "jpg"
            item.media_type = 1
        else:

            f = FlickrAPI(api_key=settings.FLICKR_KEY, api_secret=settings.FLICKR_SECRET, oauth_token=item.service.auth_token, oauth_token_secret=item.service.auth_secret)
            size_list = f.get("flickr.photos.getSizes", params = { "photo_id":item.item_id} )["sizes"]["size"]  

            ret = None
            for size in size_list:
                if size["label"] == "Video Original":
                    ret = requests.get(size["source"])
                    item.media_type = 2
                    ext = "mp4" # <------ need to go get that video        

        if ret.ok:
            output_path = item.make_original_path(ext)
        
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
        
        ret2 = requests.get(data[sz])

        if ret2.ok:
            output_path = item.make_primary_path("jpg")
                    
            target_path = utils.make_full_path(output_path)
    
            utils.make_folder(target_path) # <- no way this doesn't exist?
    
            fh = open(target_path,"wb")
            fh.write(ret2.content)
            fh.close()
            
            img = Image.open(target_path)
            
            ratio = float(img.size[0]) / float(img.size[1])
            
            w = 150
            h = int(150 / ratio)
            
            print "resizing thumbnail" , w, h
            
            
            img = img.resize((w,h),Image.BICUBIC)

            output_path = item.make_thumbnail_path("jpg")
                    
            target_path = utils.make_full_path(output_path)

            img.save(target_path)
    
            item.mirror_state = 1
            item.save()
                
            
            
            


            

                
                
                
        
                #get the instagram thumbnail, stops us having to postframe the movies
                
