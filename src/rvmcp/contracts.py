from typing import Any
from typing_extensions import NotRequired, Required, TypedDict


class ItemCreateInput(TypedDict, total=False):
    service_id: Required[int]
    item_id: Required[str]
    datetime_created: Required[str]
    remote_url: NotRequired[str]
    title: NotRequired[str]
    caption: NotRequired[str]
    caption_format: NotRequired[str]
    public: NotRequired[bool]
    moderated: NotRequired[bool]
    edited: NotRequired[bool]
    raw_data: NotRequired[Any]


class ItemUpsertInput(ItemCreateInput, total=False):
    expected_revision: NotRequired[int]


class ItemPatchInput(TypedDict, total=False):
    expected_revision: Required[int]
    datetime_created: NotRequired[str]
    remote_url: NotRequired[str]
    title: NotRequired[str]
    caption: NotRequired[str]
    caption_format: NotRequired[str]
    public: NotRequired[bool]
    moderated: NotRequired[bool]
    edited: NotRequired[bool]
    raw_data: NotRequired[Any]


class ItemSearchFilters(TypedDict, total=False):
    domain_ids: NotRequired[list[int]]
    service_ids: NotRequired[list[int]]
    service_types: NotRequired[list[str]]
    item_id: NotRequired[str]
    created_from: NotRequired[str]
    created_to: NotRequired[str]
    retrieved_from: NotRequired[str]
    retrieved_to: NotRequired[str]
    text: NotRequired[str]
    remote_url: NotRequired[str]
    public: NotRequired[bool]
    moderated: NotRequired[bool]
    edited: NotRequired[bool]
    mirror_state: NotRequired[int]
    has_media: NotRequired[bool]
    media_types: NotRequired[list[int]]
    has_links: NotRequired[bool]
    link_is_context: NotRequired[bool]


class LinkCreateInput(TypedDict, total=False):
    item_id: Required[int]
    url: Required[str]
    title: NotRequired[str]
    description: NotRequired[str]
    is_context: NotRequired[bool]


class LinkPatchInput(TypedDict, total=False):
    expected_revision: Required[int]
    url: NotRequired[str]
    title: NotRequired[str]
    description: NotRequired[str]
    is_context: NotRequired[bool]


class MediaCreateInput(TypedDict, total=False):
    item_id: Required[int]
    content_base64: NotRequired[str]
    source_url: NotRequired[str]


class MediaPatchInput(TypedDict, total=False):
    expected_revision: Required[int]
    content_base64: NotRequired[str]
    source_url: NotRequired[str]


class ServiceCreateInput(TypedDict, total=False):
    domain_id: Required[int]
    name: Required[str]
    type: Required[str]
    live: NotRequired[bool]
    hide_unmoderated: NotRequired[bool]


class ServicePatchInput(TypedDict, total=False):
    expected_revision: Required[int]
    name: NotRequired[str]
    live: NotRequired[bool]
    hide_unmoderated: NotRequired[bool]
