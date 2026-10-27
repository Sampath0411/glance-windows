// Glance for Windows - the credential provider.
//
// Loaded by LogonUI.exe (SYSTEM, secure desktop) when the workstation is locked.
// Claims exactly one usage scenario, CPUS_UNLOCK_WORKSTATION. At boot, on the
// sign-in screen, and for every other scenario it reports zero credentials and
// disappears, leaving Windows' own password/PIN tile untouched.
//
// What it does on a successful unlock:
//   1. reads C:\ProgramData\Glance\grant.bin (bounded, 85 bytes max)
//   2. verifies the HMAC grant against SYSTEM's key, the session, and the SID
//   3. deletes the grant (one attempt per grant)
//   4. decrypts the stored password (DPAPI machine scope)
//   5. hands LogonUI a KERB_INTERACTIVE_UNLOCK_LOGON serialization
//
// What it never does: open the camera, load a model, touch the network, or wait on
// anything the caller controls. GetCredentialCount and SetUsageScenario are pure
// memory operations. A provider that blocks those two calls prevents login entirely,
// which is the one failure mode worth engineering against above all others.

#include <windows.h>
#include <credentialprovider.h>
#include <ntsecapi.h>
#include <shlwapi.h>

#include <new>
#include <string>
#include <vector>

#include <cstdarg>
#include <cstdio>

#include "GlanceGrant.h"
#include "GlancePack.h"
#include "GlanceSecret.h"

#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shlwapi.lib")
#pragma comment(lib, "secur32.lib")
#pragma comment(lib, "advapi32.lib")
#pragma comment(lib, "bcrypt.lib")

// Live COM object count, used by DllCanUnloadNow.
LONG g_object_count = 0;
LONG g_server_locks = 0;
HINSTANCE g_instance = nullptr;

