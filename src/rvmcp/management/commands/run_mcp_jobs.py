import socket
import time
import uuid

from django.core.management.base import BaseCommand, CommandError

from rvmcp.jobs import run_one_job


class Command(BaseCommand):
    help = "Run durable RearVue MCP jobs from the explicit operation registry."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Exit after one claim attempt.")
        parser.add_argument("--max-jobs", type=int, default=0, help="Exit after this many jobs; 0 runs continuously.")
        parser.add_argument("--poll-interval", type=float, default=1.0)
        parser.add_argument("--worker-id", default="")

    def handle(self, *args, **options):
        max_jobs = options["max_jobs"]
        poll_interval = options["poll_interval"]
        if max_jobs < 0:
            raise CommandError("--max-jobs must be zero or greater.")
        if not 0.05 <= poll_interval <= 60:
            raise CommandError("--poll-interval must be between 0.05 and 60 seconds.")
        worker_id = options["worker_id"] or f"{socket.gethostname()}:{uuid.uuid4().hex}"
        if len(worker_id) > 128:
            raise CommandError("--worker-id must be at most 128 characters.")

        processed = 0
        while True:
            claimed = run_one_job(worker_id)
            if claimed:
                processed += 1
                if max_jobs and processed >= max_jobs:
                    break
            elif options["once"] or max_jobs:
                break
            else:
                time.sleep(poll_interval)
        self.stdout.write(f"Processed {processed} MCP job(s).")
