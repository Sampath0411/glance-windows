// Glance for Windows - grant validation. See GlanceGrant.h for the byte layout.

#include "GlanceGrant.h"

#include <bcrypt.h>
#include <sddl.h>
#include <wtsapi32.h>

#include <cstring>
#include <vector>

#pragma comment(lib, "bcrypt.lib")
#pragma comment(lib, "wtsapi32.lib")
#pragma comment(lib, "advapi32.lib")

namespace glance {
namespace {

constexpr char kMagic[4] = {'G', 'L', 'N', 'C'};
constexpr uint16_t kVersion = 1;

uint16_t ReadU16(const uint8_t* p) {
    return static_cast<uint16_t>(p[0] | (p[1] << 8));
}

uint32_t ReadU32(const uint8_t* p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
}

void WriteU16(uint8_t* p, uint16_t v) {
    p[0] = static_cast<uint8_t>(v & 0xFF);
    p[1] = static_cast<uint8_t>((v >> 8) & 0xFF);
}

void WriteU32(uint8_t* p, uint32_t v) {
    p[0] = static_cast<uint8_t>(v & 0xFF);
    p[1] = static_cast<uint8_t>((v >> 8) & 0xFF);
    p[2] = static_cast<uint8_t>((v >> 16) & 0xFF);
    p[3] = static_cast<uint8_t>((v >> 24) & 0xFF);
}

// key == nullptr means a plain SHA-256; otherwise HMAC-SHA-256.
bool HashData(const uint8_t* key, size_t key_len,
              const void* data, size_t len, uint8_t out[32]) {
    BCRYPT_ALG_HANDLE alg = nullptr;
    const ULONG flags = key ? BCRYPT_ALG_HANDLE_HMAC_FLAG : 0;
    if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, nullptr, flags) < 0) {
        return false;
    }

    BCRYPT_HASH_HANDLE hash = nullptr;
    bool ok = false;
    const PUCHAR key_ptr = const_cast<PUCHAR>(key);
    const ULONG key_bytes = key ? static_cast<ULONG>(key_len) : 0;

    if (BCryptCreateHash(alg, &hash, nullptr, 0, key_ptr, key_bytes, 0) >= 0) {
        if (BCryptHashData(hash, const_cast<PUCHAR>(static_cast<const UCHAR*>(data)),
                           static_cast<ULONG>(len), 0) >= 0 &&
            BCryptFinishHash(hash, out, 32, 0) >= 0) {
            ok = true;
        }
        BCryptDestroyHash(hash);
    }
    BCryptCloseAlgorithmProvider(alg, 0);
    return ok;
}

std::string WideToUtf8(const std::wstring& in) {
    if (in.empty()) return {};
    const int need = WideCharToMultiByte(CP_UTF8, 0, in.c_str(),
                                         static_cast<int>(in.size()),
                                         nullptr, 0, nullptr, nullptr);
    if (need <= 0) return {};
    std::string out(static_cast<size_t>(need), '\0');
    WideCharToMultiByte(CP_UTF8, 0, in.c_str(), static_cast<int>(in.size()),
                        out.data(), need, nullptr, nullptr);
    return out;
}

}  // namespace

const wchar_t* StatusText(GrantStatus status) {
    switch (status) {
        case GrantStatus::Ok: return L"ok";
        case GrantStatus::Missing: return L"no grant file";
        case GrantStatus::TooLarge: return L"grant file too large";
        case GrantStatus::BadSize: return L"grant file wrong size";
        case GrantStatus::BadMagic: return L"not a Glance grant";
        case GrantStatus::BadVersion: return L"unsupported grant version";
        case GrantStatus::BadMac: return L"grant signature invalid";
        case GrantStatus::Expired: return L"grant expired";
        case GrantStatus::NotYetValid: return L"grant issued in the future";
        case GrantStatus::WindowTooLong: return L"grant lifetime too long";
        case GrantStatus::WrongSession: return L"grant from another session";
        case GrantStatus::WrongUser: return L"grant for another account";
        case GrantStatus::KeyUnavailable: return L"grant key unavailable";
        case GrantStatus::ReadError: return L"grant file unreadable";
    }
    return L"unknown";
}