namespace {

// {8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}
const CLSID kProviderClsid =
    {0x8F3A2C41, 0x7B6D, 0x4E59, {0x9A, 0x21, 0x5C, 0x0D, 0x7E, 0x3B, 0x1F, 0x84}};

const wchar_t kGrantPath[] = L"C:\\ProgramData\\Glance\\grant.bin";
const wchar_t kGrantDir[] = L"C:\\ProgramData\\Glance";

// A header-level check only. It answers "is it worth offering a tile / submitting",
// not "may this unlock the machine" - GetSerialization answers that with the HMAC.
bool GrantUsable() {
    uint8_t buffer[glance::kGrantFileSize];
    if (glance::ReadGrantFile(kGrantPath, buffer) != glance::GrantStatus::Ok) {
        return false;
    }
    return glance::GrantIsFresh(buffer, glance::NowUnix());
}

// Diagnostics. Nothing can print from inside LogonUI, and "the provider did nothing"
// has half a dozen causes that look identical from the outside - not loaded, loaded
// but asked for the wrong scenario, tile drawn but never submitted. One line per
// decision turns that into a fact. Writes are tiny, append-only, and never fail the
// caller.
void Trace(const wchar_t* format, ...) {
    static bool directory_ready = false;
    if (!directory_ready) {
        CreateDirectoryW(L"C:\\ProgramData\\Glance\\logs", nullptr);

        // One rotation per process. Every unlock writes a handful of lines, but the file
        // is append-only across locks forever, and a stray loop once produced 14k lines in
        // ten seconds. 1 MB is months of normal use.
        WIN32_FILE_ATTRIBUTE_DATA existing{};
        if (GetFileAttributesExW(L"C:\\ProgramData\\Glance\\logs\\provider.log",
                                 GetFileExInfoStandard, &existing) &&
            existing.nFileSizeLow > 1024 * 1024) {
            MoveFileExW(L"C:\\ProgramData\\Glance\\logs\\provider.log",
                        L"C:\\ProgramData\\Glance\\logs\\provider.old.log",
                        MOVEFILE_REPLACE_EXISTING);
        }
        directory_ready = true;
    }

    wchar_t text[512];
    va_list args;
    va_start(args, format);
    _vsnwprintf_s(text, _countof(text), _TRUNCATE, format, args);
    va_end(args);

    char utf8[1400];
    const int converted = WideCharToMultiByte(CP_UTF8, 0, text, -1, utf8,
                                              static_cast<int>(sizeof(utf8)) - 1,
                                              nullptr, nullptr);
    if (converted <= 1) return;

    SYSTEMTIME now{};
    GetLocalTime(&now);

    char line[1500];
    const int length = _snprintf_s(line, sizeof(line), _TRUNCATE,
                                   "%04u-%02u-%02u %02u:%02u:%02u  %s\r\n",
                                   now.wYear, now.wMonth, now.wDay,
                                   now.wHour, now.wMinute, now.wSecond, utf8);
    if (length <= 0) return;

    HANDLE file = CreateFileW(L"C:\\ProgramData\\Glance\\logs\\provider.log", FILE_APPEND_DATA,
                              FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return;
    DWORD bytes = 0;
    WriteFile(file, line, static_cast<DWORD>(length), &bytes, nullptr);
    CloseHandle(file);
}

// LogonUI owns the strings it is handed, so every one is CoTaskMemAlloc'd.
HRESULT CopyString(const wchar_t* text, wchar_t** out) {
    if (out == nullptr) return E_INVALIDARG;
    *out = nullptr;
    if (text == nullptr) return S_OK;
    const size_t bytes = (wcslen(text) + 1) * sizeof(wchar_t);
    auto* copy = static_cast<wchar_t*>(CoTaskMemAlloc(bytes));
    if (copy == nullptr) return E_OUTOFMEMORY;
    memcpy(copy, text, bytes);
    *out = copy;
    return S_OK;
}

// Field layout of the single tile.
enum Fields : DWORD {
    kFieldLabel = 0,
    kFieldSubmit = 1,
    kFieldCount = 2,
};

// Local accounts and Microsoft accounts are authenticated by different packages.
// Sending an MSA password to MSV1_0 fails as "wrong password" even when it is correct,
// so the choice follows the stored credential's domain rather than a global default.
bool AuthenticationPackage(bool microsoft_account, ULONG* out) {
    // Id 0 is a legitimate package id here (Negotiate), so "resolved" cannot be
    // expressed by the id itself.
    static ULONG cached_local = 0;
    static ULONG cached_msa = 0;
    static bool resolved_local = false;
    static bool resolved_msa = false;

    ULONG& cached = microsoft_account ? cached_msa : cached_local;
    bool& resolved = microsoft_account ? resolved_msa : resolved_local;
    if (resolved) {
        *out = cached;
        return true;
    }

    LSA_HANDLE lsa = nullptr;
    const NTSTATUS connected = LsaConnectUntrusted(&lsa);
    if (connected != 0) {
        Trace(L"LsaConnectUntrusted failed 0x%08X", static_cast<unsigned>(connected));
        return false;
    }

    // Names and ids measured on this build (11 Pro 25H2):
    //   MICROSOFT_AUTHENTICATION_PACKAGE_V1_0 -> 11   (MSV1_0; local accounts)
    //   Kerberos                              -> 2
    //   CloudAP                               -> 6
    //   Negotiate                             -> 0    (valid; the standard password tile
    //                                                  uses it, and lsasrv rewrites a
    //                                                  MicrosoftAccount\<email> logon into
    //                                                  CloudAP from there)
    //   MSV1_0                                -> STATUS_NO_SUCH_PACKAGE
    //
    // CloudAP itself does not accept KERB_INTERACTIVE_LOGON - it wants an undocumented
    // SEC_WINNT_AUTH_IDENTITY_EX2 - so a Microsoft account must go through Negotiate.
    const char* local_names[] = {"MICROSOFT_AUTHENTICATION_PACKAGE_V1_0", "MSV1_0", "Kerberos"};
    const char* msa_names[] = {"Negotiate", "CloudAP"};

    const char* const* names = microsoft_account ? msa_names : local_names;
    const size_t count = microsoft_account ? ARRAYSIZE(msa_names) : ARRAYSIZE(local_names);
    for (size_t i = 0; i < count; ++i) {
        const char* name = names[i];
        LSA_STRING string{};
        string.Buffer = const_cast<char*>(name);
        string.Length = static_cast<USHORT>(strlen(name));
        string.MaximumLength = static_cast<USHORT>(string.Length + 1);

        ULONG package = 0;
        const NTSTATUS status = LsaLookupAuthenticationPackage(lsa, &string, &package);
        Trace(L"package %hs -> status 0x%08X id %lu", name,
              static_cast<unsigned>(status), package);
        if (status == 0) {
            cached = package;
            resolved = true;
            break;
        }
    }

    // An untrusted LSA handle is closed with LsaClose; LsaDeregisterLogonProcess is for
    // handles from LsaRegisterLogonProcess and leaves this one dangling.
    LsaClose(lsa);
    *out = cached;
    return resolved;
}

// KERB_INTERACTIVE_UNLOCK_LOGON packing lives in GlancePack.cpp so it can be
// asserted outside LogonUI.

}  // namespace

// ---------------------------------------------------------------------------
// Credential
// ---------------------------------------------------------------------------

class CGlanceProvider;

class CGlanceCredential final : public ICredentialProviderCredential2 {
public:
    CGlanceCredential(CGlanceProvider* provider, std::wstring sid,
                      CREDENTIAL_PROVIDER_USAGE_SCENARIO scenario)
        : _cRef(1), _provider(provider), _sid(std::move(sid)), _scenario(scenario),
          _events(nullptr) {
        InterlockedIncrement(&g_object_count);
    }

