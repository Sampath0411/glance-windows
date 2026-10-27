// Standalone harness for the grant format. Deliberately has no COM and no
// LogonUI involvement: this is what gets tested before any of that exists.
//
//   grant_tool create --out grant.bin --sid S-1-5-21-... --session 1 --ttl 10 [--key-hex HEX]
//   grant_tool verify --path grant.bin --sid S-1-5-21-... --session 1 [--key-hex HEX]
//   grant_tool keygen
//   grant_tool sid
//
// Exit code 0 only when the grant verifies.

#include "GlanceGrant.h"
#include "GlancePack.h"
#include "GlanceSecret.h"

#include <bcrypt.h>
#include <ntsecapi.h>

#include <cstdio>
#include <cstring>
#include <cwchar>
#include <string>
#include <vector>

#pragma comment(lib, "secur32.lib")

namespace {

std::vector<uint8_t> ParseHex(const std::string& text) {
    std::vector<uint8_t> out;
    int high = -1;
    for (char c : text) {
        int v = -1;
        if (c >= '0' && c <= '9') v = c - '0';
        else if (c >= 'a' && c <= 'f') v = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') v = c - 'A' + 10;
        else continue;  // let separators through
        if (high < 0) {
            high = v;
        } else {
            out.push_back(static_cast<uint8_t>((high << 4) | v));
            high = -1;
        }
    }
    return out;
}

void PrintKeyHex(const uint8_t* key, size_t len) {
    for (size_t i = 0; i < len; ++i) std::printf("%02x", key[i]);
    std::printf("\n");
}

bool ArgValue(int argc, char** argv, const char* name, std::string* out) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::strcmp(argv[i], name) == 0) {
            *out = argv[i + 1];
            return true;
        }
    }
    return false;
}

bool HasFlag(int argc, char** argv, const char* name) {
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], name) == 0) return true;
    }
    return false;
}

// Scripted secret input: the caller sets an environment variable and clears it after.
// It never appears in argv, in a log, or on disk.
std::wstring PasswordFromEnv(const std::string& env_name) {
    if (env_name.empty()) return L"";
    const std::wstring wide_name(env_name.begin(), env_name.end());
    const DWORD needed = GetEnvironmentVariableW(wide_name.c_str(), nullptr, 0);
    if (needed <= 1) return L"";

    std::wstring value(needed, L'\0');
    const DWORD written = GetEnvironmentVariableW(wide_name.c_str(), value.data(), needed);
    if (written == 0 || written >= needed) return L"";
    value.resize(written);
    return value;
}

void SplitAccount(const std::wstring& account, std::wstring* domain, std::wstring* user) {
    const size_t slash = account.find(L'\\');
    if (slash == std::wstring::npos) {
        domain->clear();
        *user = account;
    } else {
        *domain = account.substr(0, slash);
        *user = account.substr(slash + 1);
    }
}

// Scripted secret input from a file. Used by the desktop app when it has to elevate to
// store the credential: a pipe cannot cross ShellExecute, and argv would be visible in
// the process list. The file is deleted before the bytes are parsed.
std::wstring PasswordFromFile(const std::string& path) {
    if (path.empty()) return L"";
    const std::wstring wide_path(path.begin(), path.end());

    HANDLE file = CreateFileW(wide_path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return L"";
    DeleteFileW(wide_path.c_str());

    char bytes[512] = {};
    DWORD read = 0;
    const bool ok = ReadFile(file, bytes, sizeof(bytes) - 1, &read, nullptr) != FALSE;
    CloseHandle(file);
    if (!ok || read == 0) return L"";

    std::string text(bytes, read);
    while (!text.empty() && (text.back() == '\r' || text.back() == '\n' || text.back() == ' ')) {
        text.pop_back();
    }
    const int needed = MultiByteToWideChar(CP_UTF8, 0, text.data(),
                                           static_cast<int>(text.size()), nullptr, 0);
    std::wstring value(needed, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()),
                        value.data(), needed);
    SecureZeroMemory(bytes, sizeof(bytes));
    SecureZeroMemory(text.data(), text.size());
    return value;
}

std::wstring PasswordFromStdin() {
    const HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    if (input == nullptr || input == INVALID_HANDLE_VALUE) return L"";

    std::string text;
    char chunk[256];
    DWORD read = 0;
    while (ReadFile(input, chunk, sizeof(chunk), &read, nullptr) && read > 0) {
        text.append(chunk, read);
        if (text.size() > 4096) break;
    }
    while (!text.empty() && (text.back() == '\r' || text.back() == '\n')) text.pop_back();

    const int needed = MultiByteToWideChar(CP_UTF8, 0, text.data(),
                                           static_cast<int>(text.size()), nullptr, 0);
    std::wstring value(needed, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()),
                        value.data(), needed);
    SecureZeroMemory(text.data(), text.size());
    return value;
}

