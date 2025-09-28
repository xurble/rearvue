import time
import datetime
import os
import random
import json
from typing import Optional, List, Dict, Any

from django.conf import settings
from django.utils import timezone

from rvsite.models import *
from rearvue import utils

import instaloader
from instaloader import Profile, Post, Instaloader
import requests
from PIL import Image

import ssl
ssl._create_default_https_context = ssl._create_unverified_context


class InstagramInstaloaderService:
    """Instagram service using instaloader with burner account"""
    
    def __init__(self, service: RVService):
        self.service = service
        self.loader = None
        self.burner_username = service.auth_token  # Burner account username
        self.burner_password = service.auth_secret  # Burner account password
        self.target_username = service.username    # Target Instagram account to mirror
        
    def _initialize_loader(self) -> Instaloader:
        """Initialize instaloader with burner account credentials"""
        if self.loader is None:
            self.loader = instaloader.Instaloader(
                download_pictures=True,
                download_videos=True,
                download_video_thumbnails=True,
                download_geotags=False,
                download_comments=False,
                save_metadata=True,
                compress_json=False,
                max_connection_attempts=3,
                request_timeout=30
            )
            
            # Try to load existing session from extra_data
            try:
                if self.service.extra_data:
                    import tempfile
                    import io
                    # Create a temporary file for the session data
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.session') as temp_file:
                        temp_file.write(self.service.extra_data)
                        temp_file.flush()
                        self.loader.load_session_from_file(self.burner_username, temp_file.name)
                        print(f"Loaded existing session for {self.burner_username}")
                        # Clean up the temporary file
                        os.unlink(temp_file.name)
                else:
                    print(f"No existing session found, will login with credentials")
            except Exception as e:
                print(f"Failed to load session: {e}, will login with credentials")
                
        return self.loader
    
    def _login_if_needed(self) -> bool:
        """Login with burner account if not already logged in"""
        try:
            # Test if we're already logged in
            self.loader.context.get_json("https://www.instagram.com/")
            return True
        except:
            try:
                print(f"Logging in with burner account: {self.burner_username}")
                self.loader.login(self.burner_username, self.burner_password)
                
                # Save session to extra_data field
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False, suffix='.session') as temp_file:
                    self.loader.save_session_to_file(temp_file.name)
                    # Read the session data back
                    with open(temp_file.name, 'rb') as f:
                        self.service.extra_data = f.read()
                    self.service.save()
                    # Clean up the temporary file
                    os.unlink(temp_file.name)
                print("Session saved to database successfully")
                return True
            except Exception as e:
                print(f"Login failed: {str(e)}")
                return False
    
    def _add_random_delay(self, min_seconds: int = 120, max_seconds: int = 300):
        """Add random delay to avoid detection"""
        delay = random.randint(min_seconds, max_seconds)
        print(f"Waiting {delay} seconds to avoid rate limiting...")
        time.sleep(delay)
    
    def _convert_post_to_item_data(self, post: Post) -> Dict[str, Any]:
        """Convert instaloader Post object to our item data format"""
        return {
            'id': post.shortcode,
            'caption': post.caption or '',
            'media_type': 'CAROUSEL_ALBUM' if post.mediacount > 1 else 'IMAGE' if post.typename == 'GraphImage' else 'VIDEO',
            'media_url': post.url,
            'thumbnail_url': post.url if post.typename == 'GraphImage' else None,
            'permalink': f"https://www.instagram.com/p/{post.shortcode}/",
            'timestamp': post.date_utc.isoformat() + 'Z',
            'children': {
                'data': [
                    {
                        'media_url': post.url,
                        'media_type': 'IMAGE' if post.typename == 'GraphImage' else 'VIDEO',
                        'thumbnail_url': post.url if post.typename == 'GraphImage' else None
                    }
                ]
            } if post.mediacount == 1 else []
        }
    
    def update_instagram(self):
        """Update this specific Instagram service by fetching new posts using instaloader"""
        if utils.hours_since(self.service.last_checked) < 12:
            print(f"Skipping {self.service} (too soon)")
            return
            
        print(f"Updating {self.service} with instaloader")
        
        try:
            # Initialize and login
            self._initialize_loader()
            if not self._login_if_needed():
                print(f"Failed to login for service {self.service}")
                return
            
            # Get target profile
            if not self.target_username:
                print(f"No target username configured for service {self.service}")
                return
            
            print(f"Fetching posts for @{self.target_username}")
            profile = Profile.from_username(self.loader.context, self.target_username)
            
            # Add delay before fetching
            self._add_random_delay()
            
            # Get recent posts (limit to avoid rate limits)
            posts = list(profile.get_posts())
            print(f"Found {len(posts)} posts")
            
            # Process posts (limit to recent ones for safety)
            processed_count = 0
            max_posts = 5  # Limit to avoid rate limits
            
            for post in posts[:max_posts]:
                try:
                    post_data = self._convert_post_to_item_data(post)
                    post_id = post_data['id']
                    
                    # Check if we already have this item
                    try:
                        item = RVItem.objects.get(service=self.service, item_id=post_id)
                        print(f"Item {post_id} already exists")
                    except RVItem.DoesNotExist:
                        print(f"NEW item: {post_id}")
                        item = RVItem(
                            item_id=post_id,
                            service=self.service,
                            domain=self.service.domain
                        )
                    
                    # Update item data
                    if post_data.get('caption'):
                        item.title = post_data['caption']
                        print(f"Caption: {item.title[:50]}...")

                    # Parse timestamp
                    timestamp = datetime.datetime.fromisoformat(
                        post_data['timestamp'].replace('Z', '+00:00')
                    )
                    item.datetime_created = timestamp.replace(tzinfo=None)
                    item.date_created = item.datetime_created.date()

                    item.remote_url = post_data.get('permalink', '')
                    item.raw_data = json.dumps(post_data)
                    item.save()
                    
                    # Mirror media if not already done
                    if item.mirror_state == 0:
                        self.mirror_instagram(specific_item=item)
                    
                    processed_count += 1
                    
                    # Add delay between posts to avoid rate limiting
                    if processed_count < max_posts:
                        self._add_random_delay(60, 120)
                        
                except Exception as e:
                    print(f"Error processing post {post.shortcode}: {str(e)}")
                    continue
            
            # Update service last checked time
            self.service.last_checked = timezone.now()
            self.service.save()
            
            print(f"Successfully processed {processed_count} posts")
            
        except Exception as e:
            print(f"Error updating service {self.service}: {str(e)}")
    
    def mirror_instagram(self, specific_item=None):
        """Mirror Instagram media files to local storage using instaloader"""
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
                print(f"Mirroring item: {item.title[:50]}...")
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


