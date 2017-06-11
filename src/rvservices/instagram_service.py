

import time
import datetime
import os

from rvsite.models import *
from rearvue import utils


import requests
from instagram.client import InstagramAPI

# We use the client for the auth, but here we just freestyle it because we want raw JSON
INSTAGRAM_MEDIA_URL = "https://api.instagram.com/v1/users/self/media/recent.json?access_token=%s&count=%d"

import json

def update_instagram():
    
    ig_services = RVService.objects.filter(type="instagram")
    
    for service in ig_services:
    
        print "Updating %s" % service
    
        
        since = None
        if service.max_update_id != "":
            since = service.max_update_id + "_" + service.userid
        page = 1
        maxid = 0
        if since:
            url = INSTAGRAM_MEDIA_URL % (service.auth_token,25) + "&min_id=" + since
        else:
            url = INSTAGRAM_MEDIA_URL % (service.auth_token,25) 
            
        #print url
        
        r = requests.get(url)
        
        media_list = r.json()["data"]

        lastphoto = ""    
        while len(media_list):
            print "PAGE:",page,since
            for i in media_list:
                if i.has_key("id"):
                    print i["id"]
                    lastphoto = i["id"]
                
                
                
                    try:
                        item = RVItem.objects.filter(service=service).filter(item_id=i["id"])[0]
                    except:
                        print "NEW!"
                        item = RVItem(item_id=lastphoto,service=service,domain=service.domain)
                    if i["caption"]:
                        item.title = i["caption"]["text"]
                        print item.title.encode("utf-8")
                
                    item.datetime_created = datetime.datetime.fromtimestamp(int(i["created_time"]))
                    item.date_created     = datetime.date(year=item.datetime_created.year,month=item.datetime_created.month,day=item.datetime_created.day)

                    item.remote_url = i["link"]

                    item.raw_data = json.dumps(i)
                    
                    item.save()
                
                    this_id = int(i["id"].split("_")[0])
                    if this_id > maxid:
                        maxid = this_id
                else:
                    import pdb; pdb.set_trace()
                    pass
            page +=1    

            print "Page received, taking a break!"
            time.sleep(5)
            if len(media_list) < 25:
                #didn't get as many as we asked for so must be done
                break
                
            url = INSTAGRAM_MEDIA_URL % (service.auth_token,50) + "&max_id=" + lastphoto
            if since:
                url += "&min_id=" + since

            print url
            r = requests.get(url)
    
            media_list = r.json()["data"]
        if maxid:                
            service.max_update_id = str(maxid)
            service.save() 
            
def mirror_instagram():
    
    queue = RVItem.objects.filter(mirror_state=0).filter(service__type="instagram")[:50]
    
    for item in queue:
        print item.caption
        data = json.loads(item.raw_data)
        if data["type"] == "image":
            ret = requests.get(data["images"]["standard_resolution"]["url"])
            ext = "jpg"
            item.media_type = 1
        else:
            ext = "mp4"
            ret = requests.get(data["videos"]["standard_resolution"]["url"])
            item.media_type = 2
            
        if ret.ok:
            output_path = item.make_original_path(ext)
            
            target_path = utils.make_full_path(output_path)
        
            utils.make_folder(target_path)

            fh = open(target_path,"wb")
            fh.write(ret.content)
            fh.close()
        
            #for instagram original and priamary are the same
            item.primary_media = item.original_media
        
            #get the instagram thumbnail, stops us having to postframe the movies
                
        ret2 = requests.get(data["images"]["thumbnail"]["url"])
        if ret2.ok:
            output_path = item.make_thumbnail_path("jpg")
                            
            target_path = utils.make_full_path(output_path)
        
            utils.make_folder(target_path) # <- no way this doesn't exist?

            fh = open(target_path,"wb")
            fh.write(ret2.content)
            fh.close()
        
        item.mirror_state = 1
        item.save()
        
        
        
    
