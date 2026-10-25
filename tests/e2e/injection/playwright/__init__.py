"""Fake ``playwright`` package that shadows the real one in E2E children.

Importing this package must never start a browser, a Node process, or a socket.
It exists so the *real* ``dragon_quant.providers.cookie`` code path can be
covered deterministically (success / empty / error) in M0.
"""

__all__: list[str] = []
