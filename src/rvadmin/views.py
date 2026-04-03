from django.shortcuts import render, get_object_or_404
from django.http import HttpResponseRedirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.iguser import IGUser
from flickrapi import FlickrAPI

import json

from django.conf import settings

from rearvue.utils import admin_page, make_link

from rvsite.models import *

# Create your views here.

import rvservices.instagram_service
import rvservices.flickr_service
import rvservices.rss_service
import rvservices.twitter_service


def _safe_admin_redirect(request, fallback=None):
    if fallback is None:
        fallback = reverse("admin_index")
    ref = request.META.get("HTTP_REFERER")
    if ref and url_has_allowed_host_and_scheme(
        url=ref,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return HttpResponseRedirect(ref)
    return HttpResponseRedirect(fallback)


@login_required
@admin_page
def admin_index(request):

    request.vals["services"] = RVService.objects.filter(domain=request.domain)
    return render(request, "rvadmin/index.html", request.vals)


@login_required
@admin_page
def fix_item(request, iid):

    dbitem = get_object_or_404(RVItem, id=iid, domain=request.domain)

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

    return _safe_admin_redirect(request)


@login_required
@admin_page
@require_POST
def contextualize_item(request, iid):

    dbitem = get_object_or_404(RVItem, id=iid, domain=request.domain)

    dbitem.rvlink_set.filter(is_context=True).delete()

    link_url = request.POST.get("link", "").strip()
    if not link_url:
        messages.warning(request, "Link is required")
        return _safe_admin_redirect(request)

    (ok, msg) = make_link(link_url, dbitem, is_context=True)

    if ok:
        messages.info(request, "OK")
    else:
        messages.warning(request, msg)

    return _safe_admin_redirect(request)


@login_required
@admin_page
def instagram_connect(request, iid):

    vals = {}

    if request.method == "POST":

        if iid == "new":
            svc = RVService(name='Instagram Service',  type="instagram", domain=request.domain)
            svc.save()
        else:
            svc = get_object_or_404(RVService, id=int(iid), domain=request.domain)

        # For Instagram Graph API, we need a Facebook App and Page access token
        # The user needs to provide their Instagram Business/Creator account ID
        # and a Facebook Page access token

        # Store the service ID in session for the return
        request.session['instagram_service_id'] = svc.id

        # Redirect to a form where they can enter their Instagram account details
        return HttpResponseRedirect(reverse("instagram_setup"))

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
            return HttpResponseRedirect(reverse("admin_index"))

        service = get_object_or_404(RVService, id=service_id, domain=request.domain)

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

            return HttpResponseRedirect(reverse("admin_index"))

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
            svc = get_object_or_404(RVService,id=int(iid), domain=request.domain)

        if request.domain.alt_domain != '' :
            redirect_uri = '{domain}/rvadmin/flickr_return/?svc={service}'.format(domain=request.domain.alt_domain, service= svc.id)
        else:
            redirect_uri = '{protocol}://{domain}/rvadmin/flickr_return/?svc={service}'.format(protocol = settings.DEFAULT_DOMAIN_PROTOCOL, domain=request.domain.name, service= svc.id)

        f = FlickrAPI(settings.FLICKR_KEY, settings.FLICKR_SECRET, token=None, store_token=False)

        f.get_request_token(oauth_callback=redirect_uri)

        url = f.auth_url(perms='read')

        svc.auth_token = f.flickr_oauth.resource_owner_key
        svc.auth_secret = f.flickr_oauth.resource_owner_secret

        svc.save()

        return HttpResponseRedirect(url)


    else:
        return render(request, "rvadmin/flickr_connect.html", vals)

@login_required
@admin_page
def flickr_return(request):

    svc_id = request.GET.get("svc","0")

    svc = get_object_or_404(RVService,id=int(svc_id), domain=request.domain)

    token = request.GET["oauth_token"]

    f = FlickrAPI(settings.FLICKR_KEY, settings.FLICKR_SECRET, token=None, store_token=False)

    f.flickr_oauth.resource_owner_key = svc.auth_token
    f.flickr_oauth.resource_owner_secret = svc.auth_secret
    f.flickr_oauth.requested_permissions = "read"
    verifier = request.GET['oauth_verifier']

    f.get_access_token(verifier)

    token = f.token_cache.token

    svc.username = token.username
    svc.userid = token.user_nsid
    svc.auth_token = token.token
    svc.auth_secret = token.token_secret
    svc.save()

    return HttpResponseRedirect(reverse("admin_index"))

@login_required
@admin_page
def twitter_connect(request, iid):

    svc = get_object_or_404(RVService,id=int(iid), domain=request.domain)

    vals = {}

    if request.method == "POST":
        if request.POST["action"] == "archive":
            js = request.FILES["archive"]
            data = js.read().decode('utf-8')

            data = data[len("window.YTD.tweets.part0 = "):]
            rvservices.twitter_service.import_archive(svc, json.loads(data))


        return HttpResponseRedirect(reverse("admin_index"))
    else:
        return render(request,"rvadmin/twitter_connect.html",vals)
