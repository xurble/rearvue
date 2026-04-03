from django.shortcuts import render, get_object_or_404
from django.db.models import Q
from django.http import Http404
from django.http.response import HttpResponsePermanentRedirect
from django.urls import reverse

from dateutil.relativedelta import relativedelta

import datetime

from rearvue.utils import page
from rearvue.utils import sample_of
from rearvue.utils import MONTH_LIST

from .models import RVItem


@page
def index(request):

    request.vals["recent"] = list(RVItem.objects.filter(Q(domain=request.domain) & Q(public=True)).order_by("-datetime_created")[:12])

    return render(request, "rvsite/index.html", request.vals)


@page
def summary(request):

    item_list = list(RVItem.objects.filter(Q(domain=request.domain) & Q(public=True)).order_by("-datetime_created")[:12])

    request.vals["recent"] = sample_of(item_list, 6)

    last_year = datetime.datetime.now() - relativedelta(years=1)

    item_list = list(RVItem.objects.filter(Q(domain=request.domain) & Q(datetime_created__lt=last_year) & Q(public=True) & Q(mirror_state__gte=1)).order_by("-datetime_created")[:12])
    item_list += list(RVItem.objects.filter(Q(domain=request.domain) & Q(datetime_created__gt=last_year) & Q(public=True) & Q(mirror_state__gte=1)).order_by("datetime_created")[:6])

    request.vals["last_year"] = sample_of(item_list, 6)

    five_years = datetime.datetime.now() - relativedelta(years=5)

    item_list = list(RVItem.objects.filter(Q(domain=request.domain) & Q(datetime_created__lt=five_years) & Q(public=True) & Q(mirror_state__gte=1)).order_by("-datetime_created")[:12])
    item_list += list(RVItem.objects.filter(Q(domain=request.domain) & Q(datetime_created__gt=five_years) & Q(public=True) & Q(mirror_state__gte=1)).order_by("datetime_created")[:6])

    request.vals["five_years"] = sample_of(item_list, 6)

    return render(request, "rvsite/summary.html", request.vals)


@page
def show_year(request, year):

    start_date = datetime.date(year=int(year), month=1, day=1)
    end_date = datetime.date(year=int(year), month=12, day=31)

    q = (
        Q(domain=request.domain)
        & Q(date_created__gte=start_date)
        & Q(date_created__lte=end_date)
        & Q(mirror_state__gte=1)
    )
    if request.user != request.domain.owner:
        q &= Q(public=True)

    items = RVItem.objects.filter(q).order_by("?")

    months = []
    for month in MONTH_LIST:
        mitems = []
        months.append([month, mitems, 0])

    for i in items:
        mlist = months[i.date_created.month][1]
        if len(mlist) < 6 and i.rvmedia_set.count() > 0 and i.thumbnail != "":
            mlist.append(i)
        months[i.date_created.month][2] = months[i.date_created.month][2] + 1

    request.vals["months"] = months
    request.vals["year"] = year

    return render(request, "rvsite/year.html", request.vals)


@page
def show_month(request, year, month):

    start_date = datetime.date(year=int(year), month=int(month), day=1)
    end_date = start_date + relativedelta(months=1)

    q = (
        Q(domain=request.domain)
        & Q(date_created__gte=start_date)
        & Q(date_created__lt=end_date)
        & Q(mirror_state__gte=1)
    )
    if request.user != request.domain.owner:
        q &= Q(public=True)

    items = list(RVItem.objects.filter(q).order_by("datetime_created"))

    request.vals["month_name"] = MONTH_LIST[int(month)]

    request.vals["month"] = month
    request.vals["year"] = year
    request.vals["items"] = items
    request.vals["next"] = start_date + relativedelta(months=1)
    request.vals["prev"] = start_date - relativedelta(months=1)

    return render(request, "rvsite/month.html", request.vals)


@page
def show_day(request, year, month, day):

    request.vals["month_name"] = MONTH_LIST[int(month)]

    request.vals["month"] = month
    request.vals["year"] = year
    request.vals["day"] = day

    the_date = datetime.date(year=int(year), month=int(month), day=int(day))

    q = (
        Q(domain=request.domain)
        & Q(date_created=the_date)
        & Q(mirror_state=1)
    )
    if request.user != request.domain.owner:
        q &= Q(public=True)

    items = RVItem.objects.filter(q).order_by("datetime_created")

    other_items_rs = (
        RVItem.objects.filter(
            domain=request.domain,
            date_created__day=int(day),
            date_created__month=int(month),
            mirror_state__gte=1,
        )
        .exclude(date_created__year=int(year))
    )
    if request.user != request.domain.owner:
        other_items_rs = other_items_rs.filter(public=True)

    other_items = [o for o in other_items_rs if o.thumbnail != ""]

    request.vals["items"] = items
    request.vals["other_items"] = other_items

    return render(request, "rvsite/day.html", request.vals)


@page
def show_item(request, year, month, day, slug):

    request.vals["month_name"] = MONTH_LIST[int(month)]

    request.vals["month"] = month
    request.vals["year"] = year
    request.vals["day"] = day

    try:
        item = RVItem.objects.get(domain=request.domain, slug=slug)
    except RVItem.DoesNotExist:
        legacy_slug = f"post-{slug}"
        item = get_object_or_404(RVItem, domain=request.domain, slug=legacy_slug)
        if slug != item.slug:
            return HttpResponsePermanentRedirect(
                reverse("show_item", args=[year, month, day, item.slug])
            )

    if not item.public and request.user != request.domain.owner:
        raise Http404()

    y, m, d = int(year), int(month), int(day)
    if item.date_created.year != y or item.date_created.month != m or item.date_created.day != d:
        return HttpResponsePermanentRedirect(
            reverse(
                "show_item",
                args=[
                    item.date_created.year,
                    item.date_created.month,
                    item.date_created.day,
                    item.get_slug(),
                ],
            )
        )

    request.vals["item"] = item

    other_items_rs = (
        RVItem.objects.filter(
            domain=request.domain,
            date_created__day=int(day),
            date_created__month=int(month),
            mirror_state__gte=1,
        )
        .exclude(date_created__year=int(year))
    )
    if request.user != request.domain.owner:
        other_items_rs = other_items_rs.filter(public=True)

    other_items = [o for o in other_items_rs if o.thumbnail != ""]

    request.vals["other_items"] = other_items

    return render(request, "rvsite/item.html", request.vals)
