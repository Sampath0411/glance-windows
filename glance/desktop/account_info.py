"""Windows sign-in identity checks used by the About-page helper."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class AccountInfo:
    account_type: str
    account: str
    upn: str
    details: str = ""


def classify_account(account: str, upn: str, computer_name: str) -> AccountInfo:
    """Classify only when command output provides enough evidence to do so."""
    account = account.strip()
    upn = upn.strip()
    domain, separator, _user = account.partition("\\")
    machine = computer_name.strip()

    if separator and domain.casefold() == "microsoftaccount":
        kind = "Microsoft account"
    elif separator and machine and domain.casefold() == machine.casefold() and not upn:
        kind = "Local account"
    elif separator and domain.casefold() == "azuread":
        kind = "Work or school account"
    elif separator and machine and domain.casefold() != machine.casefold() and upn:
        kind = "Work or school account"
    else:
        kind = "Could not determine"

    return AccountInfo(kind, account, upn)


def _run(argv: list[str], timeout: float = 5.0) -> tuple[str, str]:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return "", str(error)
    return (result.stdout or "").strip(), (result.stderr or "").strip()


def detect_account(computer_name: str | None = None) -> AccountInfo:
    """Run the Windows identity commands silently and return a cautious summary."""
    whoami, whoami_error = _run(["whoami"])
    upn, upn_error = _run(["whoami", "/upn"])
    result = classify_account(whoami, upn, computer_name or os.environ.get("COMPUTERNAME", ""))
    errors = [error for error in (whoami_error, upn_error) if error]
    details = "\n".join(errors)
    return AccountInfo(result.account_type, result.account, result.upn, details)
