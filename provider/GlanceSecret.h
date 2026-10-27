// Glance for Windows - the stored credential.
//
// Windows will not unlock on an assertion. The provider must hand LogonUI a real
// password, so the account password has to live somewhere the logon process can read
// it. This stores it DPAPI-encrypted at machine scope under HKLM\SOFTWARE\Glance.
//
// LIMITATION, stated rather than buried: DPAPI machine scope means any process running
// on this machine can decrypt the blob. It defends the value at rest (a copied registry
// hive is useless without the machine's DPAPI keys) but not a compromised session, and
// not an attacker who has both the hive and SYSTEM-level access. A service-held secret
// with a narrower ACL would be better; that is the Phase 3b hardening item.
//
// Nothing here is called by the credential provider on a path that can block login:
// only GetSerialization reads it, and that runs after the user has chosen the tile.

#pragma once

#include <windows.h>
#include <string>

namespace glance {

constexpr wchar_t kRegRoot[] = L"SOFTWARE\\Glance";

struct StoredCredential {
    std::wstring domain;    // may be "." for a local account
    std::wstring user;
    std::wstring password;
};

// Writes the account name and the DPAPI-protected password. Needs write access to
// HKLM\SOFTWARE\Glance, i.e. elevation, which is correct: enrolling your password is
// an administrative act.
bool StoreCredential(const std::wstring& account,   // "DOMAIN\\user" or "user"
                     const std::wstring& password,
                     std::wstring* error);

// Reads and decrypts. Called from the provider (SYSTEM) and from a test harness.
bool LoadCredential(StoredCredential* out, std::wstring* error);

// Removes both values. Returns true if there is nothing left to read afterwards.
bool ClearCredential(std::wstring* error);

// True when a credential is present and decrypts. Does not expose the password.
bool CredentialPresent();

}  // namespace glance
