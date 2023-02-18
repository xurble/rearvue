from django.shortcuts import render
from django.shortcuts import render,get_object_or_404
from django.db.models import Q

from dateutil.relativedelta import relativedelta

import random
import datetime
import calendar

from rearvue.utils import page
from rearvue.utils import sample_of
from rearvue.utils import MONTH_LIST



from .models import *

# Create your views here.

@page
def index(request):

    request.vals["recent"] = list(RVItem.objects.filter(Q(domain=request.domain) & Q(public=True)).order_by("-datetime_created")[:12])

    return render(request, "rvsite/index.html",request.vals)

    

@page
def summary(request):

    item_list = list(RVItem.objects.filter(Q(domain=request.domain) & Q(public=True)).order_by("-datetime_created")[:12])
    
    request.vals["recent"] = sample_of(item_list, 6)
    
    last_year = datetime.datetime.now() - relativedelta(years=1)
    
    item_list  = list(RVItem.objects.filter(Q(domain=request.domain) & Q(datetime_created__lt=last_year) & Q(public=True) & Q(mirror_state__gte=1)).order_by("-datetime_created")[:12])
    item_list += list(RVItem.objects.filter(Q(domain=request.domain) & Q(datetime_created__gt=last_year) & Q(public=True) & Q(mirror_state__gte=1)).order_by("datetime_created")[:6])
    
    request.vals["last_year"] = sample_of(item_list, 6)
    
    five_years = datetime.datetime.now() - relativedelta(years=5)
    
    item_list  = list(RVItem.objects.filter(Q(domain=request.domain) & Q(datetime_created__lt=five_years) & Q(public=True) & Q(mirror_state__gte=1)).order_by("-datetime_created")[:12])
    item_list += list(RVItem.objects.filter(Q(domain=request.domain) & Q(datetime_created__gt=five_years) & Q(public=True) & Q(mirror_state__gte=1)).order_by("datetime_created")[:6])
    
    request.vals["five_years"] = sample_of(item_list, 6)

    return render(request, "rvsite/summary.html",request.vals)
    
@page
def show_year(request, year):

    start_date = datetime.date(year=int(year),month=1,day=1)
    end_date   = datetime.date(year=int(year),month=12,day=31)
    
    items = RVItem.objects.filter(Q(domain=request.domain)&Q(date_created__gte=start_date)&Q(date_created__lte=end_date)&Q(mirror_state__gte=1)& Q(public=True)).order_by("?")
    
    if request.user != request.domain.owner:
        items.filter(public=True)
        
    months = []
    for month  in MONTH_LIST:
        mitems = []
        months.append([month, mitems, 0])
    
    for i in items:
        mlist = months[i.date_created.month][1] 
        if len(mlist) < 6 and i.rvmedia_set.count() > 0:
            mlist.append(i)
        months[i.date_created.month][2] = months[i.date_created.month][2] + 1 
            
    
    request.vals["months"] = months
    request.vals["year"] = year

    return render(request, "rvsite/year.html",request.vals)

@page
def show_month(request, year, month):

    
    start_date = datetime.date(year=int(year),month=int(month),day=1)
    end_date   = start_date + relativedelta(months=1)

    thecal = calendar.monthcalendar(int(year),int(month))
    for week in thecal:
        for day in range(len(week)):
            week[day]= [week[day],0]
                
    
    items = list(RVItem.objects.filter(Q(date_created__gte=start_date)&Q(date_created__lt=end_date)&Q(mirror_state__gte=1)& Q(public=True)).order_by("datetime_created"))
        
    request.vals["month_name"] = MONTH_LIST[int(month)]
    
    request.vals["month"] = month
    request.vals["year"]  = year
    request.vals["items"] = items
        
        
    return render(request, "rvsite/month.html",request.vals)



@page
def show_day(request, year, month,day):

    request.vals["month_name"] = MONTH_LIST[int(month)]
    
    request.vals["month"] = month
    request.vals["year"]  = year
    request.vals["day"] = day
    
    
    the_date = datetime.date(year=int(year),month=int(month),day=int(day))
    
    
    items = RVItem.objects.filter(date_created=the_date).filter(mirror_state=1).filter(public=True).order_by("datetime_created")
 
    other_items = RVItem.objects.filter(date_created__day=int(day)).filter(date_created__month=int(month)).filter(public=True).filter(mirror_state__gte=1).exclude(date_created__year=int(year))
    
    
    request.vals["items"] = items
    request.vals["other_items"] = other_items

    return render(request, "rvsite/day.html",request.vals)    

    
@page
def show_item(request, year, month, day, iid):

    request.vals["month_name"] = MONTH_LIST[int(month)]
    
    request.vals["month"] = month
    request.vals["year"]  = year
    request.vals["day"] = day

    request.vals["item"] = get_object_or_404(RVItem,id=int(iid))  

    request.vals["other_items"] = RVItem.objects.filter(date_created__day=int(day)).filter(date_created__month=int(month)).filter(public=True).filter(mirror_state__gte=1).exclude(date_created__year=int(year))
  
    
    return render(request, "rvsite/item.html",request.vals)    
