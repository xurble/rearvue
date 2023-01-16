from django.contrib import admin

from .models import *
# Register your models here.
admin.site.register(RVDomain)
admin.site.register(RVService)
admin.site.register(RVItem)
admin.site.register(RVMedia)
admin.site.register(RVLink)