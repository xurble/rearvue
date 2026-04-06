import os

from django.core.management.base import BaseCommand, CommandError

from rvsite.models import RVDomain, RVItem, RVMedia, RVService
from rearvue import utils


class Command(BaseCommand):
    help = (
        "Delete all Instagram services, their items, mirrored media files, and clear "
        "domain poster_image when it points at an Instagram item. "
        "Requires --confirm."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required. Confirms destructive deletion.",
        )

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("Refusing to run without --confirm.")

        ig_services = list(RVService.objects.filter(type="instagram"))
        if not ig_services:
            self.stdout.write(self.style.WARNING("No Instagram services found."))
            return

        item_ids = list(
            RVItem.objects.filter(service__in=ig_services).values_list("id", flat=True)
        )

        paths = set()
        for r in RVMedia.objects.filter(item_id__in=item_ids):
            for rel in (r.original_media, r.primary_media, r.thumbnail):
                if rel:
                    paths.add(rel)

        poster_updated = RVDomain.objects.filter(poster_image_id__in=item_ids).update(
            poster_image=None
        )

        deleted_services, per_model = RVService.objects.filter(type="instagram").delete()

        removed_files = 0
        for rel in paths:
            abs_path = utils.make_full_path(rel)
            if os.path.isfile(abs_path):
                try:
                    os.remove(abs_path)
                    removed_files += 1
                except OSError as e:
                    self.stdout.write(self.style.WARNING(f"Could not remove {abs_path}: {e}"))

        item_ct = per_model.get("rvsite.RVItem", 0)
        self.stdout.write(
            self.style.SUCCESS(
                f"Removed {deleted_services} Instagram service row(s); "
                f"cascade deleted {item_ct} item(s) (plus links/media). "
                f"Cleared {poster_updated} poster_image(s). "
                f"Deleted {removed_files} file(s) from disk."
            )
        )
