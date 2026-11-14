"""What every Pack Studio route shares: the permission (``packs.author``, DEC-26) and the request id for audit."""

from __future__ import annotations

from fastapi import Depends, Request

from tela_api.identity.deps import require
from tela_api.identity.permissions import Perm

AUTHOR = Depends(require(Perm.PACKS_AUTHOR))


def rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)
