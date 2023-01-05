from django.shortcuts import render ,get_object_or_404
from django.template import RequestContext
from django.http import HttpResponseRedirect
from django.contrib.auth.decorators import login_required


# from instagram.client import InstagramAPI
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
def instagram_connect(request, iid):

    vals = {}
    
    if request.method == "POST":

        if iid == "new":
            svc = RVService(name='Instagram Service',type="Instagram",domain=request.domain)
            svc.save()
        else:
            svc = get_object_or_404(RVService,id=int(iid))

        redirect_uri = '{protocol}://{domain}/rvadmin/instagram_return/?svc={service}'.format(protocol = settings.DEFAULT_DOMAIN_PROTOCOL, domain=settings.DEFAULT_DOMAIN, service= svc.id) 
    
        api = InstagramAPI(client_id=settings.INSTAGRAM_CLENT_ID, client_secret=settings.INSTAGRAM_CLIENT_SECRET, redirect_uri=redirect_uri)
        redirect_url = api.get_authorize_login_url(scope = ["basic"])
        
        return HttpResponseRedirect(redirect_url)

    else:
        return render(request,"rvadmin/instagram_connect.html",vals)
    
    
@login_required
def instagram_return(request):

    code = request.GET.get("code","")
    svc_id = request.GET.get("svc","0")
    
    service = get_object_or_404(RVService,id=int(svc_id))
    
    post_vals = {
        "client_id" : settings.INSTAGRAM_CLENT_ID,
        "client_secret" : settings.INSTAGRAM_CLIENT_SECRET,
        "grant_type" : "authorization_code",
        "redirect_uri": '{protocol}://{domain}/rvadmin/instagram_return/?svc={service}'.format(protocol = settings.DEFAULT_DOMAIN_PROTOCOL, domain=settings.DEFAULT_DOMAIN, service= svc_id) ,
        "code": code
    }
    
    ret = requests.post("https://api.instagram.com/oauth/access_token", post_vals) 
    ret_data = json.loads(ret.content)
    
    service.username    = ret_data["user"]["username"]
    service.userid      = ret_data["user"]["id"]
    service.profile_pic = ret_data["user"]["profile_picture"]
    service.auth_token  = ret_data["access_token"]
    
    service.save() 
    
    

    vals = {}
    
    return render(request,"rvadmin/index.html",vals)
    
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
    
    
    return render(request, "rvadmin/index.html", request.vals)
