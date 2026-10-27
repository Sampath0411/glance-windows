// Glance for Windows - building the unlock serialization. See GlancePack.h.

#include "GlancePack.h"

#include <ntsecapi.h>

#include <cstring>

namespace glance {

namespace {

constexpr DWORD kStructBytes = static_cast<DWORD>(sizeof(KERB_INTERACTIVE_UNLOCK_LOGON));

HRESULT PackUnlockLogonImpl(const std::wstring& domain,
                            const std::wstring& user,
                            const std::wstring& password,
                            PackScenario scenario,
                            CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* serialization) {
    const DWORD domain_bytes = static_cast<DWORD>(domain.size() * sizeof(wchar_t));
    const DWORD user_bytes = static_cast<DWORD>(user.size() * sizeof(wchar_t));
    const DWORD pass_bytes = static_cast<DWORD>(password.size() * sizeof(wchar_t));
    const DWORD total = kStructBytes + domain_bytes + user_bytes + pass_bytes;

    auto* buffer = static_cast<BYTE*>(CoTaskMemAlloc(total));
    if (buffer == nullptr) return E_OUTOFMEMORY;
    ZeroMemory(buffer, total);

    auto* unlock = reinterpret_cast<KERB_INTERACTIVE_UNLOCK_LOGON*>(buffer);
    unlock->Logon.MessageType = scenario == PackScenario::Unlock
                                    ? KerbWorkstationUnlockLogon
                                    : KerbInteractiveLogon;
    unlock->LogonId.LowPart = 0;
    unlock->LogonId.HighPart = 0;

    BYTE* cursor = buffer + kStructBytes;
    auto place = [&cursor, buffer](UNICODE_STRING& target, const std::wstring& text) {
        const DWORD bytes = static_cast<DWORD>(text.size() * sizeof(wchar_t));
        target.Length = static_cast<USHORT>(bytes);
        // One spare byte for a terminator that deliberately is not written: this is what
        // the sample packers emit, and it is what LSA expects.
        target.MaximumLength = static_cast<USHORT>(bytes + sizeof(wchar_t));
        // Byte offset from the start of the buffer, not an address.
        target.Buffer = reinterpret_cast<PWSTR>(static_cast<ULONG_PTR>(cursor - buffer));
        if (bytes != 0) std::memcpy(cursor, text.data(), bytes);
        cursor += bytes;
    };

    place(unlock->Logon.LogonDomainName, domain);
    place(unlock->Logon.UserName, user);
    place(unlock->Logon.Password, password);

    serialization->rgbSerialization = buffer;
    serialization->cbSerialization = total;
    return S_OK;
}

bool ReadString(const BYTE* base,
                DWORD cb,
                const UNICODE_STRING& value,
                std::wstring* out) {
    if (out == nullptr) return false;
    if (value.Length % sizeof(wchar_t) != 0) return false;
    if (value.MaximumLength < value.Length) return false;

    const ULONG_PTR offset = reinterpret_cast<ULONG_PTR>(value.Buffer);
    if (value.Length == 0) {
        if (offset != 0 && offset > cb) return false;
        out->clear();
        return true;
    }
    if (offset < kStructBytes) return false;
    if (offset > cb) return false;
    if (value.Length > cb - offset) return false;

    out->assign(reinterpret_cast<const wchar_t*>(base + offset), value.Length / sizeof(wchar_t));
    return true;
}

}  // namespace

HRESULT PackUnlockLogon(const std::wstring& domain,
                        const std::wstring& user,
                        const std::wstring& password,
                        PackScenario scenario,
                        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* serialization) {
    if (serialization == nullptr) return E_INVALIDARG;
    return PackUnlockLogonImpl(domain, user, password, scenario, serialization);
}

bool InspectUnlockLogon(const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION& serialization,
                        std::wstring* domain,
                        std::wstring* user,
                        std::wstring* password,
                        uint32_t* message_type) {
    if (serialization.rgbSerialization == nullptr) return false;
    if (serialization.cbSerialization < kStructBytes) return false;

    const BYTE* base = reinterpret_cast<const BYTE*>(serialization.rgbSerialization);
    const DWORD cb = serialization.cbSerialization;

    auto* unlock = reinterpret_cast<const KERB_INTERACTIVE_UNLOCK_LOGON*>(base);
    const uint32_t type = static_cast<uint32_t>(unlock->Logon.MessageType);
    if (type != static_cast<uint32_t>(KerbInteractiveLogon) &&
        type != static_cast<uint32_t>(KerbWorkstationUnlockLogon)) {
        return false;
    }

    if (!ReadString(base, cb, unlock->Logon.LogonDomainName, domain)) return false;
    if (!ReadString(base, cb, unlock->Logon.UserName, user)) return false;
    if (!ReadString(base, cb, unlock->Logon.Password, password)) return false;

    if (message_type != nullptr) *message_type = type;
    return true;
}

}  // namespace glance
