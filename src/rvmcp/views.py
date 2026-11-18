import mimetypes

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.views.decorators.http import require_GET

from rvsite.models import RVMedia

from .capabilities import controlled_artifact_path, controlled_media_path
from .models import MCPJob
from .services import MCPServiceError, authenticate_token, require_scope


def _client_from_request(request):
    if not settings.MCP_ENABLED:
        raise Http404
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    client = authenticate_token(token) if scheme.lower() == "bearer" and token else None
    if client is None:
        return None
    try:
        require_scope(client, "domain:owner")
    except MCPServiceError:
        return None
    return client


def _unauthorized():
    response = HttpResponse("Bearer authentication required", status=401, content_type="text/plain")
    response["WWW-Authenticate"] = 'Bearer realm="RearVue MCP"'
    response["Cache-Control"] = "no-store"
    return response


def _file_response(path, *, content_type=None, filename=None):
    response = FileResponse(
        path.open("rb"),
        content_type=content_type or "application/octet-stream",
        as_attachment=True,
        filename=filename or path.name,
    )
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
def download_media(request, media_id):
    client = _client_from_request(request)
    if client is None:
        return _unauthorized()
    media = RVMedia.objects.select_related("item").filter(
        pk=media_id, item__domain__mcp_clients=client
    ).first()
    if media is None:
        raise Http404
    try:
        path = controlled_media_path(media)
    except (MCPServiceError, FileNotFoundError, OSError):
        raise Http404 from None
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return _file_response(path, content_type=content_type)


@require_GET
def download_job_artifact(request, job_id):
    client = _client_from_request(request)
    if client is None:
        return _unauthorized()
    job = MCPJob.objects.filter(pk=job_id, client=client, domain__mcp_clients=client).first()
    if job is None:
        raise Http404
    try:
        path = controlled_artifact_path(job)
    except (MCPServiceError, FileNotFoundError, OSError):
        raise Http404 from None
    return _file_response(path, content_type="application/x-ndjson")
