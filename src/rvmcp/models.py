import hashlib
import secrets

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from rvsite.models import RVDomain

MCP_SCOPES = frozenset({"domain:owner"})


class MCPClient(models.Model):
    name = models.CharField(max_length=128, unique=True)
    token_prefix = models.CharField(max_length=16, db_index=True, editable=False)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    scopes = models.JSONField(default=list, blank=True)
    domains = models.ManyToManyField(RVDomain, related_name="mcp_clients", blank=True)
    enabled = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        scopes = self.scopes if isinstance(self.scopes, list) else []
        unknown = sorted(set(scopes) - MCP_SCOPES)
        if unknown:
            raise ValidationError({"scopes": f"Unknown scopes: {', '.join(unknown)}"})
        if len(scopes) != len(set(scopes)):
            raise ValidationError({"scopes": "Scopes must not contain duplicates."})

    @staticmethod
    def hash_token(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def rotate_token(self):
        prefix = secrets.token_hex(4)
        token = f"rvmcp_{prefix}_{secrets.token_urlsafe(32)}"
        self.token_prefix = prefix
        self.token_hash = self.hash_token(token)
        return token

    @property
    def is_active(self):
        return self.enabled and (self.expires_at is None or self.expires_at > timezone.now())


class MCPIdempotencyRecord(models.Model):
    client = models.ForeignKey(MCPClient, on_delete=models.CASCADE)
    operation = models.CharField(max_length=64)
    key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    response = models.JSONField(default=dict)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("client", "operation", "key"),
                name="rvmcp_idempotency_client_operation_key_unique",
            ),
        ]


class MCPAuditRecord(models.Model):
    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILURE = "failure", "Failure"
        CONFLICT = "conflict", "Conflict"

    client = models.ForeignKey(MCPClient, on_delete=models.PROTECT)
    domain = models.ForeignKey(RVDomain, null=True, on_delete=models.SET_NULL)
    domain_name = models.CharField(max_length=32, blank=True)
    operation = models.CharField(max_length=64)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    affected_ids = models.JSONField(default=list, blank=True)
    affected_count = models.PositiveIntegerField(default=0)
    idempotency_key = models.CharField(max_length=128, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")


class MCPJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    client = models.ForeignKey(MCPClient, on_delete=models.PROTECT, related_name="mcp_jobs")
    domain = models.ForeignKey(RVDomain, on_delete=models.PROTECT, related_name="mcp_jobs")
    operation = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    progress_current = models.PositiveBigIntegerField(default=0)
    progress_total = models.PositiveBigIntegerField(default=0)
    result = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    failures = models.JSONField(default=list, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    run_after = models.DateTimeField(default=timezone.now, db_index=True)
    lease_owner = models.CharField(max_length=128, blank=True)
    lease_token = models.CharField(max_length=64, blank=True, editable=False)
    leased_until = models.DateTimeField(null=True, blank=True, db_index=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    artifact_path = models.CharField(max_length=512, blank=True)
    artifact_sha256 = models.CharField(max_length=64, blank=True)
    artifact_size = models.PositiveBigIntegerField(default=0)
    artifact_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=("status", "run_after", "id"), name="rvmcp_job_claim_idx"),
            models.Index(fields=("domain", "created_at", "id"), name="rvmcp_job_domain_idx"),
        ]


class MCPExportSnapshot(models.Model):
    client = models.ForeignKey(MCPClient, on_delete=models.CASCADE, related_name="export_snapshots")
    filters = models.JSONField(default=dict)
    binding_hash = models.CharField(max_length=64)
    snapshot_at = models.DateTimeField()
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")


class MCPExportSnapshotRecord(models.Model):
    snapshot = models.ForeignKey(MCPExportSnapshot, on_delete=models.CASCADE, related_name="records")
    ordinal = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=32)
    source_id = models.PositiveBigIntegerField()
    payload = models.JSONField(default=dict)

    class Meta:
        ordering = ("ordinal",)
        constraints = [
            models.UniqueConstraint(
                fields=("snapshot", "ordinal"),
                name="rvmcp_export_snapshot_ordinal_unique",
            ),
        ]
        indexes = [
            models.Index(fields=("snapshot", "ordinal"), name="rvmcp_export_page_idx"),
        ]


class MCPDestructivePreview(models.Model):
    client = models.ForeignKey(MCPClient, on_delete=models.PROTECT, related_name="destructive_previews")
    domain = models.ForeignKey(RVDomain, on_delete=models.PROTECT, related_name="destructive_previews")
    operation = models.CharField(max_length=64)
    selector = models.JSONField(default=dict)
    impact = models.JSONField(default=dict)
    impact_hash = models.CharField(max_length=64)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
