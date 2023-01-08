

from feeds.utils import update_feeds

from feeds.models import Source

from rvsite.models import *
from rearvue import utils


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
            
            
            if p.enclosures.count == 0:
                item.mirror_state = 1
        
            item.save()
        
        service.last_checked = datetime.datetime.now()
        service.save()
                        
            

            
        
        
