from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GroupConversationHints:
    """
    Group-chat hints from the transport layer (e.g. OneBot) for reply gating.

    - bot_mentioned: message explicitly @ the bot (or @all).
    - allow_autonomous_without_mention: group is in autonomous whitelist so messages
      may be delivered without @; use conservative rules when this is true but the
      user did not @ the bot.
    """

    bot_mentioned: bool = False
    allow_autonomous_without_mention: bool = False
