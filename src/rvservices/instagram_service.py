

import time
import datetime
import os

from django.conf import settings

from rvsite.models import *
from rearvue import utils

from instagram_basic_display.InstagramBasicDisplay import InstagramBasicDisplay

import requests
from PIL import Image


import ssl
ssl._create_default_https_context = ssl._create_unverified_context


import json


def fix_instagram_item(itemid):

    itemid = int(itemid)
    
    dbitem = RVItem.objects.get(id=itemid)
    
    if dbitem.service.type != "instagram":
        return (False, "Not an instagram item")
        
    data = json.loads(dbitem.raw_data)    
    post_id = data["pk"]

    insta = Client(dbitem.service.username, dbitem.service.auth_secret)

    ret = insta.user_feed(dbitem.service.userid)
    
    for i in ret["items"]:

        if i["pk"] == post_id:
            if i["caption"]:
                dbitem.title = i["caption"]["text"]
            else:
                dbitem["caption"] = ""

            dbitem.datetime_created = datetime.datetime.fromtimestamp(int(i["taken_at"]))
            dbitem.date_created     = datetime.date(year=dbitem.datetime_created.year,month=dbitem.datetime_created.month,day=dbitem.datetime_created.day)

            dbitem.remote_url = "https://www.instagram.com/p/{}/".format(i["code"])

            dbitem.raw_data = json.dumps(i)
    
            dbitem.save()
    
            mirror_instagram(specific_item=dbitem)
    
            return (True, "Yo!")
        
    return (False, "Couldn't find the post.") 
        


def update_instagram():

    date_format = '%Y-%m-%dT%H:%M:%S%z'

    
    ig_services = RVService.objects.filter(type="instagram").filter(live=True)
    
    for service in ig_services:
    
        print("Updating %s" % service)
    
        redirect_uri = '{protocol}://{domain}/rvadmin/instagram_return/'.format(protocol='https', domain="xurble.org")

        insta = InstagramBasicDisplay(
            app_id=settings.INSTAGRAM_KEY, 
            app_secret=settings.INSTAGRAM_SECRET,
            redirect_url=redirect_uri)

        insta.set_access_token(service.auth_token)    
            
        if service.max_update_id != "":
            max_id = int(service.max_update_id)
        else:
            max_id = None    

        media = insta.get_user_media(user_id='me')        


        while True:        

            if media is None:
                break
            items = media["data"]

            for i in items:
                if "id" in i:
                    print(i["id"])
                    lastphoto = i["id"]
                    try:
                        item = RVItem.objects.filter(service=service).filter(item_id=lastphoto)[0]
                    except:
                        print("NEW!")
                        item = RVItem(item_id=lastphoto,service=service,domain=service.domain)
                    if "caption" in i:
                        item.title = i["caption"]
                        print(item.title.encode("utf-8"))

                    item.datetime_created = datetime.datetime.strptime(i["timestamp"], date_format).replace(tzinfo=None)
                    item.date_created     = datetime.date(year=item.datetime_created.year,month=item.datetime_created.month,day=item.datetime_created.day)

                    item.remote_url = i["permalink"]

                    item.raw_data = json.dumps(i)
                
                    item.save()    
                    
                    if item.mirror_state == 0:
                        mirror_instagram(specific_item=item)            
                
                    if max_id is None or lastphoto > max_id:
                        max_id = lastphoto

            media = insta.pagination(media)
                
            
            
def mirror_instagram(specific_item=None):
    
    if specific_item is not None:
        queue = [specific_item]
    else:
        queue = RVItem.objects.filter(mirror_state=0).filter(service__type="instagram").filter(service__live=True)[:50]
    
    for item in queue:
        try:    
            print(item.caption)
            data = json.loads(item.raw_data)
            
            for m in item.rvmedia_set.all():
                m.delete()

            if data["media_type"] != "CAROUSEL_ALBUM":
                data["children"] = {
                    "data": [data,]
                }

            for media_item in data["children"]["data"]:
            
                rvm = RVMedia()
                rvm.item = item
                rvm.save()

                ret = requests.get(media_item["media_url"],timeout=30, verify=False)
                if ret.ok:
                
                    if media_item["media_type"] == "IMAGE":
                        ext = "jpg"
                        rvm.media_type = 1

                        output_path = rvm.make_original_path(ext)
        
                        target_path = utils.make_full_path(output_path)
    
                        utils.make_folder(target_path)

                        fh = open(target_path,"wb")
                        fh.write(ret.content)
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
                    else:
                        ext = "mp4"
                        rvm.media_type = 2

                        output_path = rvm.make_original_path(ext)
        
                        target_path = utils.make_full_path(output_path)
    
                        utils.make_folder(target_path)

                        fh = open(target_path,"wb")
                        fh.write(ret.content)
                        fh.close()
                        
                        
                        ret = requests.get(media_item["thumbnail_url"],timeout=30, verify=False)

                        output_path = rvm.make_thumbnail_path("jpg")
        
                        target_path = utils.make_full_path(output_path)
    
                        utils.make_folder(target_path)

                        fh = open(target_path,"wb")
                        fh.write(ret.content)
                        fh.close()

                    rvm.primary_media = rvm.original_media
                    print ("SAVING: " + rvm.primary_media )
                    rvm.save()

                else:     
                    rvm.delete()  
                    print("NOPE!!!!!!!!!")
                    return 
                    
        
            item.mirror_state = 1
            item.save()
        except Exception as ex:
            print(ex)
        
        
        
    
