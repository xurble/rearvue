from django.shortcuts import render_to_response,get_object_or_404
from django.template import RequestContext
from django.http import HttpResponseRedirect
from django.contrib.auth.decorators import login_required


# from instagram.client import InstagramAPI
# from flickr import FlickrAPI

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
def admin_index(request,domain_name):

    request.vals["services"] = RVService.objects.all()

    return render_to_response("rvadmin/index.html",request.vals,context_instance=RequestContext(request))


@login_required
@admin_page
def instagram_connect(request, domain_name, iid):

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
        return render_to_response("rvadmin/instagram_connect.html",vals,context_instance=RequestContext(request))
    
    
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
    
    return render_to_response("rvadmin/index.html",vals,context_instance=RequestContext(request))
    
@login_required
@admin_page
def flickr_connect(request, domain_name, iid):

    vals = {}
    
    if request.method == "POST":

        if iid == "new":
            svc = RVService(name='Flickr Service',type="Flickr",domain=request.domain)
            svc.save()
        else:
            svc = get_object_or_404(RVService,id=int(iid))

        redirect_uri = '{protocol}://{domain}/rvadmin/flickr_return/?svc={service}'.format(protocol = settings.DEFAULT_DOMAIN_PROTOCOL, domain=settings.DEFAULT_DOMAIN, service= svc.id) 
    
        f = FlickrAPI(api_key=settings.FLICKR_KEY, api_secret=settings.FLICKR_SECRET, callback_url=redirect_uri)


        auth_props = f.get_authentication_tokens()
        auth_url = auth_props['auth_url']

        #Store this token in a session or something for later use in the next step.
        oauth_token = auth_props['oauth_token']
        oauth_token_secret = auth_props['oauth_token_secret']
        
        svc.auth_token = oauth_token
        svc.auth_secret = oauth_token_secret
        svc.save()

        print('Connect with Flickr via: %s' % auth_url)
        
        return HttpResponseRedirect(auth_url)

    else:
        return render_to_response("rvadmin/instagram_connect.html",vals,context_instance=RequestContext(request))

@login_required
def flickr_return(request):

    svc_id = request.GET.get("svc","0")
    
    service = get_object_or_404(RVService,id=int(svc_id))

    f = FlickrAPI(api_key=settings.FLICKR_KEY, api_secret=settings.FLICKR_SECRET, oauth_token=service.auth_token, oauth_token_secret=service.auth_secret)
    
    authorized_tokens = f.get_auth_tokens(request.GET["oauth_verifier"])

    service.auth_token  = authorized_tokens['oauth_token']
    service.auth_secret = authorized_tokens['oauth_token_secret']    
    service.save()

    vals = {}
    
    return render_to_response("rvadmin/index.html",vals,context_instance=RequestContext(request))
