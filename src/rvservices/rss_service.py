

from feeds.utils import update_feeds

from feeds.models import Source, Post

from rvsite.models import *
from rearvue import utils

import requests
from PIL import Image



def fix_rss_item(itemid):

    itemid = int(itemid)
    
    dbitem = RVItem.objects.get(id=itemid)

    try:
        mirror_rss(specific_item=dbitem)
        return (True, "Yo!")
    except Exception as ex:
        return (False, str(ex))

def update_rss():

    rss_services = RVService.objects.filter(type="rss").filter(live=True)
    
    for service in rss_services:
    
        #make sure we are actually crawling this feed
        try:
            s = Source.objects.get(feed_url=service.auth_token)  #this is a cheesy place to up it but
        except:
            s = Source()
            s.feed_url = service.auth_token
            s.save()
        update_feeds()
        
        for p in s.posts.all():
            
            try:
                item = RVItem.objects.filter(service=service).filter(item_id=p.guid)[0]
            except:
                print("NEW!")
                item = RVItem(item_id=p.guid, service=service, domain=service.domain)
            
            item.title = p.title
            item.caption = p.body
            
            item.datetime_created = p.created
            item.date_created     = datetime.date(year=item.datetime_created.year,month=item.datetime_created.month,day=item.datetime_created.day)

            item.remote_url = p.link
            
            item.raw_data = str(p.id)
            
            
            if p.enclosures.count == 0 or item.mirror_state == 1:
                item.mirror_state = 1
                item.save()
            else:
                item.save()
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

                        fh = open(target_path,"wb")
                        fh.write(ret.content)
                        fh.close()
    
                        #for rss original and priamary are the same
                        rvm.primary_media = rvm.original_media

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
        except Exception as ex:
            print(ex)                        
            

            
        
        