bool ConstantTimeEqual(const uint8_t* a, const uint8_t* b, size_t len) {
    uint8_t diff = 0;
    for (size_t i = 0; i < len; ++i) {
        diff |= static_cast<uint8_t>(a[i] ^ b[i]);
    }
    return diff == 0;
}

bool Sha256(const void* data, size_t len, uint8_t out[32]) {
    return HashData(nullptr, 0, data, len, out);
}

bool HmacSha256(const uint8_t* key, size_t key_len,
                const void* data, size_t len, uint8_t out[32]) {
    if (key == nullptr || key_len == 0) return false;
    return HashData(key, key_len, data, len, out);
}

bool SidHashFromString(const std::wstring& sid, uint8_t out[kSidHashSize]) {
    const std::string utf8 = WideToUtf8(sid);
    if (utf8.empty()) return false;
    uint8_t digest[32];
    if (!Sha256(utf8.data(), utf8.size(), digest)) return false;
    std::memcpy(out, digest, kSidHashSize);
    return true;
}

bool LoadGrantKey(uint8_t out[kKeySize]) {
    DWORD size = kKeySize;
    const LSTATUS rc = RegGetValueW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Glance", L"GrantKey",
                                    RRF_RT_REG_BINARY, nullptr, out, &size);
    return rc == ERROR_SUCCESS && size == kKeySize;
}

GrantStatus VerifyGrantBytes(const uint8_t* buffer, size_t size,
                             const uint8_t key[kKeySize],
                             const std::wstring& expected_sid,
                             uint32_t expected_session,
                             uint64_t now_unix) {
    if (buffer == nullptr) return GrantStatus::ReadError;
    if (size != kGrantFileSize) return GrantStatus::BadSize;
    if (std::memcmp(buffer, kMagic, sizeof(kMagic)) != 0) return GrantStatus::BadMagic;
    if (ReadU16(buffer + 4) != kVersion) return GrantStatus::BadVersion;

    // Signature first: nothing below is trusted until this passes.
    uint8_t mac[32];
    if (!HmacSha256(key, kKeySize, buffer, kGrantBodySize, mac)) {
        return GrantStatus::KeyUnavailable;
    }
    if (!ConstantTimeEqual(mac, buffer + kGrantBodySize, kMacSize)) {
        return GrantStatus::BadMac;
    }

    const uint32_t session_id = ReadU32(buffer + 8);
    const uint32_t issued = ReadU32(buffer + 12);
    const uint32_t expires = ReadU32(buffer + 16);

    if (expires <= issued) return GrantStatus::BadSize;
    if (expires - issued > kMaxGrantLifetimeSeconds) return GrantStatus::WindowTooLong;
    if (now_unix > expires) return GrantStatus::Expired;
    if (issued > now_unix + kClockSkewToleranceSeconds) return GrantStatus::NotYetValid;

    if (session_id != expected_session) return GrantStatus::WrongSession;

    uint8_t want_sid[kSidHashSize];
    if (!SidHashFromString(expected_sid, want_sid)) return GrantStatus::WrongUser;
    if (!ConstantTimeEqual(want_sid, buffer + 36, kSidHashSize)) {
        return GrantStatus::WrongUser;
    }

    return GrantStatus::Ok;
}

GrantStatus VerifyGrantFile(const std::wstring& path,
                            const uint8_t key[kKeySize],
                            const std::wstring& expected_sid,
                            uint32_t expected_session,
                            uint64_t now_unix) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ,
                              FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        const DWORD err = GetLastError();
        return (err == ERROR_FILE_NOT_FOUND || err == ERROR_PATH_NOT_FOUND)
                   ? GrantStatus::Missing
                   : GrantStatus::ReadError;
    }

    // Read one byte more than a grant can be, so anything larger is rejected
    // without ever growing a buffer based on file contents.
    uint8_t buffer[kGrantFileSize + 1];
    DWORD read = 0;
    const BOOL ok = ReadFile(file, buffer, static_cast<DWORD>(sizeof(buffer)), &read, nullptr);
    const DWORD err = GetLastError();
    CloseHandle(file);

    if (!ok) return err == ERROR_FILE_NOT_FOUND ? GrantStatus::Missing : GrantStatus::ReadError;
    if (read > kGrantFileSize) return GrantStatus::TooLarge;
    if (read != kGrantFileSize) return GrantStatus::BadSize;

    return VerifyGrantBytes(buffer, read, key, expected_sid, expected_session, now_unix);
}

