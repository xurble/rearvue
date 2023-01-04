

import time
import datetime
import os

from rvsite.models import *
from rearvue import utils



from instagram_private_api import Client, ClientCompatPatch

import requests

# We use the client for the auth, but here we just freestyle it because we want raw JSON
INSTAGRAM_MEDIA_URL = "https://api.instagram.com/v1/users/self/media/recent.json?access_token=%s&count=%d"

import json

def update_instagram():


    ig_services = RVService.objects.filter(type="instagram")
    
    for service in ig_services:
    
        print("Updating %s" % service)
    
        insta = Client(service.username, service.auth_secret)
        
        service.userid = insta.authenticated_user_id # just in case

        if service.max_update_id != "":
            max_id = int(service.max_update_id)
        else:
            max_id = 0    
        
        
        
        ret = insta.user_feed(service.userid, min_id=max_id)
        
        items = ret["items"]

        next_max_id = ret.get('next_max_id')
        while next_max_id:
            ret = insta.user_feed(service.userid, max_id=next_max_id)
            items.extend(ret.get('items', []))
            next_max_id = ret.get('next_max_id')

        for i in items:
            if "pk" in i:
                print(i["pk"])
                lastphoto = i["pk"]
                try:
                    item = RVItem.objects.filter(service=service).filter(item_id=lastphoto)[0]
                except:
                    print("NEW!")
                    item = RVItem(item_id=lastphoto,service=service,domain=service.domain)
                if i["caption"]:
                    item.title = i["caption"]["text"]
                    print(item.title.encode("utf-8"))
            
                item.datetime_created = datetime.datetime.fromtimestamp(int(i["taken_at"]))
                item.date_created     = datetime.date(year=item.datetime_created.year,month=item.datetime_created.month,day=item.datetime_created.day)

                item.remote_url = "https://www.instagram.com/p/{}/".format(i["code"])

                item.raw_data = json.dumps(i)
                
                item.save()
                
                
                if lastphoto > max_id:
                    max_id = lastphoto
                
            
        if max_id:                
            service.max_update_id = str(max_id)
            service.save() 
            
def mirror_instagram():
    
    queue = RVItem.objects.filter(mirror_state=0).filter(service__type="instagram")[:50]
    
    for item in queue:
        try:
            print(item.caption)
            data = json.loads(item.raw_data)

            if "carousel_media_count" not in data:
                data["carousel_media"] = [data,]

            for media_item in data["carousel_media"]:
            
                rvm = RVMedia()
                rvm.item = item
                rvm.save()
                dl_media = None
                dl_thumb = None
                for m in media_item["image_versions2"]["candidates"]:
                    if m["width"] == media_item["original_width"] and m["height"] == media_item["original_height"]:
                        dl_media = m
                    elif dl_media is None:
                        dl_media = m
                    elif dl_media["width"] < m["width"]:
                        dl_media = m
                        
                    if dl_thumb is None:
                        dl_thumb = m
                    elif dl_thumb["width"] > m["width"]:
                        dl_thumb = m
                    

                    if media_item["media_type"] == 1:
                        ret = requests.get(dl_media["url"],timeout=30, verify=False)
                        ext = "jpg"
                        rvm.media_type = 1
                    else:
                        import pdb; pdb.set_trace()
                        
                        ext = "mp4"
                        ret = requests.get(dl_media["url"],timeout=30, verify=False)
                        rvm.media_type = 2
        
                    if ret.ok:
                        output_path = rvm.make_original_path(ext)
        
                        target_path = utils.make_full_path(output_path)
    
                        utils.make_folder(target_path)

                        fh = open(target_path,"wb")
                        fh.write(ret.content)
                        fh.close()
    
                        #for instagram original and priamary are the same
                        rvm.primary_media = rvm.original_media
    
                    #get the instagram thumbnail, stops us having to postframe the movies
                    ret2 = requests.get(dl_thumb["url"],timeout=30, verify=False)
                    if ret2.ok:
                        output_path = rvm.make_thumbnail_path("jpg")
                    
                        target_path = utils.make_full_path(output_path)

                        utils.make_folder(target_path) # <- no way this doesn't exist?

                        fh = open(target_path,"wb")
                        fh.write(ret2.content)
                        fh.close()
                rvm.save()
        
            item.mirror_state = 1
            item.save()
        except Exception as ex:
            print(ex)
        
        
        
    
