from django.contrib import admin

from .models import (
    RVDomain,
    RVService,
    RVItem,
    RVMedia,
    RVLink
)


class RVItemAdmin(admin.ModelAdmin):

    list_display = ("id", 'display_title', "service")

    search_fields = ('title',)


# Register your models here.
admin.site.register(RVDomain)
admin.site.register(RVService)
admin.site.register(RVItem, RVItemAdmin)
admin.site.register(RVMedia)
admin.site.register(RVLink)