std::wstring PromptPassword(const std::wstring& account);

std::wstring AcquirePassword(int argc, char** argv, const std::wstring& account) {
    std::string env_name, file_path;
    if (ArgValue(argc, argv, "--password-env", &env_name)) return PasswordFromEnv(env_name);
    if (ArgValue(argc, argv, "--password-file", &file_path)) return PasswordFromFile(file_path);
    if (HasFlag(argc, argv, "--password-stdin")) return PasswordFromStdin();
    return PromptPassword(account);
}

bool EnableTcb() {
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                          &token)) {
        return false;
    }
    LUID luid{};
    bool enabled = false;
    if (LookupPrivilegeValueW(nullptr, SE_TCB_NAME, &luid)) {
        TOKEN_PRIVILEGES privileges{};
        privileges.PrivilegeCount = 1;
        privileges.Privileges[0].Luid = luid;
        privileges.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
        SetLastError(ERROR_SUCCESS);
        enabled = AdjustTokenPrivileges(token, FALSE, &privileges, sizeof(privileges),
                                        nullptr, nullptr) != FALSE &&
                  GetLastError() == ERROR_SUCCESS;
    }
    CloseHandle(token);
    return enabled;
}

struct Probe {
    NTSTATUS status = 0;
    NTSTATUS substatus = 0;
    bool trusted = false;
};

// Asks LSA the same question the lock screen will ask, with the same serialization.
//
// A trusted handle (SeTcbPrivilege, i.e. elevated) allows an interactive logon, which is
// the only type CloudAP answers for a Microsoft account: from an untrusted handle the
// network logon gets STATUS_LOGON_FAILURE even for a correct password. When the
// privilege is unavailable the probe still runs and reports which mode it used, so a
// rejection on an untrusted handle is not mistaken for a wrong password.
bool ProbeCredential(const std::wstring& domain, const std::wstring& user,
                     const std::wstring& password, Probe* out) {
    LSA_STRING process_name{};
    const char kName[] = "Glance";
    process_name.Buffer = const_cast<char*>(kName);
    process_name.Length = static_cast<USHORT>(std::strlen(kName));
    process_name.MaximumLength = static_cast<USHORT>(process_name.Length + 1);

    ULONG mode = 0;
    LSA_HANDLE lsa = nullptr;
    bool trusted = false;
    if (EnableTcb()) {
        trusted = LsaRegisterLogonProcess(&process_name, &lsa, &mode) == 0;
    }
    if (!trusted) {
        if (LsaConnectUntrusted(&lsa) != 0) return false;
    }

    const bool microsoft_account = _wcsicmp(domain.c_str(), L"MicrosoftAccount") == 0;
    const char* package_name =
        microsoft_account ? "Negotiate" : "MICROSOFT_AUTHENTICATION_PACKAGE_V1_0";

    LSA_STRING name{};
    name.Buffer = const_cast<char*>(package_name);
    name.Length = static_cast<USHORT>(std::strlen(package_name));
    name.MaximumLength = static_cast<USHORT>(name.Length + 1);

    ULONG package = 0;
    if (LsaLookupAuthenticationPackage(lsa, &name, &package) != 0) {
        if (!trusted) LsaClose(lsa);
        return false;
    }

    CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION packed{};
    const HRESULT packed_ok =
        glance::PackUnlockLogon(domain, user, password, glance::PackScenario::Logon, &packed);
    if (FAILED(packed_ok)) {
        if (!trusted) LsaClose(lsa);
        return false;
    }

    TOKEN_SOURCE source{};
    std::memcpy(source.SourceName, "Glance", 6);
    AllocateLocallyUniqueId(&source.SourceIdentifier);

    LSA_STRING origin{};
    origin.Buffer = const_cast<char*>(kName);
    origin.Length = static_cast<USHORT>(std::strlen(kName));
    origin.MaximumLength = static_cast<USHORT>(origin.Length + 1);

    PVOID profile = nullptr;
    ULONG profile_length = 0;
    LUID logon_id{};
    HANDLE token = nullptr;
    QUOTA_LIMITS quotas{};
    NTSTATUS substatus = 0;
    const SECURITY_LOGON_TYPE logon_type = trusted ? Interactive : Network;

    out->status =
        LsaLogonUser(lsa, &origin, logon_type, package, packed.rgbSerialization,
                     packed.cbSerialization, nullptr, &source, &profile, &profile_length,
                     &logon_id, &token, &quotas, &substatus);
    out->substatus = substatus;
    out->trusted = trusted;

    if (token != nullptr) CloseHandle(token);
    if (profile != nullptr) LsaFreeReturnBuffer(profile);
    CoTaskMemFree(packed.rgbSerialization);
    if (!trusted) LsaClose(lsa);
    return true;
}