bool GrantIsFresh(const uint8_t grant[kGrantFileSize], uint64_t now_unix) {
    if (grant == nullptr) return false;
    if (std::memcmp(grant, kMagic, sizeof(kMagic)) != 0) return false;
    if (ReadU16(grant + 4) != kVersion) return false;

    const uint32_t issued = ReadU32(grant + 12);
    const uint32_t expires = ReadU32(grant + 16);
    if (expires < issued) return false;
    if (expires - issued > kMaxGrantLifetimeSeconds) return false;

    return now_unix <= static_cast<uint64_t>(expires) + kClockSkewToleranceSeconds;
}

GrantStatus ReadGrantFile(const std::wstring& path, uint8_t out[kGrantFileSize]) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ,
                              FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        const DWORD err = GetLastError();
        return (err == ERROR_FILE_NOT_FOUND || err == ERROR_PATH_NOT_FOUND)
                   ? GrantStatus::Missing
                   : GrantStatus::ReadError;
    }

    uint8_t buffer[kGrantFileSize + 1];
    DWORD read = 0;
    const BOOL ok = ReadFile(file, buffer, static_cast<DWORD>(sizeof(buffer)), &read, nullptr);
    const DWORD err = GetLastError();
    CloseHandle(file);

    if (!ok) return err == ERROR_FILE_NOT_FOUND ? GrantStatus::Missing : GrantStatus::ReadError;
    if (read > kGrantFileSize) return GrantStatus::TooLarge;
    if (read != kGrantFileSize) return GrantStatus::BadSize;

    if (out != nullptr) std::memcpy(out, buffer, kGrantFileSize);
    return GrantStatus::Ok;
}

GrantStatus ConsumeGrantFile(const std::wstring& path,
                             const uint8_t key[kKeySize],
                             const std::wstring& expected_sid,
                             uint32_t expected_session,
                             uint64_t now_unix) {
    uint8_t buffer[kGrantFileSize];
    const GrantStatus read = ReadGrantFile(path, buffer);

    // Consume first: the grant must not survive a failed attempt.
    DeleteFileW(path.c_str());

    if (read != GrantStatus::Ok) return read;
    return VerifyGrantBytes(buffer, kGrantFileSize, key, expected_sid,
                            expected_session, now_unix);
}

bool BuildGrant(const uint8_t key[kKeySize],
                const std::wstring& sid,
                uint32_t session_id,
                uint32_t issued_unix,
                uint32_t expires_unix,
                const uint8_t nonce[kNonceSize],
                uint8_t out[kGrantFileSize],
                bool* hmac_ok) {
    if (out == nullptr) return false;
    std::memset(out, 0, kGrantFileSize);
    std::memcpy(out, kMagic, sizeof(kMagic));
    WriteU16(out + 4, kVersion);
    WriteU16(out + 6, 0);
    WriteU32(out + 8, session_id);
    WriteU32(out + 12, issued_unix);
    WriteU32(out + 16, expires_unix);
    if (nonce) std::memcpy(out + 20, nonce, kNonceSize);

    uint8_t sid_hash[kSidHashSize];
    if (!SidHashFromString(sid, sid_hash)) return false;
    std::memcpy(out + 36, sid_hash, kSidHashSize);

    uint8_t mac[32];
    if (!HmacSha256(key, kKeySize, out, kGrantBodySize, mac)) return false;
    std::memcpy(out + kGrantBodySize, mac, kMacSize);

    if (hmac_ok) *hmac_ok = true;
    return true;
}

namespace {

thread_local DWORD g_last_sid_error = 0;

// WTSQueryUserToken needs SeTcbPrivilege *enabled*, and Windows hands SYSTEM a token
// where it is present but disabled. Without this the query fails even though the
// caller looks privileged enough.
void EnableTcbPrivilege() {
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &token)) {
        return;
    }
    LUID luid{};
    if (LookupPrivilegeValueW(nullptr, SE_TCB_NAME, &luid)) {
        TOKEN_PRIVILEGES privileges{};
        privileges.PrivilegeCount = 1;
        privileges.Privileges[0].Luid = luid;
        privileges.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
        SetLastError(ERROR_SUCCESS);
        AdjustTokenPrivileges(token, FALSE, &privileges, sizeof(privileges), nullptr, nullptr);
    }
    CloseHandle(token);
}

