from django.core.management.base import BaseCommand
from django.db.models import Max, Min
from django.utils import timezone
from django.template.loader import render_to_string

from rvsite.models import RVItem, RVDomain
from rearvue.utils import make_full_path


class Command(BaseCommand):
    help = 'Update content from all services (RSS, Twitter, Instagram, Flickr) and generate RSS feeds'

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-rss',
            action='store_true',
            help='Skip RSS updates',
        )
        parser.add_argument(
            '--skip-twitter',
            action='store_true',
            help='Skip Twitter updates',
        )
        parser.add_argument(
            '--skip-instagram',
            action='store_true',
            help='Skip Instagram updates',
        )
        parser.add_argument(
            '--skip-flickr',
            action='store_true',
            help='Skip Flickr updates',
        )
        parser.add_argument(
            '--skip-cleanup',
            action='store_true',
            help='Skip domain cleanup and RSS generation',
        )

    def handle(self, *args, **options):
        # Update RSS
        if not options['skip_rss']:
            self.stdout.write("Updating RSS")
            try:
                from rvservices.rss_service import update_rss, mirror_rss, find_rss_links
                update_rss()
                self.stdout.write(self.style.SUCCESS("RSS update completed"))
            except Exception as ex:
                self.stdout.write(self.style.ERROR(f"RSS update failed: {ex}"))

            try:
                mirror_rss()
                find_rss_links()
                self.stdout.write(self.style.SUCCESS("RSS mirroring and link finding completed"))
            except Exception as ex:
                self.stdout.write(self.style.ERROR(f"RSS mirroring failed: {ex}"))

        # Update Twitter
        if not options['skip_twitter']:
            self.stdout.write("Updating Twitter")
            try:
                from rvservices.twitter_service import find_twitter_links, mirror_twitter
                mirror_twitter()
                find_twitter_links()
                self.stdout.write(self.style.SUCCESS("Twitter update completed"))
            except Exception as ex:
                self.stdout.write(self.style.ERROR(f"Twitter update failed: {ex}"))

        # Update Instagram
        if not options['skip_instagram']:
            self.stdout.write("Updating Instagram")
            try:
                from rvservices.instagram_service import update_instagram
                update_instagram()
                self.stdout.write(self.style.SUCCESS("Instagram update completed"))
            except Exception as ex:
                self.stdout.write(self.style.ERROR(f"Instagram update failed: {ex}"))

        # Update Flickr
        if not options['skip_flickr']:
            self.stdout.write("Updating Flickr")
            try:
                from rvservices.flickr_service import update_flickr, mirror_flickr
                update_flickr()
                self.stdout.write(self.style.SUCCESS("Flickr update completed"))
            except Exception as ex:
                self.stdout.write(self.style.ERROR(f"Flickr update failed: {ex}"))

            self.stdout.write("Mirroring Flickr")
            try:
                mirror_flickr()
                self.stdout.write(self.style.SUCCESS("Flickr mirroring completed"))
            except Exception as ex:
                self.stdout.write(self.style.ERROR(f"Flickr mirroring failed: {ex}"))

        # Clean up domains and generate RSS feeds
        if not options['skip_cleanup']:
            self.stdout.write("Cleaning up domains")
            for domain in RVDomain.objects.all():
                self.stdout.write(f"Processing domain: {domain}")

                try:
                    max_year = RVItem.objects.filter(service__domain=domain).aggregate(Max('datetime_created'))["datetime_created__max"]
                    min_year = RVItem.objects.filter(service__domain=domain).aggregate(Min('datetime_created'))["datetime_created__min"]

                    if max_year and min_year:
                        domain.max_year = max_year.year
                        domain.min_year = min_year.year
                        domain.last_updated = timezone.now()
                        domain.save()
                        self.stdout.write(self.style.SUCCESS(f"Updated domain {domain}: {min_year.year}-{max_year.year}"))
                    else:
                        self.stdout.write(self.style.WARNING(f"No items found for domain {domain}"))
                except Exception as ex:
                    self.stdout.write(self.style.ERROR(f"Failed to update domain {domain}: {ex}"))

                # Generate RSS feed for domain
                try:
                    rss_path = make_full_path(f"media/{domain.name}/rss.xml")
                    with open(rss_path, "w") as out:
                        vals = {
                            "domain": domain,
                            "items": RVItem.objects.filter(service__domain=domain).filter(public=True).order_by("-datetime_created")[:25]
                        }
                        out.write(render_to_string("rss.xml", vals))
                    self.stdout.write(self.style.SUCCESS(f"Generated RSS feed for {domain}"))
                except Exception as ex:
                    self.stdout.write(self.style.ERROR(f"Failed to generate RSS feed for {domain}: {ex}"))

        self.stdout.write(self.style.SUCCESS("Content update completed"))