const char* ProbeVerdict(const Probe& probe) {
    if (probe.status == 0) return "ok";
    switch (static_cast<unsigned long>(probe.status)) {
        case 0xC000006D:  // STATUS_LOGON_FAILURE
        case 0xC000006A:  // STATUS_WRONG_PASSWORD
        case 0xC0000064:  // STATUS_NO_SUCH_USER
        case 0xC0000234:  // STATUS_ACCOUNT_LOCKED_OUT
            return "rejected";
        case 0xC000000D:  // STATUS_INVALID_PARAMETER - serialization rejected
            return "invalid";
        case 0xC000006E:  // STATUS_ACCOUNT_RESTRICTION
        case 0xC0000072:  // STATUS_ACCOUNT_DISABLED
            return "blocked";
        default:
            return "error";
    }
}

bool LoadKey(bool have_hex, const std::string& hex, uint8_t key[glance::kKeySize]) {
    if (have_hex) {
        auto bytes = ParseHex(hex);
        if (bytes.size() != glance::kKeySize) {
            std::printf("key must be %zu bytes, got %zu\n", glance::kKeySize, bytes.size());
            return false;
        }
        std::memcpy(key, bytes.data(), glance::kKeySize);
        return true;
    }
    if (!glance::LoadGrantKey(key)) {
        std::printf("could not read HKLM\\SOFTWARE\\Glance\\GrantKey\n");
        return false;
    }
    return true;
}

// Reads a password without echoing it. The console mode is restored even on the
// failure path, and the buffer is wiped before returning.
std::wstring PromptPassword(const std::wstring& account) {
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    DWORD mode = 0;
    const bool console = input != INVALID_HANDLE_VALUE && GetConsoleMode(input, &mode) != 0;
    if (console) SetConsoleMode(input, mode & ~ENABLE_ECHO_INPUT);

    std::printf("Windows password for %ls: ", account.c_str());
    std::fflush(stdout);

    wchar_t buffer[256] = {};
    DWORD read = 0;
    if (!ReadConsoleW(input, buffer, 255, &read, nullptr)) read = 0;

    if (console) {
        SetConsoleMode(input, mode);
        std::printf("\n");
    }

    std::wstring password(buffer, read);
    while (!password.empty() &&
           (password.back() == L'\r' || password.back() == L'\n')) {
        password.pop_back();
    }
    SecureZeroMemory(buffer, sizeof(buffer));
    return password;
}

bool GrantKeyConfigured() {
    DWORD size = 0;
    return RegGetValueW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Glance", L"GrantKey",
                        RRF_RT_REG_BINARY, nullptr, nullptr, &size) == ERROR_SUCCESS &&
           size == glance::kKeySize;
}

