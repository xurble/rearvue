
from django.urls import path, include

from django.contrib import admin
admin.autodiscover()

urlpatterns = [
    # Examples:
    

    
    path('rvadmin/', include('rvadmin.urls')),

    path('admin/', admin.site.urls),

    
    path('', include('rvsite.urls')),
    
    
]
