from django.urls import path


from .views import *

urlpatterns = [
    # Examples:
    
    path('instagram_connect/<int:iid>/', instagram_connect, name='instagram_connect'),
    path('instagram_return/',instagram_return, name='instagram_return'),

    path('flickr_connect/<int:iid>/', flickr_connect, name='flickr_connect'),
    path('flickr_return/',flickr_return, name='flickr_return'),

    path('twitter_connect/<int:iid>/', twitter_connect, name='twitter_connect'),

    path('', admin_index, name='admin_index'),
    
    path('fix_item/(<int:iid>/', fix_item, name='fix_item'),
    path('contextualize_item/<int:iid>/', contextualize_item, name='contextualize_item'),


]