    // The screen can change under a cached credential (the same object is reused while
    // LogonUI re-enumerates), and the message type depends on it.
    void SetScenario(CREDENTIAL_PROVIDER_USAGE_SCENARIO scenario) { _scenario = scenario; }

    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (ppv == nullptr) return E_INVALIDARG;
        *ppv = nullptr;
        if (riid == IID_IUnknown || riid == IID_ICredentialProviderCredential ||
            riid == IID_ICredentialProviderCredential2) {
            *ppv = static_cast<ICredentialProviderCredential2*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }

    IFACEMETHODIMP_(ULONG) AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&_cRef));
    }

    IFACEMETHODIMP_(ULONG) Release() override {
        const LONG remaining = InterlockedDecrement(&_cRef);
        if (remaining == 0) delete this;
        return static_cast<ULONG>(remaining);
    }

    IFACEMETHODIMP Advise(ICredentialProviderCredentialEvents* events) override {
        _events = events;
        return S_OK;
    }

    IFACEMETHODIMP UnAdvise() override {
        _events = nullptr;
        return S_OK;
    }

    IFACEMETHODIMP SetSelected(BOOL* auto_logon) override {
        // This is the hook that actually submits on this build: LogonUI ignores
        // pbAutoLogonWithDefault on re-enumeration, but honours SetSelected's
        // out-parameter. Claim it only when a grant exists, or a click on a stale
        // tile bounces the user to the password prompt for no reason.
        const bool usable = GrantUsable();
        if (auto_logon != nullptr) *auto_logon = usable ? TRUE : FALSE;
        Trace(L"SetSelected -> auto=%d", usable ? 1 : 0);
        return S_OK;
    }

    IFACEMETHODIMP SetDeselected() override { return S_OK; }

    IFACEMETHODIMP GetFieldState(DWORD field,
                                 CREDENTIAL_PROVIDER_FIELD_STATE* state,
                                 CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE* interactive) override {
        if (state == nullptr || interactive == nullptr) return E_INVALIDARG;
        *interactive = CPFIS_NONE;
        switch (field) {
            case kFieldLabel:
                *state = CPFS_DISPLAY_IN_BOTH;
                return S_OK;
            case kFieldSubmit:
                *state = CPFS_DISPLAY_IN_SELECTED_TILE;
                return S_OK;
            default:
                return E_INVALIDARG;
        }
    }

    IFACEMETHODIMP GetStringValue(DWORD field, wchar_t** value) override {
        switch (field) {
            case kFieldLabel:
                return CopyString(L"Face", value);
            case kFieldSubmit:
                return CopyString(L"Unlock", value);
            default:
                if (value != nullptr) *value = nullptr;
                return E_INVALIDARG;
        }
    }

    IFACEMETHODIMP GetBitmapValue(DWORD, HBITMAP* bitmap) override {
        if (bitmap != nullptr) *bitmap = nullptr;
        return E_NOTIMPL;
    }

    IFACEMETHODIMP GetCheckboxValue(DWORD, BOOL* checked, wchar_t** label) override {
        if (checked != nullptr) *checked = FALSE;
        if (label != nullptr) *label = nullptr;
        return E_NOTIMPL;
    }

    IFACEMETHODIMP GetSubmitButtonValue(DWORD field, DWORD* adjacent) override {
        if (field != kFieldSubmit || adjacent == nullptr) return E_INVALIDARG;
        *adjacent = kFieldLabel;
        return S_OK;
    }

    IFACEMETHODIMP GetComboBoxValueCount(DWORD, DWORD* count, DWORD* selected) override {
        if (count != nullptr) *count = 0;
        if (selected != nullptr) *selected = 0;
        return E_NOTIMPL;
    }

    IFACEMETHODIMP GetComboBoxValueAt(DWORD, DWORD, wchar_t**) override {
        return E_NOTIMPL;
    }

    IFACEMETHODIMP SetStringValue(DWORD, PCWSTR) override { return E_NOTIMPL; }
    IFACEMETHODIMP SetCheckboxValue(DWORD, BOOL) override { return E_NOTIMPL; }
    IFACEMETHODIMP SetComboBoxSelectedValue(DWORD, DWORD) override { return E_NOTIMPL; }
    IFACEMETHODIMP CommandLinkClicked(DWORD) override { return E_NOTIMPL; }

    IFACEMETHODIMP GetSerialization(CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE* response,
                                    CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* serialization,
                                    wchar_t** status_text,
                                    CREDENTIAL_PROVIDER_STATUS_ICON* status_icon) override {
        if (response == nullptr || serialization == nullptr) return E_INVALIDARG;
        *response = CPGSR_NO_CREDENTIAL_FINISHED;
        ZeroMemory(serialization, sizeof(*serialization));
        if (status_text != nullptr) *status_text = nullptr;
        if (status_icon != nullptr) *status_icon = CPSI_NONE;
        Trace(L"GetSerialization enter");

        // Everything below is bounded work: one small file read, one HMAC, one
        // DPAPI unprotect. Nothing here can wait on the camera or the network.

        // Consume first. Whatever happens next - unresolvable SID, missing key, bad
        // MAC - this grant is spent, so it cannot be replayed inside its window.
        uint8_t grant[glance::kGrantFileSize];
        const glance::GrantStatus read = glance::ReadGrantFile(kGrantPath, grant);
        DeleteFileW(kGrantPath);
        Trace(L"GetSerialization grant read=%d", static_cast<int>(read));

        if (read != glance::GrantStatus::Ok) {
            CopyString(glance::StatusText(read), status_text);
            return S_OK;
        }

        const std::wstring sid = glance::SidStringForSession(glance::CurrentSessionId());
        if (sid.empty()) {
            CopyString(L"Session account unavailable", status_text);
            Trace(L"GetSerialization sid unavailable (session %u, error %lu)",
                  glance::CurrentSessionId(), glance::LastSidError());
            return S_OK;
        }

        uint8_t key[glance::kKeySize];
        if (!glance::LoadGrantKey(key)) {
            CopyString(L"Glance is not configured", status_text);
            return S_OK;
        }

        const glance::GrantStatus status = glance::VerifyGrantBytes(
            grant, sizeof(grant), key, sid, glance::CurrentSessionId(), glance::NowUnix());

        if (status != glance::GrantStatus::Ok) {
            // Fail closed and say so quietly. The password tile is still there.
            CopyString(glance::StatusText(status), status_text);
            Trace(L"GetSerialization verify=%d (%ls)", static_cast<int>(status),
                  glance::StatusText(status));
            return S_OK;
        }

        glance::StoredCredential credential;
        std::wstring error;
        if (!glance::LoadCredential(&credential, &error) ||
            credential.password.empty()) {
            CopyString(L"Stored credential unavailable", status_text);
            Trace(L"GetSerialization credential failed: %ls", error.c_str());
            return S_OK;
        }

        // Microsoft accounts and local accounts are not interchangeable here: an MSA
        // password sent to MSV1_0 comes back as "the user name or password is incorrect".
        const bool microsoft_account =
            _wcsicmp(credential.domain.c_str(), L"MicrosoftAccount") == 0;
        ULONG package = 0;
        if (!AuthenticationPackage(microsoft_account, &package)) {
            CopyString(L"Authentication package unavailable", status_text);
            Trace(L"GetSerialization no authentication package (msa=%d)",
                  microsoft_account ? 1 : 0);
            return S_OK;
        }

        // The message type has to match the screen the user is actually on: a locked
        // workstation must submit KerbWorkstationUnlockLogon, the sign-in screen
        // KerbInteractiveLogon. The wrong one is refused outright.
        const glance::PackScenario pack_scenario =
            _scenario == CPUS_UNLOCK_WORKSTATION ? glance::PackScenario::Unlock
                                                 : glance::PackScenario::Logon;
        const HRESULT packed = glance::PackUnlockLogon(credential.domain, credential.user,
                                                       credential.password, pack_scenario,
                                                       serialization);
        if (FAILED(packed)) return packed;

        serialization->clsidCredentialProvider = kProviderClsid;
        serialization->ulAuthenticationPackage = package;

        Trace(L"GetSerialization submitting %lu bytes as %ls\\%ls (package %lu, type %d)",
              serialization->cbSerialization, credential.domain.c_str(),
              credential.user.c_str(), package,
              pack_scenario == glance::PackScenario::Unlock ? 7 : 2);

        *response = CPGSR_RETURN_CREDENTIAL_FINISHED;
        return S_OK;
    }

    IFACEMETHODIMP ReportResult(NTSTATUS, NTSTATUS, wchar_t** status_text,
                                CREDENTIAL_PROVIDER_STATUS_ICON* status_icon) override {
        if (status_text != nullptr) *status_text = nullptr;
        if (status_icon != nullptr) *status_icon = CPSI_NONE;
        return S_OK;
    }

    IFACEMETHODIMP GetUserSid(wchar_t** sid) override {
        return CopyString(_sid.c_str(), sid);
    }

