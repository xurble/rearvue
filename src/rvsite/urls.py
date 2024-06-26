from django.urls import path

from .views import (
    index,
    show_item,
    show_day,
    show_month,
    show_year,
)

urlpatterns = [
    # Examples:
    path('', index, name='index'),

    path('rv/<int:year>/<int:month>/<int:day>/<str:slug>/', show_item, name='show_item'),
    path('rv/<int:year>/<int:month>/<int:day>/', show_day, name='show_day'),
    path('rv/<int:year>/<int:month>/', show_month, name='show_month'),
    path('rv/<int:year>/', show_year, name='show_year'),
]


#    path('feed/(<str:key>)/edit/', editfeed), # legacy
