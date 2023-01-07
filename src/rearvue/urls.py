
from django.urls import path, include

from django.contrib import admin
admin.autodiscover()

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Examples:
    

    
    path('rvadmin/', include('rvadmin.urls')),

    path('admin/', admin.site.urls),

    
    path('', include('rvsite.urls')),
 
    
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)  
    
print(urlpatterns) 