private:
    ~CGlanceCredential() { InterlockedDecrement(&g_object_count); }

    LONG _cRef;
    CGlanceProvider* _provider;
    std::wstring _sid;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO _scenario;
    ICredentialProviderCredentialEvents* _events;
};

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

class CGlanceProvider final : public ICredentialProvider {
public:
    CGlanceProvider() : _cRef(1), _scenario(CPUS_INVALID), _credential(nullptr),
                        _events(nullptr), _advise_context(0), _stop(nullptr),
                        _watch(nullptr), _thread(nullptr) {
        InterlockedIncrement(&g_object_count);
    }

    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (ppv == nullptr) return E_INVALIDARG;
        *ppv = nullptr;
        if (riid == IID_IUnknown || riid == IID_ICredentialProvider) {
            *ppv = static_cast<ICredentialProvider*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }

    IFACEMETHODIMP_(ULONG) AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&_cRef));
    }

    IFACEMETHODIMP_(ULONG) Release() override {
        const LONG remaining = InterlockedDecrement(&_cRef);
        if (remaining == 0) delete this;
        return static_cast<ULONG>(remaining);
    }

    IFACEMETHODIMP SetUsageScenario(CREDENTIAL_PROVIDER_USAGE_SCENARIO scenario, DWORD) override {
        // The only scenario this provider ever touches. Pure memory write, returns
        // immediately: a hang here blocks the logon UI before anything is drawn.
        _scenario = scenario;
        Trace(L"SetUsageScenario %u", static_cast<unsigned>(scenario));
        return S_OK;
    }

    IFACEMETHODIMP SetSerialization(const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION*) override {
        return E_NOTIMPL;
    }

    IFACEMETHODIMP Advise(ICredentialProviderEvents* events, UINT_PTR context) override {
        if (events == nullptr) return E_INVALIDARG;
        _events = events;
        _advise_context = context;
        StartWatching();
        Trace(L"Advise (watching=%d)", _thread != nullptr ? 1 : 0);
        return S_OK;
    }

    IFACEMETHODIMP UnAdvise() override {
        StopWatching();
        _events = nullptr;
        _advise_context = 0;
        return S_OK;
    }

    IFACEMETHODIMP GetFieldDescriptorCount(DWORD* count) override {
        if (count == nullptr) return E_INVALIDARG;
        *count = 0;  // no provider-level fields
        return S_OK;
    }

    IFACEMETHODIMP GetFieldDescriptorAt(DWORD, CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR** descriptor) override {
        if (descriptor != nullptr) *descriptor = nullptr;
        return E_INVALIDARG;
    }

    IFACEMETHODIMP GetCredentialCount(DWORD* count, DWORD* default_index,
                                      BOOL* auto_logon) override {
        if (count == nullptr || default_index == nullptr || auto_logon == nullptr) {
            return E_INVALIDARG;
        }

        const CREDENTIAL_PROVIDER_USAGE_SCENARIO scenario = _scenario;

        auto none = [&](const wchar_t* reason) {
            *count = 0;
            *default_index = CREDENTIAL_PROVIDER_NO_DEFAULT;
            *auto_logon = FALSE;
            Trace(L"GetCredentialCount scenario=%u -> 0 (%ls)",
                  static_cast<unsigned>(scenario), reason);
            return S_OK;
        };

        if (scenario != CPUS_UNLOCK_WORKSTATION && scenario != CPUS_LOGON) {
            return none(L"scenario not ours");
        }

        const bool usable = GrantUsable();

        // Measured on this machine: the locked-workstation screen reports CPUS_LOGON,
        // not CPUS_UNLOCK_WORKSTATION, so refusing CPUS_LOGON meant the provider was
        // never even drawn. Both are accepted, with different rules:
        //
        //   unlock  - always one tile, so a grant can arrive while it is on screen
        //   logon   - a tile only when a fresh grant exists, because at boot or after
        //             a sign-out there is no agent to write one, and a Face tile that
        //             cannot possibly work is worse than no tile
        if (scenario == CPUS_LOGON && !usable) {
            return none(L"sign-in screen without a grant");
        }

        *count = 1;
        *default_index = usable ? 0 : CREDENTIAL_PROVIDER_NO_DEFAULT;
        *auto_logon = usable ? TRUE : FALSE;
        Trace(L"GetCredentialCount scenario=%u -> 1 auto=%d usable=%d",
              static_cast<unsigned>(scenario), usable ? 1 : 0, usable ? 1 : 0);
        return S_OK;
    }

    IFACEMETHODIMP GetCredentialAt(DWORD index, ICredentialProviderCredential** credential) override {
        if (credential == nullptr) return E_INVALIDARG;
        *credential = nullptr;
        if (index != 0) return E_INVALIDARG;
        if (_scenario != CPUS_UNLOCK_WORKSTATION && _scenario != CPUS_LOGON) {
            return E_INVALIDARG;
        }
        // Same rule as GetCredentialCount: on a sign-in screen the credential is only
        // handed out when a grant proves this is an unlock, not a boot.
        if (_scenario == CPUS_LOGON && !GrantUsable()) return E_INVALIDARG;
        Trace(L"GetCredentialAt scenario=%u", static_cast<unsigned>(_scenario));

        if (_credential == nullptr) {
            const std::wstring sid = glance::SidStringForSession(glance::CurrentSessionId());
            _credential = new (std::nothrow) CGlanceCredential(this, sid, _scenario);
            if (_credential == nullptr) return E_OUTOFMEMORY;
        }
        _credential->SetScenario(_scenario);
        _credential->AddRef();
        *credential = _credential;
        return S_OK;
    }

