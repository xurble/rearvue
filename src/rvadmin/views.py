from django.shortcuts import render ,get_object_or_404
from django.template import RequestContext
from django.http import HttpResponseRedirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.iguser import IGUser
from flickrapi import FlickrAPI

import datetime
import requests
import json

from django.conf import settings

from rearvue.utils import admin_page, make_link

from rvsite.models import *

# Create your views here.

import rvservices.instagram_service
import rvservices.flickr_service
import rvservices.rss_service
import rvservices.twitter_service


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
    elif dbitem.service.type == "rss":
        (ok, msg) = rvservices.rss_service.fix_rss_item(iid)
    elif dbitem.service.type == "twitter":
        (ok, msg) = rvservices.twitter_service.fix_twitter_item(iid)
    else:
        (ok, msg) = (False, "Can't do this for {} yet.".format(dbitem.service.type))

    if ok:
        messages.info(request, "OK")
    else:
        messages.warning(request, msg)

    return HttpResponseRedirect(request.META["HTTP_REFERER"])


@login_required
@admin_page
def contextualize_item(request, iid):

    dbitem = RVItem.objects.get(id=iid)

    dbitem.rvlink_set.filter(is_context=True).delete()

    (ok, msg) = make_link(request.POST["link"], dbitem, is_context=True)

    if ok:
        messages.info(request, "OK")
    else:
        messages.warning(request, msg)

    return HttpResponseRedirect(request.META["HTTP_REFERER"])


@login_required
@admin_page
def instagram_connect(request, iid):

    vals = {}

    if request.method == "POST":

        if iid == "new":
            svc = RVService(name='Instagram Service',  type="instagram", domain=request.domain)
            svc.save()
        else:
            svc = get_object_or_404(RVService, id=int(iid))

        # For Instagram Graph API, we need a Facebook App and Page access token
        # The user needs to provide their Instagram Business/Creator account ID
        # and a Facebook Page access token
        
        # Store the service ID in session for the return
        request.session['instagram_service_id'] = svc.id
        
        # Redirect to a form where they can enter their Instagram account details
        return HttpResponseRedirect("/rvadmin/instagram_setup/")

    else:
        return render(request,"rvadmin/instagram_connect.html",vals)


@login_required
@admin_page
def instagram_setup(request):
    """Setup Instagram connection with Facebook Graph API"""
    
    if request.method == "POST":
        service_id = request.session.get('instagram_service_id')
        if not service_id:
            messages.error(request, "No service found in session")
            return HttpResponseRedirect("/rvadmin/")
            
        service = get_object_or_404(RVService, id=service_id)
        
        # Get form data
        instagram_account_id = request.POST.get('instagram_account_id')
        facebook_access_token = request.POST.get('facebook_access_token')
        
        if not instagram_account_id or not facebook_access_token:
            messages.error(request, "Please provide both Instagram Account ID and Facebook Access Token")
            return render(request, "rvadmin/instagram_setup.html", {})
        
        try:
            # Initialize Facebook API
            FacebookAdsApi.init(access_token=facebook_access_token)
            
            # Test the connection by getting the Instagram user info
            ig_user = IGUser(instagram_account_id)
            user_data = ig_user.api_get(fields=['id', 'username'])
            
            # Update service with Instagram account details
            service.userid = user_data['id']
            service.username = user_data['username']
            service.auth_token = facebook_access_token
            service.save()
            
            messages.success(request, f"Successfully connected to Instagram account: @{user_data['username']}")
            
            # Clear session
            del request.session['instagram_service_id']
            
            return HttpResponseRedirect("/rvadmin/")
            
        except Exception as e:
            messages.error(request, f"Error connecting to Instagram: {str(e)}")
            return render(request, "rvadmin/instagram_setup.html", {})
    
    return render(request, "rvadmin/instagram_setup.html", {})

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

@login_required
@admin_page
def twitter_connect(request, iid):

    svc = get_object_or_404(RVService,id=int(iid))

    vals = {}

    if request.method == "POST":
        if request.POST["action"] == "archive":
            js = request.FILES["archive"]
            data = js.read().decode('utf-8')

            data = data[len("window.YTD.tweets.part0 = "):]
            rvservices.twitter_service.import_archive(svc, json.loads(data))


        return HttpResponseRedirect("/rvadmin/")  #todo return reverse
    else:
        return render(request,"rvadmin/twitter_connect.html",vals)
