from django.shortcuts import render, get_object_or_404
from django.http import HttpResponseRedirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from flickrapi import FlickrAPI

import json
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from rearvue.utils import admin_page, make_link

from rvsite.models import *

# Create your views here.

import rvservices.instagram_graph_service
import rvservices.instagram_oauth
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


def _instagram_oauth_redirect_uri(request):
    """Must match a redirect URI registered on the Meta app (exact string)."""
    fixed = getattr(settings, "INSTAGRAM_REDIRECT_URI", None)
    if fixed:
        return fixed.rstrip("/")
    dom = request.domain
    if dom.alt_domain:
        base = dom.alt_domain.rstrip("/")
        if not base.startswith(("http://", "https://")):
            base = f"{settings.DEFAULT_DOMAIN_PROTOCOL}://{base.lstrip('/')}"
    else:
        base = f"{settings.DEFAULT_DOMAIN_PROTOCOL}://{dom.name}"
    return f"{base}/rvadmin/instagram_oauth_return/"


@login_required
@admin_page
def admin_index(request):

    request.vals["services"] = RVService.objects.filter(domain=request.domain)
    return render(request, "rvadmin/index.html", request.vals)


@login_required
@admin_page
@require_POST
def fix_item(request, iid):

    dbitem = get_object_or_404(RVItem, id=iid, domain=request.domain)

    if dbitem.service.type == "instagram":
        (ok, msg) = rvservices.instagram_graph_service.fix_instagram_item(iid)
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

        request.session['instagram_service_id'] = svc.id
        return HttpResponseRedirect(reverse("instagram_oauth_start"))

    else:
        return render(request,"rvadmin/instagram_connect.html",vals)


@login_required
@admin_page
def instagram_oauth_start(request):
    """Redirect to Instagram authorization (Instagram API with Instagram Login)."""
    service_id = request.session.get("instagram_service_id")
    if not service_id:
        messages.error(request, "No Instagram service in session. Start from Connect again.")
        return HttpResponseRedirect(reverse("admin_index"))

    get_object_or_404(RVService, id=service_id, domain=request.domain, type="instagram")

    state = secrets.token_urlsafe(32)
    request.session["instagram_oauth_state"] = state

    redirect_uri = _instagram_oauth_redirect_uri(request)
    auth_url = rvservices.instagram_oauth.build_authorize_url(
        redirect_uri=redirect_uri,
        state=state,
    )
    return HttpResponseRedirect(auth_url)


@login_required
@admin_page
def instagram_oauth_return(request):
    """OAuth callback: exchange code, store long-lived token on RVService."""
    if request.GET.get("error"):
        messages.error(
            request,
            request.GET.get("error_description", request.GET.get("error")),
        )
        return HttpResponseRedirect(reverse("admin_index"))

    code = request.GET.get("code")
    state = request.GET.get("state")
    if not code or state != request.session.get("instagram_oauth_state"):
        messages.error(request, "Invalid Instagram OAuth response.")
        return HttpResponseRedirect(reverse("admin_index"))

    service_id = request.session.get("instagram_service_id")
    if not service_id:
        messages.error(request, "Session expired. Connect Instagram again.")
        return HttpResponseRedirect(reverse("admin_index"))

    service = get_object_or_404(
        RVService, id=service_id, domain=request.domain, type="instagram"
    )
    redirect_uri = _instagram_oauth_redirect_uri(request)

    try:
        row = rvservices.instagram_oauth.exchange_code_for_short_lived_token(
            code=code, redirect_uri=redirect_uri
        )
        short_token = row["access_token"]
        long_out = rvservices.instagram_graph_service.exchange_short_lived_for_long_lived(
            short_token
        )
        long_token = long_out["access_token"]
        expires_in = int(long_out.get("expires_in", 0))

        me = rvservices.instagram_graph_service.fetch_me(long_token)
        service.userid = str(me["id"])
        service.username = me.get("username", "")[:128]
        service.auth_token = long_token
        now = timezone.now()
        service.instagram_last_token_refresh_at = now
        if expires_in:
            service.instagram_token_expires_at = now + timedelta(seconds=expires_in)
        else:
            service.instagram_token_expires_at = None
        service.save()

        for key in ("instagram_oauth_state", "instagram_service_id"):
            request.session.pop(key, None)

        messages.success(
            request,
            f"Connected Instagram @{service.username or service.userid}. Run content update to import media.",
        )
        return HttpResponseRedirect(reverse("admin_index"))
    except Exception as e:
        messages.error(request, f"Instagram OAuth failed: {e}")
        return HttpResponseRedirect(reverse("admin_index"))

@login_required
@admin_page
def flickr_connect(request, iid):

    vals = {}

    if request.method == "POST":

        if iid == "new":
            svc = RVService(name='Flickr Service', type="flickr", domain=request.domain)
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