private:
    ~CGlanceProvider() {
        StopWatching();
        if (_credential != nullptr) _credential->Release();
        InterlockedDecrement(&g_object_count);
    }

    // GrantUsable() lives in the anonymous namespace so the credential can use it too.

    // The agent writes the grant a second or two after the screen locks. Without a
    // notification, LogonUI would never re-ask, and the user would have to click.
    void StartWatching() {
        if (_thread != nullptr) return;
        _stop = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        if (_stop == nullptr) return;
        _watch = FindFirstChangeNotificationW(
            kGrantDir, FALSE,
            FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_LAST_WRITE);
        if (_watch == INVALID_HANDLE_VALUE) {
            CloseHandle(_stop);
            _stop = nullptr;
            _watch = nullptr;
            return;
        }
        _thread = CreateThread(nullptr, 0, &CGlanceProvider::WatchThread, this, 0, nullptr);
        if (_thread == nullptr) {
            FindCloseChangeNotification(_watch);
            CloseHandle(_stop);
            _watch = nullptr;
            _stop = nullptr;
        }
    }

    void StopWatching() {
        if (_stop != nullptr) SetEvent(_stop);
        if (_thread != nullptr) {
            WaitForSingleObject(_thread, 2000);
            CloseHandle(_thread);
            _thread = nullptr;
        }
        if (_watch != nullptr) {
            FindCloseChangeNotification(_watch);
            _watch = nullptr;
        }
        if (_stop != nullptr) {
            CloseHandle(_stop);
            _stop = nullptr;
        }
    }

    static DWORD WINAPI WatchThread(LPVOID parameter) {
        auto* self = static_cast<CGlanceProvider*>(parameter);
        HANDLE handles[2] = {self->_stop, self->_watch};
        ULONGLONG last_notify = 0;

        for (;;) {
            const DWORD wait = WaitForMultipleObjects(2, handles, FALSE, INFINITE);
            if (wait == WAIT_OBJECT_0) return 0;  // stop requested
            if (wait != WAIT_OBJECT_0 + 1) return 0;

            FindNextChangeNotification(self->_watch);

            ICredentialProviderEvents* events = self->_events;
            if (events == nullptr) continue;
            if (!GrantUsable()) continue;

            // Debounced. Directory notifications coalesce poorly: a write followed by
            // a rename can arrive as several signals, and each one costs LogonUI a
            // full re-enumeration.
            const ULONGLONG now = GetTickCount64();
            if (last_notify != 0 && now - last_notify < 500) continue;
            last_notify = now;

            Trace(L"grant available, notifying LogonUI");
            events->CredentialsChanged(self->_advise_context);
        }
    }

    LONG _cRef;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO _scenario;
    CGlanceCredential* _credential;
    ICredentialProviderEvents* _events;
    UINT_PTR _advise_context;
    HANDLE _stop;
    HANDLE _watch;
    HANDLE _thread;
};

