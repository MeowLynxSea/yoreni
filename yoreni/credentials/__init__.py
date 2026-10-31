"""Connector, account, credential, and secret-redaction runtime.

The package owns the only path through which stored secrets enter or leave the
process. Agent-facing code sees placeholders and account metadata; the model
API only ever sees redacted text.
"""

from yoreni.credentials.auth import AuthRequest, AuthRequestManager, AuthRequestStatus
from yoreni.credentials.manager import (
    CredentialManager,
    get_credential_manager,
    reset_credential_manager,
)
from yoreni.credentials.models import (
    Account,
    AuthMethod,
    ConnectorSpec,
    CredentialKind,
)

__all__ = [
    "Account",
    "AuthMethod",
    "AuthRequest",
    "AuthRequestManager",
    "AuthRequestStatus",
    "ConnectorSpec",
    "CredentialKind",
    "CredentialManager",
    "get_credential_manager",
    "reset_credential_manager",
]
