// Glance for Windows - building the unlock serialization.
//
// Separated from the provider so it can be tested without LogonUI. A malformed
// KERB_INTERACTIVE_UNLOCK_LOGON does not crash anything - it just fails to
// authenticate with "The parameter is incorrect", which looks exactly like a broken
// password and is miserable to debug from the lock screen. So it gets its own file
// and its own assertions.
//
// Two details here are not negotiable, because Winlogon and LSA read the buffer
// differently from a normal in-process structure:
//
//   * every UNICODE_STRING.Buffer is a BYTE OFFSET from the start of the buffer,
//     not an address. Handing over real pointers yields STATUS_INVALID_PARAMETER.
//   * the strings are NOT null terminated and are not counted in MaximumLength's
//     spare byte; cbSerialization is exactly struct + sum of Length.

#pragma once

#include <windows.h>
#include <credentialprovider.h>

#include <cstdint>
#include <string>

namespace glance {

// The message type is part of the credential, not a hint: a sign-in submitted as an
// unlock (or the reverse) is rejected by the authentication package.
enum class PackScenario {
    Logon,   // KerbInteractiveLogon       - the sign-in screen
    Unlock,  // KerbWorkstationUnlockLogon - a locked workstation
};

// Allocates the serialization with CoTaskMemAlloc; the caller (LogonUI) owns it.
HRESULT PackUnlockLogon(const std::wstring& domain,
                        const std::wstring& user,
                        const std::wstring& password,
                        PackScenario scenario,
                        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* serialization);

// Walks a serialization produced by PackUnlockLogon and recovers the three strings.
// Rejects any buffer whose offsets do not land inside the allocation, any odd byte
// length, any truncated string and any unknown message type. Returns false on the
// first inconsistency rather than reading past the end. message_type may be null.
bool InspectUnlockLogon(const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION& serialization,
                        std::wstring* domain,
                        std::wstring* user,
                        std::wstring* password,
                        uint32_t* message_type = nullptr);

}  // namespace glance
