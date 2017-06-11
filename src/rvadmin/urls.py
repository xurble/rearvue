from django.conf.urls import include, url

from views import *

urlpatterns = [
    # Examples:
    
    url(r'^(?P<domain_name>.*)/instagram_connect/(?P<iid>.*)/',instagram_connect, name='instagram_connect'),
    url(r'^instagram_return/',instagram_return, name='instagram_return'),

    url(r'^(?P<domain_name>.*)/flickr_connect/(?P<iid>.*)/',flickr_connect, name='flickr_connect'),
    url(r'^flickr_return/',flickr_return, name='flickr_return'),

    url(r'^(?P<domain_name>.*)/$', admin_index, name='admin_index'),

]