// ---------------------------------------------------------------------------
// COM plumbing
// ---------------------------------------------------------------------------

namespace {

class CClassFactory final : public IClassFactory {
public:
    CClassFactory() : _cRef(1) {}

    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (ppv == nullptr) return E_INVALIDARG;
        *ppv = nullptr;
        if (riid == IID_IUnknown || riid == IID_IClassFactory) {
            *ppv = static_cast<IClassFactory*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }

    IFACEMETHODIMP_(ULONG) AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&_cRef));
    }

    IFACEMETHODIMP_(ULONG) Release() override {
        const LONG remaining = InterlockedDecrement(&_cRef);
        if (remaining == 0) delete this;
        return static_cast<ULONG>(remaining);
    }

    IFACEMETHODIMP CreateInstance(IUnknown* outer, REFIID riid, void** ppv) override {
        if (ppv == nullptr) return E_INVALIDARG;
        *ppv = nullptr;
        if (outer != nullptr) return CLASS_E_NOAGGREGATION;

        auto* provider = new (std::nothrow) CGlanceProvider();
        if (provider == nullptr) return E_OUTOFMEMORY;
        const HRESULT hr = provider->QueryInterface(riid, ppv);
        provider->Release();
        return hr;
    }

    IFACEMETHODIMP LockServer(BOOL lock) override {
        if (lock) InterlockedIncrement(&g_server_locks);
        else InterlockedDecrement(&g_server_locks);
        return S_OK;
    }