std::wstring SidStringFromToken(HANDLE token) {
    DWORD needed = 0;
    GetTokenInformation(token, TokenUser, nullptr, 0, &needed);
    if (GetLastError() != ERROR_INSUFFICIENT_BUFFER || needed == 0) {
        g_last_sid_error = GetLastError();
        return L"";
    }

    std::vector<uint8_t> buffer(needed);
    if (!GetTokenInformation(token, TokenUser, buffer.data(), needed, &needed)) {
        g_last_sid_error = GetLastError();
        return L"";
    }

    auto* user = reinterpret_cast<TOKEN_USER*>(buffer.data());
    LPWSTR sid_string = nullptr;
    if (!ConvertSidToStringSidW(user->User.Sid, &sid_string)) {
        g_last_sid_error = GetLastError();
        return L"";
    }

    std::wstring result(sid_string);
    LocalFree(sid_string);
    g_last_sid_error = 0;
    return result;
}

std::wstring SessionText(uint32_t session_id, WTS_INFO_CLASS info) {
    LPWSTR value = nullptr;
    DWORD bytes = 0;
    if (!WTSQuerySessionInformationW(WTS_CURRENT_SERVER_HANDLE, session_id, info, &value,
                                     &bytes)) {
        g_last_sid_error = GetLastError();
        return L"";
    }
    std::wstring result(value != nullptr ? value : L"");
    WTSFreeMemory(value);
    return result;
}

// Needs no privilege at all: ask the session for its user name and look the account up.
std::wstring SidStringFromSessionName(uint32_t session_id) {
    const std::wstring user = SessionText(session_id, WTSUserName);
    if (user.empty()) {
        if (g_last_sid_error == 0) g_last_sid_error = ERROR_NO_SUCH_USER;
        return L"";
    }
    const std::wstring domain = SessionText(session_id, WTSDomainName);

    uint8_t sid_buffer[SECURITY_MAX_SID_SIZE];
    DWORD sid_size = sizeof(sid_buffer);
    wchar_t domain_buffer[256];
    DWORD domain_size = 256;
    SID_NAME_USE use = SidTypeUnknown;

    if (!LookupAccountNameW(domain.empty() ? nullptr : domain.c_str(), user.c_str(),
                            sid_buffer, &sid_size, domain_buffer, &domain_size, &use)) {
        g_last_sid_error = GetLastError();
        return L"";
    }

    LPWSTR sid_string = nullptr;
    if (!ConvertSidToStringSidW(sid_buffer, &sid_string)) {
        g_last_sid_error = GetLastError();
        return L"";
    }

    std::wstring result(sid_string);
    LocalFree(sid_string);
    g_last_sid_error = 0;
    return result;
}

}  // namespace

DWORD LastSidError() { return g_last_sid_error; }

std::wstring SidStringForSession(uint32_t session_id) {
    g_last_sid_error = 0;

    EnableTcbPrivilege();
    HANDLE token = nullptr;
    if (WTSQueryUserToken(session_id, &token)) {
        const std::wstring sid = SidStringFromToken(token);
        CloseHandle(token);
        if (!sid.empty()) return sid;
    } else {
        g_last_sid_error = GetLastError();
    }

    // Two independent routes: the session token, then the session's account name.
    // The second one is the reason a locked workstation cannot dead-end here.
    return SidStringFromSessionName(session_id);
}

uint32_t CurrentSessionId() {
    DWORD session = 0;
    if (!ProcessIdToSessionId(GetCurrentProcessId(), &session)) return 0;
    return static_cast<uint32_t>(session);
}

uint64_t NowUnix() {
    FILETIME ft;
    GetSystemTimeAsFileTime(&ft);
    ULARGE_INTEGER value;
    value.LowPart = ft.dwLowDateTime;
    value.HighPart = ft.dwHighDateTime;
    // 100-ns ticks since 1601-01-01; 11644473600s between then and 1970-01-01.
    return (value.QuadPart / 10000000ULL) - 11644473600ULL;
}

}  // namespace glance
