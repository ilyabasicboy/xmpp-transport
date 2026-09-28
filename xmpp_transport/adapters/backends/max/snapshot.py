import hashlib
import mimetypes
from collections import deque
from typing import Optional
from urllib.parse import urlsplit

from .models import (
    MaxAvatar,
    MaxChat,
    MaxChatMember,
    MaxContact,
    MaxIncomingMessage,
    MaxMedia,
)


class MaxMessageDeduplicator:
    """Bounded dedup window for live MAX messages keyed by chat id and message id."""

    def __init__(self, limit: int):
        self._limit = limit
        self._seen_keys: set[tuple[str, str]] = set()
        self._seen_order: deque[tuple[str, str]] = deque()

    def is_duplicate(self, event: MaxIncomingMessage) -> bool:
        if event.chat_id is None or event.message_id is None:
            return False
        key = (event.chat_id, event.message_id)
        if key in self._seen_keys:
            return True
        self._seen_keys.add(key)
        self._seen_order.append(key)
        while len(self._seen_order) > self._limit:
            # Keep memory bounded while preserving recent reconnect duplicates.
            expired = self._seen_order.popleft()
            self._seen_keys.discard(expired)
        return False


class MaxSnapshotCache:
    """Store normalized chat/contact metadata extracted from MAX snapshots."""

    MEDIA_NESTED_KEYS = (
        "file",
        "files",
        "media",
        "photo",
        "photos",
        "picture",
        "pictures",
        "video",
        "videos",
        "movie",
        "movies",
        "clip",
        "clips",
        "audio",
        "audios",
        "voice",
        "voices",
        "image",
        "images",
        "attach",
        "attachment",
        "payload",
        "content",
        "data",
        "object",
        "resource",
    )

    def __init__(self) -> None:
        self.my_id: Optional[str] = None
        self.contact_names: dict[str, str] = {}
        self.contacts: dict[str, MaxContact] = {}
        self.chat_titles: dict[str, str] = {}
        self.chat_is_group: dict[str, bool] = {}
        self.chat_members: dict[str, tuple[MaxChatMember, ...]] = {}
        self.chat_avatars: dict[str, MaxAvatar] = {}
        self.address_book: dict[str, MaxContact] = {}

    def set_profile_id(self, profile_id: Optional[str]) -> None:
        self.my_id = profile_id

    def update_contact_names(self, payload: dict) -> None:
        # Name extraction and full contact extraction are separate because some MAX
        # payloads carry only partial contact data.
        self.contact_names.update(self.extract_contact_names(payload))
        self.update_contacts(payload)

    def update_contacts(self, payload: dict) -> None:
        for contact in self.extract_address_book(payload):
            if contact.contact_id == self.my_id:
                continue
            self.contacts[contact.contact_id] = contact
            self.contact_names[contact.contact_id] = contact.title

    def replace_address_book(self, payload: dict) -> None:
        self.address_book = {
            contact.contact_id: contact
            for contact in self.extract_address_book(payload)
            if contact.contact_id != self.my_id
        }
        self.contacts.update(self.address_book)

    def register_snapshot_chat(self, chat: dict) -> Optional[MaxChat]:
        chat_id = chat.get("id") or chat.get("chatId")
        if chat_id is None:
            return None
        chat_id = str(chat_id)
        # Normalize one raw MAX chat into the compact shape the transport uses everywhere.
        title = self.chat_title(chat, chat_id)
        is_group = self.is_group_chat(chat)
        members = self.chat_members_from_snapshot(chat)
        avatar = self.chat_avatar(chat)
        self.chat_titles[chat_id] = title
        self.chat_is_group[chat_id] = is_group
        self.chat_members[chat_id] = members
        if avatar is not None:
            self.chat_avatars[chat_id] = avatar
        else:
            self.chat_avatars.pop(chat_id, None)
        return MaxChat(
            chat_id=chat_id,
            title=title,
            is_group=is_group,
            members=members,
            raw=chat,
            avatar=avatar,
        )

    def has_chat(self, chat_id: str) -> bool:
        return chat_id in self.chat_titles

    def chat_by_id(self, chat_id: str) -> Optional[MaxChat]:
        if chat_id not in self.chat_titles:
            return None
        return MaxChat(
            chat_id=chat_id,
            title=self.chat_titles[chat_id],
            is_group=self.chat_is_group.get(chat_id, False),
            members=self.chat_members.get(chat_id, ()),
            avatar=self.chat_avatars.get(chat_id),
        )

    def update_chat_members(self, chat_id: str, members: tuple["MaxChatMember", ...]) -> Optional[MaxChat]:
        if chat_id not in self.chat_titles:
            return None
        self.chat_members[chat_id] = members
        return self.chat_by_id(chat_id)

    def message_chat_title(self, payload: dict, sender_id: object) -> Optional[str]:
        chat_id = payload.get("chatId")
        if chat_id is not None and str(chat_id) in self.chat_titles:
            return self.chat_titles[str(chat_id)]
        return self.contact_names.get(str(sender_id))

    def message_is_group(self, payload: dict) -> bool:
        chat_id = payload.get("chatId")
        return chat_id is not None and self.chat_is_group.get(str(chat_id), False)

    def missing_dialog_contact_ids(self, payload: dict) -> list[str]:
        missing: set[str] = set()
        chats = payload.get("chats") or []
        if not isinstance(chats, list):
            return []
        for chat in chats:
            if not isinstance(chat, dict) or chat.get("type") != "DIALOG":
                continue
            participants = chat.get("participants") or {}
            participant_ids = participants.keys() if isinstance(participants, dict) else participants
            for participant_id in participant_ids:
                participant_id = str(participant_id)
                if participant_id != self.my_id and participant_id not in self.contacts:
                    # Dialog titles require the other participant contact. Missing
                    # contacts are fetched before registering chats.
                    missing.add(participant_id)
        return sorted(missing)

    def chat_members_from_snapshot(self, chat: dict) -> tuple[MaxChatMember, ...]:
        participants = chat.get("participants") or {}
        participant_ids = participants.keys() if isinstance(participants, dict) else participants
        members = []
        for participant_id in participant_ids:
            participant_id = str(participant_id)
            if participant_id == self.my_id:
                # Xabber group membership for transport-owned MAX groups excludes the owner;
                # the owner is represented by the real XMPP account.
                continue
            title = self.contact_names.get(participant_id) or f"MAX user {participant_id}"
            members.append(
                MaxChatMember(
                    user_id=participant_id,
                    title=title,
                    avatar=self.member_avatar(participant_id),
                )
            )
        return tuple(members)

    def member_avatar(self, participant_id: str) -> Optional[MaxAvatar]:
        contact = self.contacts.get(participant_id)
        if contact is not None and contact.avatar is not None:
            return contact.avatar
        if self.my_id is None:
            return None
        try:
            # MAX direct dialog id is XOR(owner_id, participant_id). Reuse the direct
            # chat avatar when contact avatar metadata is absent.
            dialog_chat_id = str(int(self.my_id) ^ int(participant_id))
        except ValueError:
            return None
        return self.chat_avatars.get(dialog_chat_id)

    @staticmethod
    def is_group_chat(chat: dict) -> bool:
        return chat.get("type") != "DIALOG"

    def chat_title(self, chat: dict, chat_id: str) -> str:
        title = chat.get("title") or chat.get("name")
        if isinstance(title, str) and title.strip():
            return title.strip()
        if chat.get("type") == "DIALOG":
            # Dialog chat titles are usually the other participant's contact name.
            participants = chat.get("participants") or {}
            participant_ids = participants.keys() if isinstance(participants, dict) else participants
            for participant_id in participant_ids:
                participant_id = str(participant_id)
                if participant_id != self.my_id and participant_id in self.contact_names:
                    return self.contact_names[participant_id]
        return f"MAX chat {chat_id}"

    def chat_avatar(self, chat: dict) -> Optional[MaxAvatar]:
        avatar = self.extract_avatar(chat)
        if avatar is not None or chat.get("type") != "DIALOG":
            return avatar
        participants = chat.get("participants") or {}
        participant_ids = participants.keys() if isinstance(participants, dict) else participants
        for participant_id in participant_ids:
            participant_id = str(participant_id)
            if participant_id == self.my_id:
                continue
            contact = self.contacts.get(participant_id)
            if contact is not None and contact.avatar is not None:
                return contact.avatar
        return None

    def extract_address_book(self, payload: dict) -> list[MaxContact]:
        contacts = payload.get("contacts") or []
        if not isinstance(contacts, list):
            return []
        return [
            self.max_contact(contact)
            for contact in contacts
            if isinstance(contact, dict) and contact.get("id") is not None
        ]

    def max_contact(self, contact: dict, fallback_title: Optional[str] = None) -> MaxContact:
        contact_id = str(contact["id"])
        title = self.extract_contact_names({"contacts": [contact]}).get(contact_id)
        if title is None:
            title = fallback_title or f"MAX contact {contact_id}"
        if self.my_id is None:
            raise RuntimeError("MAX profile is not authorized")
        # Direct chat ids are deterministic in MAX personal protocol.
        return MaxContact(
            contact_id=contact_id,
            title=title,
            chat_id=str(int(self.my_id) ^ int(contact_id)),
            raw=contact,
            avatar=self.extract_avatar(contact),
        )

    @classmethod
    def extract_avatar(cls, payload: dict) -> Optional[MaxAvatar]:
        url = cls._extract_avatar_url(payload)
        if not url:
            return None
        # Avatar id is required by XEP-0084 metadata. Derive a stable hash if MAX does
        # not expose its own avatar/photo id.
        avatar_id = cls._extract_avatar_id(payload, url)
        mime_type = cls._extract_avatar_mime_type(payload)
        avatar_bytes = cls._extract_avatar_bytes(payload)
        return MaxAvatar(
            url=url,
            avatar_id=avatar_id,
            mime_type=mime_type,
            bytes=avatar_bytes,
        )

    @classmethod
    def _extract_avatar_url(cls, payload: dict) -> Optional[str]:
        for key in (
            "baseUrl",
            "baseIconUrl",
            "avatar",
            "avatarUrl",
            "avatar_url",
            "photo",
            "photoUrl",
            "photo_url",
            "picture",
            "pictureUrl",
            "image",
            "icon",
        ):
            value = payload.get(key)
            url = cls._avatar_url_from_value(value)
            if url:
                return cls._thumbnail_url(url)
        return None

    @classmethod
    def _avatar_url_from_value(cls, value: object) -> Optional[str]:
        if isinstance(value, str):
            return value.strip() or None
        if not isinstance(value, dict):
            return None
        # MAX has used several nested URL key names across payload types.
        for key in ("url", "href", "file", "src", "baseUrl", "base", "original"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
        thumbnails = value.get("thumbnails") or value.get("thumbnail")
        if isinstance(thumbnails, list):
            for item in thumbnails:
                url = cls._avatar_url_from_value(item)
                if url:
                    return url
        return cls._avatar_url_from_value(thumbnails)

    @staticmethod
    def _thumbnail_url(url: str, size: int = 64) -> str:
        replacements = {
            "{size}": str(size),
            "{width}": str(size),
            "{height}": str(size),
            "{w}": str(size),
            "{h}": str(size),
        }
        for source, target in replacements.items():
            url = url.replace(source, target)
        return url

    @classmethod
    def _extract_avatar_id(cls, payload: dict, url: str) -> str:
        for key in ("avatarId", "avatar_id", "photoId", "photo_id"):
            value = payload.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        for key in ("avatar", "photo", "picture", "image", "icon"):
            nested = payload.get(key)
            if not isinstance(nested, dict):
                continue
            for nested_key in ("avatarId", "avatar_id", "photoId", "photo_id", "id"):
                value = nested.get(nested_key)
                if value is not None and str(value).strip():
                    return str(value).strip()
        return hashlib.sha1(url.encode("utf-8")).hexdigest()

    @classmethod
    def _extract_avatar_mime_type(cls, payload: dict) -> str:
        for key in ("mimeType", "mime_type", "type", "contentType", "content_type"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("image/"):
                return value
        for key in ("avatar", "photo", "picture", "image", "icon"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                value = cls._extract_avatar_mime_type(nested)
                if value:
                    return value
        # Xabber avatar metadata requires a MIME type; JPEG is the safest default for
        # MAX avatar CDN thumbnails.
        return "image/jpeg"

    @classmethod
    def _extract_avatar_bytes(cls, payload: dict) -> int:
        for key in ("bytes", "size", "fileSize", "file_size"):
            value = payload.get(key)
            if isinstance(value, int) and value > 0:
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        for key in ("avatar", "photo", "picture", "image", "icon"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                value = cls._extract_avatar_bytes(nested)
                if value:
                    return value
        return 0

    @classmethod
    def extract_media(cls, message: dict) -> tuple[MaxMedia, ...]:
        attaches = message.get("attaches") or message.get("attachments") or []
        media: list[MaxMedia] = []
        for item in cls._iter_media_attaches(attaches):
            media_item = cls.media_from_attach(item)
            if media_item is not None:
                media.append(media_item)
        return tuple(media)

    @classmethod
    def unsupported_video_attachments(cls, message: dict) -> tuple[dict, ...]:
        attaches = message.get("attaches") or message.get("attachments") or []
        result: list[dict] = []
        cls._collect_video_attachments(attaches, result)
        return tuple(result)

    @classmethod
    def unsupported_file_attachments(cls, message: dict) -> tuple[dict, ...]:
        attaches = message.get("attaches") or message.get("attachments") or []
        result: list[dict] = []
        cls._collect_file_attachments(attaches, result)
        return tuple(result)

    @classmethod
    def unsupported_audio_attachments(cls, message: dict) -> tuple[dict, ...]:
        attaches = message.get("attaches") or message.get("attachments") or []
        result: list[dict] = []
        cls._collect_audio_attachments(attaches, result)
        return tuple(result)

    @classmethod
    def _collect_video_attachments(cls, value: object, result: list[dict], type_hint: Optional[str] = None) -> None:
        if isinstance(value, list):
            for item in value:
                cls._collect_video_attachments(item, result, type_hint=type_hint)
            return
        if not isinstance(value, dict):
            return
        attach = dict(value)
        if type_hint and not attach.get("type") and not attach.get("_type"):
            # Parent key names like "video" or "files" are useful when the nested
            # payload itself does not carry a type field.
            attach["type"] = type_hint
        if cls._is_video_attachment(attach):
            result.append(attach)
        for key, nested in value.items():
            if key in ("thumbnail", "thumbnails", "preview", "previewData"):
                continue
            cls._collect_video_attachments(nested, result, type_hint=cls._media_type_hint(key))

    @classmethod
    def _is_video_attachment(cls, payload: dict) -> bool:
        for key in ("_type", "type", "mediaType", "media_type", "fileType", "file_type"):
            value = payload.get(key)
            if isinstance(value, str) and cls._media_type_hint(value) == "video":
                return True
        return payload.get("videoId") is not None

    @classmethod
    def _collect_file_attachments(cls, value: object, result: list[dict], type_hint: Optional[str] = None) -> None:
        if isinstance(value, list):
            for item in value:
                cls._collect_file_attachments(item, result, type_hint=type_hint)
            return
        if not isinstance(value, dict):
            return
        attach = dict(value)
        if type_hint and not attach.get("type") and not attach.get("_type"):
            attach["type"] = type_hint
        if cls._is_file_attachment(attach):
            result.append(attach)
            return
        for key, nested in value.items():
            if key in ("thumbnail", "thumbnails", "preview", "previewData"):
                continue
            cls._collect_file_attachments(nested, result, type_hint=cls._media_type_hint(key))

    @classmethod
    def _is_file_attachment(cls, payload: dict) -> bool:
        for key in ("_type", "type", "mediaType", "media_type", "fileType", "file_type"):
            value = payload.get(key)
            if not isinstance(value, str):
                continue
            type_hint = cls._media_type_hint(value)
            if type_hint in {"audio", "voice"}:
                # Audio/voice ids are resolved through the audio path, not as generic files.
                return False
            if type_hint == "file":
                return True
        return (
            payload.get("fileId") is not None
            and payload.get("videoId") is None
            and payload.get("photoId") is None
            and payload.get("audioId") is None
            and payload.get("voiceId") is None
        )

    @classmethod
    def _collect_audio_attachments(cls, value: object, result: list[dict], type_hint: Optional[str] = None) -> None:
        if isinstance(value, list):
            for item in value:
                cls._collect_audio_attachments(item, result, type_hint=type_hint)
            return
        if not isinstance(value, dict):
            return
        attach = dict(value)
        if type_hint and not attach.get("type") and not attach.get("_type"):
            attach["type"] = type_hint
        if cls._is_audio_attachment_requiring_resolution(attach):
            result.append(attach)
            return
        for key, nested in value.items():
            if key in ("thumbnail", "thumbnails", "preview", "previewData"):
                continue
            cls._collect_audio_attachments(nested, result, type_hint=cls._media_type_hint(key))

    @classmethod
    def _is_audio_attachment_requiring_resolution(cls, payload: dict) -> bool:
        if cls._extract_media_url(payload):
            # If the URL is already present, normal media extraction can handle it.
            return False
        for key in ("_type", "type", "mediaType", "media_type", "fileType", "file_type"):
            value = payload.get(key)
            if isinstance(value, str) and cls._media_type_hint(value) in {"audio", "voice"}:
                return True
        return payload.get("audioId") is not None or payload.get("voiceId") is not None

    @classmethod
    def _iter_media_attaches(cls, value: object) -> tuple[dict, ...]:
        result: list[dict] = []
        cls._collect_media_attaches(value, result)
        return tuple(result)

    @classmethod
    def _collect_media_attaches(cls, value: object, result: list[dict], type_hint: Optional[str] = None) -> None:
        if isinstance(value, list):
            for item in value:
                cls._collect_media_attaches(item, result, type_hint=type_hint)
            return
        if not isinstance(value, dict):
            return
        attach = dict(value)
        if type_hint and not attach.get("type"):
            attach["type"] = type_hint
        if cls._extract_media_url(attach):
            result.append(attach)
            return
        # MAX attachment payloads vary by media type and client version. Walk nested
        # objects, but skip URL collections that _media_url_from_value already handles.
        for key, nested in value.items():
            if key in ("urls", "sources", "variants", "thumbnails"):
                continue
            cls._collect_media_attaches(nested, result, type_hint=cls._media_type_hint(key))

    @classmethod
    def media_from_attach(cls, attach: dict) -> Optional[MaxMedia]:
        url = cls._extract_media_url(attach)
        if not url:
            return None
        mime_type = cls._extract_media_mime_type(attach, url)
        # Convert a raw MAX attach into the transport's neutral media DTO.
        return MaxMedia(
            url=url,
            name=cls._extract_media_name(attach, url),
            mime_type=mime_type,
            size=cls._extract_media_size(attach),
            thumbnail_url=cls._extract_media_thumbnail_url(attach),
            width=cls._extract_media_int(attach, ("width", "w")),
            height=cls._extract_media_int(attach, ("height", "h")),
            duration=cls._extract_media_duration(attach),
            voice=cls._is_voice_attachment(attach),
        )

    @classmethod
    def _is_voice_attachment(cls, payload: dict) -> bool:
        for key in ("_type", "type", "mediaType", "media_type", "fileType", "file_type"):
            value = payload.get(key)
            if isinstance(value, str) and cls._media_type_hint(value) == "voice":
                return True
            if isinstance(value, str) and cls._media_type_hint(value) == "audio" and payload.get("wave") is not None:
                return True
        return payload.get("voiceId") is not None or bool(payload.get("voice"))

    @classmethod
    def _extract_media_url(cls, payload: dict) -> Optional[str]:
        # Try explicit URL-ish fields first before recursively scanning nested payloads.
        for key in (
            "url",
            "href",
            "downloadUrl",
            "download_url",
            "downloadLink",
            "download_link",
            "fileUrl",
            "file_url",
            "mediaUrl",
            "media_url",
            "contentUrl",
            "content_url",
            "audioUrl",
            "audio_url",
            "voiceUrl",
            "voice_url",
            "videoUrl",
            "video_url",
            "movieUrl",
            "movie_url",
            "streamUrl",
            "stream_url",
            "playUrl",
            "play_url",
            "source",
            "src",
            "baseUrl",
            "base",
            "originalUrl",
            "original_url",
        ):
            value = payload.get(key)
            url = cls._media_url_from_value(value)
            if url:
                return cls._media_url(url)
        for key in cls.MEDIA_NESTED_KEYS:
            nested = payload.get(key)
            url = cls._media_url_from_value(nested)
            if url:
                return cls._media_url(url)
        return None

    @classmethod
    def _extract_media_thumbnail_url(cls, payload: dict) -> Optional[str]:
        # Prefer preview/poster fields over the main media URL.
        for key in (
            "thumbnail",
            "thumbnailUrl",
            "thumbnail_url",
            "thumbUrl",
            "thumb_url",
            "preview",
            "previewUrl",
            "preview_url",
            "previewImage",
            "preview_image",
            "poster",
            "posterUrl",
            "poster_url",
        ):
            url = cls._media_url_from_value(payload.get(key))
            if url:
                return cls._media_url(url, size=320)
        for key in cls.MEDIA_NESTED_KEYS:
            nested = payload.get(key)
            if isinstance(nested, dict):
                url = cls._extract_media_thumbnail_url(nested)
                if url:
                    return url
        return None

    @classmethod
    def _media_url_from_value(cls, value: object) -> Optional[str]:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith(("http://", "https://")):
                return value
            return None
        if isinstance(value, list):
            for item in value:
                url = cls._media_url_from_value(item)
                if url:
                    return url
            return None
        if not isinstance(value, dict):
            return None
        for key in (
            "url",
            "href",
            "downloadUrl",
            "download_url",
            "downloadLink",
            "download_link",
            "fileUrl",
            "file_url",
            "mediaUrl",
            "media_url",
            "contentUrl",
            "content_url",
            "audioUrl",
            "audio_url",
            "voiceUrl",
            "voice_url",
            "videoUrl",
            "video_url",
            "movieUrl",
            "movie_url",
            "streamUrl",
            "stream_url",
            "playUrl",
            "play_url",
            "src",
            "baseUrl",
            "base",
            "original",
            "originalUrl",
            "original_url",
        ):
            url = cls._media_url_from_value(value.get(key))
            if url:
                return url
        for key in ("urls", "sources", "variants", "thumbnails"):
            url = cls._media_url_from_value(value.get(key))
            if url:
                return url
        # Last-resort traversal keeps media support resilient to small MAX schema
        # changes without hard-coding every attachment shape.
        for key, nested in value.items():
            if cls._media_type_hint(key) in {"thumbnail", "preview"}:
                continue
            url = cls._media_url_from_value(nested)
            if url:
                return url
        return None

    @staticmethod
    def _media_url(url: str, size: int = 1024) -> str:
        replacements = {
            "{size}": str(size),
            "{width}": str(size),
            "{height}": str(size),
            "{w}": str(size),
            "{h}": str(size),
        }
        for source, target in replacements.items():
            url = url.replace(source, target)
        return url

    @classmethod
    def _extract_media_name(cls, payload: dict, url: str) -> str:
        for key in ("name", "fileName", "file_name", "filename", "title", "caption"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in cls.MEDIA_NESTED_KEYS:
            nested = payload.get(key)
            if isinstance(nested, dict):
                value = cls._extract_media_name(nested, url)
                if value:
                    return value
        tail = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        return tail or "MAX media"

    @classmethod
    def _extract_media_mime_type(cls, payload: dict, url: str) -> str:
        for key in (
            "_type",
            "mediaType",
            "media_type",
            "mimeType",
            "mime_type",
            "type",
            "contentType",
            "content_type",
            "fileType",
            "file_type",
        ):
            value = payload.get(key)
            if isinstance(value, str):
                value = value.strip().lower()
                if "/" in value:
                    return value
                if cls._media_type_hint(value) == "audio" and cls._is_voice_attachment(payload):
                    # MAX often labels voice as AUDIO plus wave metadata.
                    return "audio/ogg"
                mapped = cls._media_mime_type_from_token(value) or cls._media_mime_type_from_token(
                    cls._media_type_hint(value)
                )
                if mapped:
                    return mapped
        for key in ("photo", "image", "picture", "preview"):
            if key in payload:
                return "image/jpeg"
        if "sticker" in payload:
            return "image/webp"
        if any(key in payload for key in ("video", "videos", "movie", "movies", "clip", "clips")):
            return "video/mp4"
        if "audio" in payload or "voice" in payload:
            return "audio/mpeg"
        for key in cls.MEDIA_NESTED_KEYS:
            nested = payload.get(key)
            if isinstance(nested, dict):
                value = cls._extract_media_mime_type(nested, url)
                if value != "application/octet-stream":
                    return value
        guessed, _encoding = mimetypes.guess_type(url.split("?", 1)[0])
        if guessed:
            return guessed
        name = cls._extract_media_name(payload, url)
        guessed, _encoding = mimetypes.guess_type(name)
        # Unknown media stays generic until the backend optionally probes Content-Type.
        return guessed or "application/octet-stream"

    @staticmethod
    def _media_mime_type_from_token(value: str) -> Optional[str]:
        return {
            "image": "image/jpeg",
            "img": "image/jpeg",
            "photo": "image/jpeg",
            "picture": "image/jpeg",
            "pic": "image/jpeg",
            "sticker": "image/webp",
            "video": "video/mp4",
            "videos": "video/mp4",
            "movie": "video/mp4",
            "movies": "video/mp4",
            "clip": "video/mp4",
            "clips": "video/mp4",
            "audio": "audio/mpeg",
            "audios": "audio/mpeg",
            "voice": "audio/ogg",
            "voices": "audio/ogg",
            "file": "application/octet-stream",
        }.get(value)

    @classmethod
    def _extract_media_size(cls, payload: dict) -> int:
        return cls._extract_media_int(payload, ("size", "bytes", "fileSize", "file_size", "fileBytes")) or 0

    @classmethod
    def _extract_media_duration(cls, payload: dict) -> Optional[int]:
        if cls._max_duration_is_milliseconds(payload):
            # MAX audio duration commonly arrives in milliseconds in the generic
            # "duration" field, unlike video payloads where it is often seconds.
            milliseconds = cls._extract_media_int(payload, ("duration", "length"))
            if milliseconds:
                return max(1, round(milliseconds / 1000))
        seconds = cls._extract_media_int(
            payload,
            ("duration", "durationSeconds", "duration_seconds", "length", "lengthSeconds", "length_seconds"),
        )
        if seconds:
            return seconds
        milliseconds = cls._extract_media_int(
            payload,
            ("durationMs", "duration_ms", "durationMillis", "duration_millis", "lengthMs", "length_ms"),
        )
        if milliseconds:
            return max(1, round(milliseconds / 1000))
        return None

    @classmethod
    def _max_duration_is_milliseconds(cls, payload: dict) -> bool:
        for key in ("_type", "type", "mediaType", "media_type", "fileType", "file_type"):
            value = payload.get(key)
            if isinstance(value, str) and cls._media_type_hint(value) == "audio":
                return payload.get("wave") is not None or payload.get("audioId") is not None
        return False

    @classmethod
    def _extract_media_int(cls, payload: dict, keys: tuple[str, ...]) -> Optional[int]:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, int) and value > 0:
                return value
            if isinstance(value, float) and value > 0:
                return round(value)
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.isdigit():
                    return int(stripped)
                try:
                    numeric = float(stripped)
                except ValueError:
                    numeric = 0
                if numeric > 0:
                    return round(numeric)
        for key in cls.MEDIA_NESTED_KEYS:
            nested = payload.get(key)
            if isinstance(nested, dict):
                value = cls._extract_media_int(nested, keys)
                if value:
                    return value
        return None

    @staticmethod
    def _media_type_hint(value: object) -> str:
        normalized = str(value or "").strip().lower().replace("-", "_")
        for suffix in ("_url", "_urls", "_id", "_ids", "_token", "_tokens", "_meta", "_metadata"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)]
        if normalized in {"thumb", "thumbnail", "thumbnails"}:
            return "thumbnail"
        if normalized in {"preview", "previews", "preview_image", "poster"}:
            return "preview"
        return normalized

    @staticmethod
    def extract_contact_names(payload: dict) -> dict[str, str]:
        contacts = payload.get("contacts") or []
        if not isinstance(contacts, list):
            return {}
        names: dict[str, str] = {}
        for contact in contacts:
            if not isinstance(contact, dict) or contact.get("id") is None:
                continue
            variants = contact.get("names") or []
            if not isinstance(variants, list):
                continue
            preferred: Optional[str] = None
            for kind in ("CUSTOM", "ONEME"):
                # Prefer user-custom names over MAX-provided names when both are present.
                for variant in variants:
                    if not isinstance(variant, dict) or variant.get("type") != kind:
                        continue
                    candidate = MaxSnapshotCache._contact_variant_display_name(variant)
                    if candidate:
                        preferred = candidate
                        break
                if preferred is not None:
                    break
            if preferred is None:
                preferred = MaxSnapshotCache._contact_variant_display_name(contact)
            if preferred is not None:
                names[str(contact["id"])] = preferred
        return names

    @staticmethod
    def _contact_variant_display_name(variant: dict) -> Optional[str]:
        full_name = MaxSnapshotCache._contact_name_part(
            variant,
            ("name", "fullName", "full_name", "displayName", "display_name"),
        )
        first_name = MaxSnapshotCache._contact_name_part(
            variant,
            ("firstName", "first_name", "firstname", "givenName", "given_name"),
        )
        last_name = MaxSnapshotCache._contact_name_part(
            variant,
            ("lastName", "last_name", "lastname", "familyName", "family_name", "surname"),
        )
        joined_name = " ".join(part for part in (first_name, last_name) if part)
        if full_name and last_name and joined_name and last_name.casefold() not in full_name.casefold():
            return f"{full_name} {last_name}"
        return joined_name or full_name

    @staticmethod
    def _contact_name_part(payload: dict, keys: tuple[str, ...]) -> Optional[str]:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())
        return None