# Convenience functions to maintain compatibility with existing code
def update_instagram():
    """Main entry point - update all Instagram services using instaloader"""
    ig_services = RVService.objects.filter(type="instagram").filter(live=True)
    
    for service in ig_services:
        try:
            instagram_service = InstagramInstaloaderService(service)
            instagram_service.update_instagram()
        except Exception as e:
            print(f"Error updating service {service}: {str(e)}")
            continue


def mirror_instagram(specific_item=None):
    """Main entry point - mirror Instagram media using instaloader"""
    if specific_item is not None:
        # Use the service from the specific item
        instagram_service = InstagramInstaloaderService(specific_item.service)
        instagram_service.mirror_instagram(specific_item)
    else:
        # Mirror all pending Instagram items
        ig_services = RVService.objects.filter(type="instagram").filter(live=True)
        
        for service in ig_services:
            try:
                instagram_service = InstagramInstaloaderService(service)
                instagram_service.mirror_instagram()
            except Exception as e:
                print(f"Error mirroring for service {service}: {str(e)}")
                continue


def fix_instagram_item(itemid):
    """Fix a specific Instagram item by re-fetching its data using instaloader"""
    itemid = int(itemid)
    
    dbitem = RVItem.objects.get(id=itemid)
    
    if dbitem.service.type != "instagram":
        return (False, "Not an instagram item")
    
    try:
        instagram_service = InstagramInstaloaderService(dbitem.service)
        instagram_service._initialize_loader()
        
        if not instagram_service._login_if_needed():
            return (False, "Failed to login with burner account")
        
        # Get the post data
        data = json.loads(dbitem.raw_data)
        post_shortcode = data.get("id")
        
        if not post_shortcode:
            return (False, "No post shortcode found in item data")
        
        # Fetch the post using instaloader
        post = Post.from_shortcode(instagram_service.loader.context, post_shortcode)
        post_data = instagram_service._convert_post_to_item_data(post)
        
        # Update the item with fresh data
        if post_data.get('caption'):
            dbitem.title = post_data['caption']
        else:
            dbitem.title = ""

        # Parse timestamp
        timestamp = datetime.datetime.fromisoformat(post_data['timestamp'].replace('Z', '+00:00'))
        dbitem.datetime_created = timestamp.replace(tzinfo=None)
        dbitem.date_created = dbitem.datetime_created.date()

        dbitem.remote_url = post_data.get('permalink', '')
        dbitem.raw_data = json.dumps(post_data)
        dbitem.save()
    
        # Re-mirror the media
        mirror_instagram(specific_item=dbitem)
    
        return (True, "Item updated successfully!")
        
    except Exception as e:
        return (False, f"Error updating item: {str(e)}")
