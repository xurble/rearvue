import logging

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max, Min
from django.template.loader import render_to_string
from django.utils import timezone

from rearvue.utils import make_full_path
from rvservices.results import OperationResult, log_safe_exception
from rvsite.models import RVDomain, RVItem

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Update content from all services (RSS, Twitter, Instagram, Flickr) and generate RSS feeds"

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-rss",
            action="store_true",
            help="Skip RSS updates",
        )
        parser.add_argument(
            "--skip-twitter",
            action="store_true",
            help="Skip Twitter updates",
        )
        parser.add_argument(
            "--skip-instagram",
            action="store_true",
            help="Skip Instagram updates",
        )
        parser.add_argument(
            "--skip-flickr",
            action="store_true",
            help="Skip Flickr updates",
        )
        parser.add_argument(
            "--skip-cleanup",
            action="store_true",
            help="Skip domain cleanup and RSS generation",
        )

    def handle(self, *args, **options):
        failed_phases = []

        if not options["skip_rss"]:
            from rvservices.rss_service import find_rss_links, mirror_rss, update_rss

            self._run_phase("rss-update", update_rss, failed_phases)
            self._run_phase("rss-mirror", mirror_rss, failed_phases)
            self._run_phase("rss-links", find_rss_links, failed_phases)

        if not options["skip_twitter"]:
            from rvservices.twitter_service import find_twitter_links, mirror_twitter

            self._run_phase("twitter-mirror", mirror_twitter, failed_phases)
            self._run_phase("twitter-links", find_twitter_links, failed_phases)

        if not options["skip_instagram"]:
            from rvservices.instagram_graph_service import (
                mirror_instagram,
                update_instagram,
            )

            self._run_phase("instagram-update", update_instagram, failed_phases)
            self._run_phase("instagram-mirror", mirror_instagram, failed_phases)

        if not options["skip_flickr"]:
            from rvservices.flickr_service import mirror_flickr, update_flickr

            self._run_phase("flickr-update", update_flickr, failed_phases)
            self._run_phase("flickr-mirror", mirror_flickr, failed_phases)

        if not options["skip_cleanup"]:
            for domain in RVDomain.objects.all():
                self._run_phase(
                    f"domain-metadata:{domain.id}",
                    lambda domain=domain: self._update_domain_metadata(domain),
                    failed_phases,
                )
                self._run_phase(
                    f"domain-feed:{domain.id}",
                    lambda domain=domain: self._generate_domain_feed(domain),
                    failed_phases,
                )

        if failed_phases:
            summary = ", ".join(f"{phase}={count}" for phase, count in failed_phases)
            message = f"Content update failed; failed phases/counts: {summary}"
            self.stdout.write(self.style.ERROR(message))
            raise CommandError(message)

        self.stdout.write(
            self.style.SUCCESS("Content update completed; failed phases/counts: none")
        )

    def _run_phase(self, name, callback, failed_phases):
        self.stdout.write(f"Running {name}")
        try:
            result = callback()
        except Exception as exc:  # noqa: BLE001 - scheduler phase boundary.
            log_safe_exception(
                logger,
                "Content update phase failed phase=%s",
                name,
                exc=exc,
            )
            failed_phases.append((name, 1))
            self.stdout.write(self.style.ERROR(f"{name} failed (1)"))
            return

        if result is None:
            result = OperationResult(processed=1)
        if not isinstance(result, OperationResult):
            raise TypeError(f"Phase {name} returned an unsupported result")

        if result.failed:
            failed_phases.append((name, result.failed))
            self.stdout.write(
                self.style.ERROR(
                    f"{name} completed with failures: processed={result.processed} failed={result.failed}"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"{name} completed: processed={result.processed}")
            )

    def _update_domain_metadata(self, domain):
        years = RVItem.objects.filter(service__domain=domain).aggregate(
            Max("datetime_created"), Min("datetime_created")
        )
        max_year = years["datetime_created__max"]
        min_year = years["datetime_created__min"]

        if max_year and min_year:
            domain.max_year = max_year.year
            domain.min_year = min_year.year
            domain.last_updated = timezone.now()
            domain.save(update_fields=["max_year", "min_year", "last_updated"])
        else:
            logger.warning("No items found for domain id=%s", domain.id)
        return OperationResult(processed=1)

    def _generate_domain_feed(self, domain):
        rss_path = make_full_path(f"media/{domain.name}/rss.xml")
        values = {
            "domain": domain,
            "items": RVItem.objects.filter(
                service__domain=domain, public=True
            ).order_by("-datetime_created")[:25],
        }
        with open(rss_path, "w", encoding="utf-8") as output:
            output.write(render_to_string("rss.xml", values))
        return OperationResult(processed=1)
