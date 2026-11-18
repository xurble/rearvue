import re

from django import forms
from django.contrib import admin, messages

from .models import (
    MCP_SCOPES,
    MCPAuditRecord,
    MCPClient,
    MCPDestructivePreview,
    MCPIdempotencyRecord,
    MCPJob,
)


class MCPClientAdminForm(forms.ModelForm):
    new_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to generate a high-entropy token. The token is shown once after saving.",
    )
    scopes = forms.MultipleChoiceField(
        choices=[(scope, scope) for scope in sorted(MCP_SCOPES)],
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = MCPClient
        fields = ("name", "new_token", "scopes", "domains", "enabled", "expires_at")

    def clean_new_token(self):
        token = self.cleaned_data["new_token"]
        if token and (
            len(token) > 256
            or re.fullmatch(r"rvmcp_[0-9a-f]{8}_[A-Za-z0-9_-]{32,}", token) is None
        ):
            raise forms.ValidationError(
                "Use rvmcp_<8 lowercase hex characters>_<at least 32 URL-safe characters>."
            )
        return token


@admin.register(MCPClient)
class MCPClientAdmin(admin.ModelAdmin):
    form = MCPClientAdminForm
    list_display = ("name", "enabled", "expires_at", "token_prefix", "updated_at")
    filter_horizontal = ("domains",)
    readonly_fields = ("token_prefix", "created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        supplied = form.cleaned_data.get("new_token")
        if supplied:
            obj.token_prefix = supplied.split("_", 2)[1]
            obj.token_hash = obj.hash_token(supplied)
            shown_token = supplied
        elif not obj.token_hash:
            shown_token = obj.rotate_token()
        else:
            shown_token = None
        super().save_model(request, obj, form, change)
        if shown_token:
            self.message_user(
                request,
                f"Bearer token for {obj.name} (copy now; it will not be shown again): {shown_token}",
                level=messages.WARNING,
            )


@admin.register(MCPAuditRecord)
class MCPAuditRecordAdmin(admin.ModelAdmin):
    list_display = ("created_at", "client", "operation", "outcome", "domain_name", "affected_count")
    list_filter = ("operation", "outcome")
    readonly_fields = tuple(field.name for field in MCPAuditRecord._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MCPIdempotencyRecord)
class MCPIdempotencyRecordAdmin(admin.ModelAdmin):
    list_display = ("client", "operation", "key", "expires_at", "created_at")
    readonly_fields = tuple(field.name for field in MCPIdempotencyRecord._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(MCPJob)
class MCPJobAdmin(admin.ModelAdmin):
    list_display = (
        "id", "created_at", "operation", "status", "domain", "attempt_count",
        "progress_current", "progress_total",
    )
    list_filter = ("operation", "status")
    readonly_fields = tuple(field.name for field in MCPJob._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MCPDestructivePreview)
class MCPDestructivePreviewAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "operation", "domain", "expires_at", "used_at")
    readonly_fields = tuple(field.name for field in MCPDestructivePreview._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