int Usage() {
    std::printf(
        "grant_tool init                     create the grant key + data directory (admin)\n"
        "grant_tool enroll --account DOMAIN\\\\user [--password-env VAR | --password-file PATH |\n"
        "                    --password-stdin] [--verify]\n"
        "                                   store the Windows password (admin); --verify asks\n"
        "                                   LSA first and refuses to store a rejected one\n"
        "grant_tool check --account DOMAIN\\\\user [--password-env VAR | --password-file PATH |\n"
        "                  --password-stdin]\n"
        "                                   verify a password without storing it\n"
        "grant_tool status                   show whether a credential is stored\n"
        "grant_tool clear                    forget the stored credential\n"
        "grant_tool create --out FILE --sid SID --session N --ttl SEC [--key-hex HEX]\n"
        "grant_tool verify --path FILE --sid SID --session N [--key-hex HEX]\n"
        "grant_tool keygen                   print a new 32-byte key\n"
        "grant_tool sid                      print the current session's SID\n");
    return 2;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) return Usage();
    const std::string command = argv[1];

    std::string hex, sid, path, out_path, session_text, ttl_text;
    const bool have_hex = ArgValue(argc, argv, "--key-hex", &hex);
    ArgValue(argc, argv, "--sid", &sid);
    ArgValue(argc, argv, "--path", &path);
    ArgValue(argc, argv, "--out", &out_path);
    ArgValue(argc, argv, "--session", &session_text);
    ArgValue(argc, argv, "--ttl", &ttl_text);

    if (command == "keygen") {
        uint8_t key[glance::kKeySize];
        if (BCryptGenRandom(nullptr, key, sizeof(key), BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0) {
            std::printf("BCryptGenRandom failed\n");
            return 1;
        }
        PrintKeyHex(key, sizeof(key));
        return 0;
    }

    if (command == "sid") {
        const uint32_t session = glance::CurrentSessionId();
        const std::wstring value = glance::SidStringForSession(session);
        std::printf("session %u sid %ls\n", session, value.empty() ? L"(unavailable)" : value.c_str());
        return value.empty() ? 1 : 0;
    }

    if (command == "init") {
        uint8_t key[glance::kKeySize];
        if (BCryptGenRandom(nullptr, key, sizeof(key), BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0) {
            std::printf("BCryptGenRandom failed\n");
            return 1;
        }

        HKEY handle = nullptr;
        if (RegCreateKeyExW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Glance", 0, nullptr, 0,
                            KEY_WRITE, nullptr, &handle, nullptr) != ERROR_SUCCESS) {
            std::printf("cannot create HKLM\\SOFTWARE\\Glance - run this elevated\n");
            return 1;
        }
        const LSTATUS rc = RegSetValueExW(handle, L"GrantKey", 0, REG_BINARY, key,
                                          static_cast<DWORD>(sizeof(key)));
        RegCloseKey(handle);
        if (rc != ERROR_SUCCESS) {
            std::printf("cannot write GrantKey (error %ld)\n", rc);
            return 1;
        }

        if (!CreateDirectoryW(L"C:\\ProgramData\\Glance", nullptr) &&
            GetLastError() != ERROR_ALREADY_EXISTS) {
            std::printf("warning: could not create C:\\ProgramData\\Glance (error %lu)\n",
                        GetLastError());
        }

        std::printf("grant key written to HKLM\\SOFTWARE\\Glance\\GrantKey\n");
        std::printf("next: grant_tool enroll --account %ls\n", L"DOMAIN\\user");
        return 0;
    }

    if (command == "status") {
        std::printf("grant key        : %s\n",
                    GrantKeyConfigured() ? "configured" : "MISSING (run init)");
        glance::StoredCredential stored;
        std::wstring error;
        const bool present = glance::CredentialPresent() &&
                             glance::LoadCredential(&stored, &error);
        std::printf("stored credential: %s\n", present ? "present" : "absent");
        if (present) {
            std::printf("account          : %ls\\%ls\n",
                        stored.domain.empty() ? L"." : stored.domain.c_str(),
                        stored.user.c_str());
        }
        const DWORD attributes = GetFileAttributesW(L"C:\\ProgramData\\Glance");
        std::printf("data directory   : %s\n",
                    attributes != INVALID_FILE_ATTRIBUTES ? "present" : "missing");
        return 0;
    }

    if (command == "clear") {
        std::wstring error;
        if (!glance::ClearCredential(&error)) {
            std::printf("%ls\n", error.c_str());
            return 1;
        }
        std::printf("stored credential cleared\n");
        return 0;
    }

    if (command == "check") {
        std::string account_text;
        ArgValue(argc, argv, "--account", &account_text);
        if (account_text.empty()) {
            std::printf("check needs --account DOMAIN\\user\n");
            return 2;
        }

        std::wstring domain, user;
        SplitAccount(std::wstring(account_text.begin(), account_text.end()), &domain, &user);

        std::wstring password = AcquirePassword(argc, argv, user);
        if (password.empty()) {
            std::printf("verify=error reason=no-password\n");
            return 3;
        }

        Probe probe;
        const bool ran = ProbeCredential(domain, user, password, &probe);
        SecureZeroMemory(password.data(), password.size() * sizeof(wchar_t));
        if (!ran) {
            std::printf("verify=error reason=lsa-unavailable\n");
            return 3;
        }

        std::printf("verify=%s status=0x%08lX sub=0x%08lX mode=%s\n", ProbeVerdict(probe),
                    static_cast<unsigned long>(probe.status),
                    static_cast<unsigned long>(probe.substatus),
                    probe.trusted ? "trusted" : "untrusted");
        return std::strcmp(ProbeVerdict(probe), "ok") == 0 ? 0 : 1;
    }

    if (command == "enroll") {
        std::string account_text;
        ArgValue(argc, argv, "--account", &account_text);
        if (account_text.empty()) {
            std::printf("enroll needs --account DOMAIN\\user (whoami prints the form)\n");
            return 2;
        }

        const std::wstring account(account_text.begin(), account_text.end());
        std::wstring domain, user;
        SplitAccount(account, &domain, &user);

        std::wstring password = AcquirePassword(argc, argv, user);
        if (password.empty()) {
            std::printf("refusing to store an empty password\n");
            return 1;
        }

        // Verify before storing. A wrong password here is a dialog, not a lock screen that
        // says "the user name and password are incorrect" hours later.
        if (HasFlag(argc, argv, "--verify")) {
            Probe probe;
            const bool ran = ProbeCredential(domain, user, password, &probe);
            if (!ran) {
                SecureZeroMemory(password.data(), password.size() * sizeof(wchar_t));
                std::printf("verify=error reason=lsa-unavailable\n");
                return 3;
            }
            const char* verdict = ProbeVerdict(probe);
            if (std::strcmp(verdict, "ok") != 0) {
                SecureZeroMemory(password.data(), password.size() * sizeof(wchar_t));
                std::printf("verify=%s status=0x%08lX sub=0x%08lX mode=%s\n", verdict,
                            static_cast<unsigned long>(probe.status),
                            static_cast<unsigned long>(probe.substatus),
                            probe.trusted ? "trusted" : "untrusted");
                std::printf("not stored\n");
                return 1;
            }
            std::printf("verify=ok mode=%s\n", probe.trusted ? "trusted" : "untrusted");
        }

        std::wstring error;
        const bool ok = glance::StoreCredential(account, password, &error);
        SecureZeroMemory(password.data(), password.size() * sizeof(wchar_t));
        if (!ok) {
            std::printf("%ls\n", error.c_str());
            return 1;
        }

        std::printf("stored credential for %ls (DPAPI machine scope)\n", account.c_str());
        return 0;
    }

    uint8_t key[glance::kKeySize];
    if (!LoadKey(have_hex, hex, key)) return 1;

    uint32_t session = 0;
    if (!session_text.empty()) session = static_cast<uint32_t>(std::stoul(session_text));

    if (command == "create") {
        if (out_path.empty() || sid.empty()) return Usage();

        uint32_t ttl = 10;
        if (!ttl_text.empty()) ttl = static_cast<uint32_t>(std::stoul(ttl_text));

        const uint64_t now = glance::NowUnix();
        uint8_t nonce[glance::kNonceSize];
        if (BCryptGenRandom(nullptr, nonce, sizeof(nonce), BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0) {
            std::printf("nonce generation failed\n");
            return 1;
        }

        std::wstring wide_sid(sid.begin(), sid.end());
        uint8_t grant[glance::kGrantFileSize];
        bool mac_ok = false;
        if (!glance::BuildGrant(key, wide_sid, session, static_cast<uint32_t>(now),
                                static_cast<uint32_t>(now) + ttl, nonce, grant, &mac_ok)) {
            std::printf("build failed\n");
            return 1;
        }

        std::wstring wide_path(out_path.begin(), out_path.end());
        HANDLE file = CreateFileW(wide_path.c_str(), GENERIC_WRITE, 0, nullptr,
                                  CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE) {
            std::printf("cannot write %s (error %lu)\n", out_path.c_str(), GetLastError());
            return 1;
        }
        DWORD written = 0;
        const BOOL ok = WriteFile(file, grant, static_cast<DWORD>(sizeof(grant)), &written, nullptr);
        CloseHandle(file);
        if (!ok || written != sizeof(grant)) {
            std::printf("short write\n");
            return 1;
        }
        std::printf("wrote %s  (%zu bytes, ttl %u s, hmac %s)\n", out_path.c_str(),
                    sizeof(grant), ttl, mac_ok ? "ok" : "FAILED");
        return 0;
    }

    if (command == "verify") {
        if (path.empty() || sid.empty()) return Usage();
        std::wstring wide_sid(sid.begin(), sid.end());
        std::wstring wide_path(path.begin(), path.end());
        const glance::GrantStatus status =
            glance::VerifyGrantFile(wide_path, key, wide_sid, session, glance::NowUnix());
        std::printf("%ls\n", glance::StatusText(status));
        return status == glance::GrantStatus::Ok ? 0 : 1;
    }

    return Usage();
}
