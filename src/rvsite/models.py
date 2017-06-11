from django.db import models

from django.contrib.auth.models import User

# Create your models here.
import datetime


class RVDomain(models.Model):
    name       = models.CharField(max_length=32,unique=True)
    alt_domain = models.CharField(max_length=128,blank=True,default='',db_index=True)
    
    min_year      = models.IntegerField(default=0)
    max_year      = models.IntegerField(default=0)
    
    owner         = models.ForeignKey(User)
    
    display_name  = models.CharField(max_length=128,default='RearVue')
    poster_image  = models.ForeignKey('RVItem',null=True,blank=True)
    
    blurb         = models.TextField(null=True,blank=True,default='')
    
    def __unicode__(self):
        return "%s - %s" % (self.name,self.display_name)


class RVService(models.Model):
    name           = models.CharField(max_length=512)
    domain         = models.ForeignKey(RVDomain)
    type           = models.CharField(max_length=128)
    last_checked   = models.DateTimeField(default=datetime.datetime(2015, 1, 10, 17, 26, 51, 977260)) #old date makes it get checked right away
    username       = models.CharField(max_length=128,blank=True,default='')
    userid         = models.CharField(max_length=128,blank=True,default='')
    profile_pic    = models.CharField(max_length=512,blank=True,default='')
    max_update_id  = models.CharField(max_length=256,blank=True,default='')
    auth_token     = models.CharField(max_length=256,blank=True,default='')
    auth_secret    = models.CharField(max_length=256,blank=True,default='')
    
    
    
    def __unicode__(self):
        
        return "%s (%s)" % (self.name,self.type)


class RVItem(models.Model):

    service          = models.ForeignKey(RVService)
    domain           = models.ForeignKey(RVDomain)

    item_id          = models.CharField(max_length=128,db_index=True)

    date_retrieved   = models.DateTimeField(auto_now_add=True)
    date_created     = models.DateField(db_index=True)
    datetime_created = models.DateTimeField(db_index=True)

    remote_url       = models.CharField(max_length=512,blank=True,default='')

    title            = models.CharField(max_length=512,blank=True,default='')
    caption          = models.TextField(blank=True,default='')
    
    public           = models.BooleanField(default=True)

    original_media   = models.CharField(max_length=256,blank=True,default='')

    primary_media    = models.CharField(max_length=256,blank=True,default='')
    media_type       = models.IntegerField(default=0,choices=((0,"None"),(1,"Image"),(2,"Video")))
    thumbnail        = models.CharField(max_length=256,blank=True,default='')
    

    raw_data         = models.TextField(blank=True,default='')

    mirror_state     = models.IntegerField(default=0)
    


    def make_original_path(self,file_type):
    
        self.original_media = "media/%s/%d/%02d/%02d/%s_%d_o.%s" % (self.domain.name,self.date_created.year,self.date_created.month,self.date_created.day,self.service.type,self.id,file_type)
        return self.original_media
        
    def make_primary_path(self,file_type):
    
        self.primary_media = "media/%s/%d/%02d/%02d/%s_%d_p.%s" % (self.domain.name,self.date_created.year,self.date_created.month,self.date_created.day,self.service.type,self.id,file_type)
        return self.primary_media

    def make_thumbnail_path(self,file_type):
    
        self.thumbnail =  "media/%s/%d/%02d/%02d/%s_%d_t.%s" % (self.domain.name,self.date_created.year,self.date_created.month,self.date_created.day,self.service.type,self.id,file_type)
        return self.thumbnail 

