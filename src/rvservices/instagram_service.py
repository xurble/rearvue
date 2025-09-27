import time
import datetime
import os

from django.conf import settings
from django.utils import timezone

from rvsite.models import *
from rearvue import utils

from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.iguser import IGUser
from facebook_business.adobjects.igmedia import IGMedia

import requests
from PIL import Image

import ssl
ssl._create_default_https_context = ssl._create_unverified_context

import json


def fix_instagram_item(itemid):
    """Fix a specific Instagram item by re-fetching its data"""
    itemid = int(itemid)
    
    dbitem = RVItem.objects.get(id=itemid)
    
    if dbitem.service.type != "instagram":
        return (False, "Not an instagram item")
        
    data = json.loads(dbitem.raw_data)    
    media_id = data.get("id")

    if not media_id:
        return (False, "No media ID found in item data")

    try:
        # Initialize Facebook API with service's access token
        FacebookAdsApi.init(access_token=dbitem.service.auth_token)
        
        # Get the media object
        media = IGMedia(media_id)
        media_data = media.api_get(fields=[
            'id', 'caption', 'media_type', 'media_url', 'thumbnail_url', 
            'permalink', 'timestamp', 'children{media_url,media_type,thumbnail_url}'
        ])
        
        # Update the item with fresh data
        if media_data.get('caption'):
            dbitem.title = media_data['caption']
        else:
            dbitem.title = ""

        # Parse timestamp
        timestamp = datetime.datetime.fromisoformat(media_data['timestamp'].replace('Z', '+00:00'))
        dbitem.datetime_created = timestamp.replace(tzinfo=None)
        dbitem.date_created = dbitem.datetime_created.date()

        dbitem.remote_url = media_data.get('permalink', '')
        dbitem.raw_data = json.dumps(media_data)
        dbitem.save()
    
        # Re-mirror the media
        mirror_instagram(specific_item=dbitem)
    
        return (True, "Item updated successfully!")
        
    except Exception as e:
        return (False, f"Error updating item: {str(e)}")


def update_instagram():
    """Update Instagram services by fetching new posts"""
    ig_services = RVService.objects.filter(type="instagram").filter(live=True)
    
    for service in ig_services:
        if utils.hours_since(service.last_checked) < 12:
            print(f"Skipping {service} (too soon)")
            continue
            
        print(f"Updating {service}")

        try:
            # Initialize Facebook API with service's access token
            FacebookAdsApi.init(access_token=service.auth_token)
            
            # Get the Instagram user
            ig_user = IGUser(service.userid)
            
            # Get media from the user
            media_response = ig_user.api_get(
                'media',
                params={
                    'fields': 'id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,children{media_url,media_type,thumbnail_url}',
                    'limit': 50
                }
            )
            
            media_list = media_response.get('data', [])
            
            for media_data in media_list:
                media_id = media_data['id']
                
                # Check if we already have this item
                try:
                    item = RVItem.objects.get(service=service, item_id=media_id)
                    print(f"Item {media_id} already exists")
                except RVItem.DoesNotExist:
                    print(f"NEW item: {media_id}")
                    item = RVItem(
                        item_id=media_id,
                        service=service,
                        domain=service.domain
                    )
                
                # Update item data
                if media_data.get('caption'):
                    item.title = media_data['caption']
                    print(f"Caption: {item.title[:50]}...")

                # Parse timestamp
                timestamp = datetime.datetime.fromisoformat(
                    media_data['timestamp'].replace('Z', '+00:00')
                )
                item.datetime_created = timestamp.replace(tzinfo=None)
                item.date_created = item.datetime_created.date()

                item.remote_url = media_data.get('permalink', '')
                item.raw_data = json.dumps(media_data)
                item.save()
            
                # Mirror media if not already done
                if item.mirror_state == 0:
                    mirror_instagram(specific_item=item)
            
            # Update service last checked time
            service.last_checked = timezone.now()
            service.save()
            
        except Exception as e:
            print(f"Error updating service {service}: {str(e)}")
            continue


def mirror_instagram(specific_item=None):
    """Mirror Instagram media files to local storage"""
    if specific_item is not None:
        queue = [specific_item]
    else:
        queue = RVItem.objects.filter(
            mirror_state=0
        ).filter(
            service__type="instagram"
        ).filter(
            service__live=True
        )[:50]
    
    for item in queue:
        try:
            print(f"Mirroring item: {item.caption[:50]}...")
            data = json.loads(item.raw_data)
            
            # Clear existing media
            for m in item.rvmedia_set.all():
                m.delete()

            # Handle carousel albums vs single media
            if data.get("media_type") == "CAROUSEL_ALBUM" and data.get("children"):
                media_items = data["children"]["data"]
            else:
                # Single media item
                media_items = [data]

            for media_item in media_items:
                rvm = RVMedia()
                rvm.item = item
                rvm.save()

                media_url = media_item.get("media_url")
                if not media_url:
                    print("No media URL found")
                    rvm.delete()
                    continue

                ret = requests.get(media_url, timeout=30, verify=False)
                if not ret.ok:
                    print(f"Failed to download media: {ret.status_code}")
                    rvm.delete()
                    continue

                if media_item.get("media_type") == "IMAGE":
                    ext = "jpg"
                    rvm.media_type = 1

                    # Save original image
                    output_path = rvm.make_original_path(ext)
                    target_path = utils.make_full_path(output_path)
                    utils.make_folder(target_path)

                    with open(target_path, "wb") as fh:
                        fh.write(ret.content)

                    # Create thumbnail
                    img = Image.open(target_path)
                    ratio = float(img.size[0]) / float(img.size[1])
                    w = 300
                    h = int(300 / ratio)

                    print(f"Resizing thumbnail: {w}x{h}")
                    img = img.resize((w, h), Image.BICUBIC)

                    output_path = rvm.make_thumbnail_path(ext)
                    target_path = utils.make_full_path(output_path)
                    img.save(target_path)

                elif media_item.get("media_type") == "VIDEO":
                    ext = "mp4"
                    rvm.media_type = 2

                    # Save original video
                    output_path = rvm.make_original_path(ext)
                    target_path = utils.make_full_path(output_path)
                    utils.make_folder(target_path)

                    with open(target_path, "wb") as fh:
                        fh.write(ret.content)
                    
                    # Download thumbnail for video
                    thumbnail_url = media_item.get("thumbnail_url")
                    if thumbnail_url:
                        ret = requests.get(thumbnail_url, timeout=30, verify=False)
                        if ret.ok:
                            output_path = rvm.make_thumbnail_path("jpg")
                            target_path = utils.make_full_path(output_path)
                            utils.make_folder(target_path)

                            with open(target_path, "wb") as fh:
                                fh.write(ret.content)

                rvm.primary_media = rvm.original_media
                print(f"SAVING: {rvm.primary_media}")
                rvm.save()

            item.mirror_state = 1
            item.save()
            
        except Exception as ex:
            print(f"Error mirroring item {item.id}: {ex}")
            continue
    
