"""The language-model side of the system: one client, one set of prompts, no other entry point.

`client` is the only module that opens a socket to DeepSeek; `prompts` is the only module that decides
what to say. Anything that needs a model goes through these two, so there is exactly one place to look
when a bill is surprising or a prompt is wrong.
"""

from server.app.llm import client, prompts

__all__ = ["client", "prompts"]