private:
    ~CClassFactory() = default;
    LONG _cRef;
};

}  // namespace

// ---------------------------------------------------------------------------
// DLL exports
// ---------------------------------------------------------------------------

namespace {

struct Value {
    const wchar_t* name;
    const wchar_t* text;
};

bool WriteRegistryKey(const wchar_t* path, const Value* values, size_t count) {
    HKEY key = nullptr;
    if (RegCreateKeyExW(HKEY_LOCAL_MACHINE, path, 0, nullptr, 0, KEY_WRITE,
                        nullptr, &key, nullptr) != ERROR_SUCCESS) {
        return false;
    }

    bool ok = true;
    for (size_t i = 0; i < count; ++i) {
        const DWORD bytes = static_cast<DWORD>((wcslen(values[i].text) + 1) * sizeof(wchar_t));
        if (RegSetValueExW(key, values[i].name, 0, REG_SZ,
                           reinterpret_cast<const BYTE*>(values[i].text),
                           bytes) != ERROR_SUCCESS) {
            ok = false;
        }
    }

    RegCloseKey(key);
    return ok;
}

}  // namespace

STDAPI DllGetClassObject(REFCLSID clsid, REFIID riid, void** ppv) {
    if (ppv == nullptr) return E_INVALIDARG;
    *ppv = nullptr;
    if (clsid != kProviderClsid) return CLASS_E_CLASSNOTAVAILABLE;

    auto* factory = new (std::nothrow) CClassFactory();
    if (factory == nullptr) return E_OUTOFMEMORY;
    const HRESULT hr = factory->QueryInterface(riid, ppv);
    factory->Release();
    return hr;
}

