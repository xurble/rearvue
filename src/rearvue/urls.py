
from django.contrib import admin
from django.urls import include, path

admin.autodiscover()

from django.conf import settings
from django.conf.urls.static import static

from rvmcp import views as rvmcp_views

urlpatterns = [
    # Examples:

    path('mcp-download/media/<int:media_id>/', rvmcp_views.download_media, name='mcp_download_media'),
    path('mcp-download/jobs/<int:job_id>/', rvmcp_views.download_job_artifact, name='mcp_download_job_artifact'),

    path('rvadmin/', include('rvadmin.urls')),

    path('admin/', admin.site.urls),


    path('', include('rvsite.urls')),


]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
