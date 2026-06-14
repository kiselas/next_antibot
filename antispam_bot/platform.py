"""Platform port: messenger-agnostic events and outbound actions.

The moderation core (``core.Core``) depends only on this interface, so a new
messenger is supported by writing a single adapter that (a) converts its updates
into the event dataclasses below and (b) implements :class:`BotPlatform`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

# An inline button: (label, callback_data). A keyboard is a list of rows.
Button = tuple[str, str]


@dataclass(frozen=True)
class User:
    id: int
    username: str | None = None
    is_bot: bool = False


@dataclass(frozen=True)
class IncomingMessage:
    chat_id: int
    message_id: int
    user: User
    text: str
    is_group: bool = True
    is_automatic: bool = False  # channel post / anonymous admin -> not moderated
    has_links: bool = False
    has_mentions: bool = False
    is_forward: bool = False
    chat_title: str | None = None


@dataclass(frozen=True)
class MemberUpdate:
    chat_id: int
    user: User
    joined: bool  # transitioned into membership


@dataclass(frozen=True)
class BotMembership:
    chat_id: int
    present: bool  # the bot is now a member/admin of this chat
    chat_title: str | None = None


@dataclass(frozen=True)
class CallbackAction:
    callback_id: str
    data: str
    user: User
    chat_id: int | None = None
    message_id: int | None = None
    message_text: str | None = None


@dataclass(frozen=True)
class CommandRequest:
    chat_id: int
    user: User
    args: list[str]


class BotPlatform(ABC):
    """Outbound actions the core needs from a messenger."""

    @abstractmethod
    async def delete_message(self, chat_id: int, message_id: int) -> None: ...

    @abstractmethod
    async def ban_user(self, chat_id: int, user_id: int) -> None: ...

    @abstractmethod
    async def mute_user(self, chat_id: int, user_id: int) -> None: ...

    @abstractmethod
    async def unban_user(self, chat_id: int, user_id: int) -> None:
        """Lift a ban and any restriction (mute)."""

    @abstractmethod
    async def send_message(
        self, chat_id: int, text: str, *, buttons: list[list[Button]] | None = None
    ) -> None: ...

    @abstractmethod
    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None: ...

    @abstractmethod
    async def answer_callback(
        self, callback_id: str, text: str | None = None, *, alert: bool = False
    ) -> None: ...

    @abstractmethod
    async def chat_admin_ids(self, chat_id: int) -> set[int]: ...

    @abstractmethod
    async def leave_chat(self, chat_id: int) -> None: ...
