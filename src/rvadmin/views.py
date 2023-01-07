from django.shortcuts import render ,get_object_or_404
from django.template import RequestContext
from django.http import HttpResponseRedirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from instagram_basic_display.InstagramBasicDisplay import InstagramBasicDisplay
from flickrapi import FlickrAPI

import datetime
import requests
import json

from django.conf import settings

from rearvue.utils import admin_page

from rvsite.models import *

# Create your views here.

import rvservices.instagram_service
import rvservices.flickr_service


@login_required
@admin_page
def admin_index(request):

    request.vals["services"] = RVService.objects.filter(domain=request.domain)
    return render(request, "rvadmin/index.html", request.vals)
    
    
@login_required
@admin_page
def fix_item(request, iid):
    
    dbitem = RVItem.objects.get(id=iid)
    
    if dbitem.service.type == "instagram":
        (ok, msg) = rvservices.instagram_service.fix_instagram_item(iid)
        if ok:
            messages.info(request, "OK")
        else:
            messages.warning(request, msg)
    else:
        messages.error(request, "Can only do instagram right now.")
        
        
    return HttpResponseRedirect(request.META["HTTP_REFERER"])


@login_required
@admin_page
def instagram_connect(request, iid):

    vals = {}
    
    if request.method == "POST":


        if iid == "new":
            svc = RVService(name='Instagram Service',type="instagram",domain=request.domain)
            svc.save()
        else:
            svc = get_object_or_404(RVService,id=int(iid))


        #redirect_uri = '{protocol}://{domain}/rvadmin/instagram_return/?svc={service}'.format(protocol = settings.DEFAULT_DOMAIN_PROTOCOL, domain=request.domain.name, service= svc.id)
        redirect_uri = '{protocol}://{domain}/rvadmin/instagram_return/'.format(protocol='https', domain="xurble.org")


        instagram_basic_display = InstagramBasicDisplay(
            app_id=settings.INSTAGRAM_KEY, 
            app_secret=settings.INSTAGRAM_SECRET,
            redirect_url=redirect_uri)

        return HttpResponseRedirect(instagram_basic_display.get_login_url())

    else:
        return render(request,"rvadmin/instagram_connect.html",vals)
    
    
@login_required
def instagram_return(request):

    code = request.GET.get("code","")
    
    service = RVService.objects.filter(type="instagram").first()
    
    redirect_uri = '{protocol}://{domain}/rvadmin/instagram_return/'.format(protocol='https', domain="xurble.org")

    instagram_basic_display = InstagramBasicDisplay(
            app_id=settings.INSTAGRAM_KEY, 
            app_secret=settings.INSTAGRAM_SECRET,
            redirect_url=redirect_uri)
            
    # Get the short lived access token (valid for 1 hour)
    short_lived_token = instagram_basic_display.get_o_auth_token(code)

    # Exchange this token for a long lived token (valid for 60 days)
    long_lived_token = instagram_basic_display.get_long_lived_token(short_lived_token.get('access_token'))

    token = long_lived_token["access_token"]
    
    instagram_basic_display.set_access_token(token)    
    
    profile = instagram_basic_display.get_user_profile()    
    
    service.username    = profile["username"]
    service.userid      = profile["id"]
    service.auth_token  = token
    
    service.save() 
    
    

    vals = {}
    
    return HttpResponseRedirect("/rvadmin/")  #todo return reverse
    
@login_required
@admin_page
def flickr_connect(request, iid):

    vals = {}
    
    if request.method == "POST":

        if iid == "new":
            svc = RVService(name='Flickr Service',type="Flickr",domain=request.domain)
            svc.save()
        else:
            svc = get_object_or_404(RVService,id=int(iid))

        if request.domain.alt_domain != '' :
            redirect_uri = '{domain}/rvadmin/flickr_return/?svc={service}'.format(domain=request.domain.alt_domain, service= svc.id) 
        else:
            redirect_uri = '{protocol}://{domain}/rvadmin/flickr_return/?svc={service}'.format(protocol = settings.DEFAULT_DOMAIN_PROTOCOL, domain=request.domain.name, service= svc.id) 
    
        f = FlickrAPI(settings.FLICKR_KEY, settings.FLICKR_SECRET, token=None, store_token=False)
                       
        f.get_request_token(oauth_callback=redirect_uri)

        print('Redirecting user to Flickr to get frob')
        url = f.auth_url(perms='read')
        
        svc.auth_token = f.flickr_oauth.resource_owner_key
        svc.auth_secret = f.flickr_oauth.resource_owner_secret
        
        svc.save()
        print(f.flickr_oauth.requested_permissions)
    
        return HttpResponseRedirect(url)
        

    else:
        return render(request, "rvadmin/flickr_connect.html", vals)

@admin_page
def flickr_return(request):

    svc_id = request.GET.get("svc","0")
    
    svc = get_object_or_404(RVService,id=int(svc_id))

    token = request.GET["oauth_token"]
    
    f = FlickrAPI(settings.FLICKR_KEY, settings.FLICKR_SECRET, token=None, store_token=False)

    f.flickr_oauth.resource_owner_key = svc.auth_token
    f.flickr_oauth.resource_owner_secret = svc.auth_secret
    f.flickr_oauth.requested_permissions = "read"
    verifier = request.GET['oauth_verifier']

    print('Getting resource key')
    print ('Verifier is %s' % verifier)
    f.get_access_token(verifier)
    
    token = f.token_cache.token
    
    svc.username = token.username
    svc.userid = token.user_nsid
    svc.auth_token = token.token    
    svc.auth_secret = token.token_secret
    svc.save()
    
    
    return HttpResponseRedirect("/rvadmin/")  #todo return reverse