STDAPI DllCanUnloadNow() {
    return (g_object_count == 0 && g_server_locks == 0) ? S_OK : S_FALSE;
}

STDAPI DllRegisterServer() {
    wchar_t path[MAX_PATH];
    const DWORD length = GetModuleFileNameW(g_instance, path, MAX_PATH);
    if (length == 0 || length >= MAX_PATH) return HRESULT_FROM_WIN32(GetLastError());

    // A credential provider is a COM object, and it needs two registrations that
    // look unrelated: the entry under "Credential Providers" says this CLSID is
    // enabled, and the CLSID's InprocServer32 says where the DLL lives. Miss the
    // second and nothing fails loudly - LogonUI just never loads the provider.
    const wchar_t* provider_key =
        L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Authentication\\Credential Providers\\"
        L"{8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}";
    const wchar_t* filter_key =
        L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Authentication\\Credential Provider Filters\\"
        L"{8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}";
    const wchar_t* clsid_key =
        L"SOFTWARE\\Classes\\CLSID\\{8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}";
    const wchar_t* inproc_key =
        L"SOFTWARE\\Classes\\CLSID\\{8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}\\InprocServer32";

    const Value name_only[] = {{nullptr, L"Glance face unlock"}};
    const Value inproc[] = {{nullptr, path}, {L"ThreadingModel", L"Apartment"}};

    bool ok = WriteRegistryKey(provider_key, name_only, 1);
    ok = WriteRegistryKey(filter_key, name_only, 1) && ok;
    ok = WriteRegistryKey(clsid_key, name_only, 1) && ok;
    ok = WriteRegistryKey(inproc_key, inproc, 2) && ok;
    return ok ? S_OK : E_FAIL;
}

STDAPI DllUnregisterServer() {
    const wchar_t* keys[] = {
        L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Authentication\\Credential Providers\\"
        L"{8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}",
        L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Authentication\\Credential Provider Filters\\"
        L"{8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}",
        L"SOFTWARE\\Classes\\CLSID\\{8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}",
    };
    for (const wchar_t* key : keys) {
        RegDeleteTreeW(HKEY_LOCAL_MACHINE, key);
    }
    return S_OK;
}

extern "C" BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_instance = instance;
        DisableThreadLibraryCalls(instance);
        Trace(L"DllMain attach");
    }
    return TRUE;
}
