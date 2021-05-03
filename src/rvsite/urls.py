from django.conf.urls import include, url

from .views import *

urlpatterns = [
    # Examples:
    url(r'^$', index, name='index'),

    url(r'^rv/(?P<year>.*)/(?P<month>.*)/(?P<day>.*)/(?P<iid>.*)/$',show_item, name='show_item'),
    url(r'^rv/(?P<year>.*)/(?P<month>.*)/(?P<day>.*)/$',show_day, name='show_day'),
    url(r'^rv/(?P<year>.*)/(?P<month>.*)/$',show_month, name='show_month'),
    url(r'^rv/(?P<year>.*)/$',show_year, name='show_year'),
]
