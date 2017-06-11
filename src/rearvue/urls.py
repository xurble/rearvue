from django.conf.urls import include, url

from django.contrib import admin
admin.autodiscover()

urlpatterns = [
    # Examples:
    
    url(r'^rvadmin/', include('rvadmin.urls')),

    url(r'^admin/', include(admin.site.urls)),

    url(r'^', include('rvsite.urls')),
    
    
]
