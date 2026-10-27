// Standalone harness for GlanceProvider.dll.
//
// This is the test that matters. A credential provider that blocks inside
// GetCredentialCount or SetUsageScenario prevents login entirely, and no amount of
// care later makes up for it. So the DLL is loaded here, out of process, with no
// LogonUI and no registry registration, and every interface method is called with a
// stopwatch running.
//
// What it proves:
//   * the COM object can be created without LogonUI
//   * every method returns, and returns quickly
//   * scenarios other than unlock claim zero credentials (fail closed)
//   * a bad or missing grant produces no serialization
//   * a grant is consumed on the first attempt even when the attempt fails
//
// What it cannot prove without SYSTEM: the success path, which needs SE_TCB_NAME to
// resolve the session's SID. That is tested separately under a SYSTEM task.
//
//   provider_harness.exe [dll_path] [sid]

#include <windows.h>
#include <credentialprovider.h>
#include <ntsecapi.h>

#include <chrono>
#include <cstdio>
#include <string>

#include "GlanceGrant.h"
#include "GlancePack.h"
#include "GlanceSecret.h"

#pragma comment(lib, "secur32.lib")  // LsaConnectUntrusted / LsaLogonUser (the --lsa probe)

namespace {

int g_pass = 0;
int g_fail = 0;

void Check(const char* name, bool ok, const std::string& detail = "") {
    printf("  %-54s %s", name, ok ? "PASS" : "FAIL");
    if (!detail.empty()) printf("   [%s]", detail.c_str());
    printf("\n");
    if (ok) ++g_pass; else ++g_fail;
}

// Printed but not counted: used for facts that depend on installation state rather
// than on the DLL itself.
void Note(const char* name, bool ok, const std::string& detail = "") {
    printf("  %-54s %s", name, ok ? "ok" : "NO");
    if (!detail.empty()) printf("   [%s]", detail.c_str());
    printf("\n");
}

std::string Narrow(const wchar_t* text) {
    if (text == nullptr) return {};
    const int need = WideCharToMultiByte(CP_UTF8, 0, text, -1, nullptr, 0, nullptr, nullptr);
    if (need <= 1) return {};
    std::string out(static_cast<size_t>(need - 1), '\0');
    WideCharToMultiByte(CP_UTF8, 0, text, -1, out.data(), need, nullptr, nullptr);
    return out;
}

double Millis(std::chrono::steady_clock::time_point start) {
    return std::chrono::duration<double, std::milli>(
               std::chrono::steady_clock::now() - start).count();
}

const CLSID kProviderClsid =
    {0x8F3A2C41, 0x7B6D, 0x4E59, {0x9A, 0x21, 0x5C, 0x0D, 0x7E, 0x3B, 0x1F, 0x84}};

const wchar_t kGrantDir[] = L"C:\\ProgramData\\Glance";
const wchar_t kGrantPath[] = L"C:\\ProgramData\\Glance\\grant.bin";

bool WriteGrant(const std::string& sid, uint32_t session, uint32_t ttl) {
    uint8_t key[glance::kKeySize];
    for (size_t i = 0; i < sizeof(key); ++i) key[i] = static_cast<uint8_t>(i + 1);

    const uint64_t now = glance::NowUnix();
    uint8_t nonce[glance::kNonceSize] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16};
    std::wstring wide_sid(sid.begin(), sid.end());

    uint8_t grant[glance::kGrantFileSize];
    if (!glance::BuildGrant(key, wide_sid, session, static_cast<uint32_t>(now),
                            static_cast<uint32_t>(now) + ttl, nonce, grant, nullptr)) {
        return false;
    }

    CreateDirectoryW(kGrantDir, nullptr);
    HANDLE file = CreateFileW(kGrantPath, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    DWORD written = 0;
    const BOOL ok = WriteFile(file, grant, static_cast<DWORD>(sizeof(grant)), &written, nullptr);
    CloseHandle(file);
    return ok && written == sizeof(grant);
}

bool GrantExists() {
    return GetFileAttributesW(kGrantPath) != INVALID_FILE_ATTRIBUTES;
}

}  // namespace

int main(int argc, char** argv) {
    // Diagnostic mode: prove whether the stored secret is the account's real password,
    // without ever printing it. Run as either the user or SYSTEM; LogonUser validates a
    // network logon, so it does not need the desktop.
    if (argc > 1 && std::string(argv[1]) == "--credential") {
        glance::StoredCredential cred;
        std::wstring error;
        if (!glance::LoadCredential(&cred, &error)) {
            printf("load failed: %ls\n", error.c_str());
            return 2;
        }
        printf("stored: domain='%ls' user='%ls' password_len=%zu\n",
               cred.domain.c_str(), cred.user.c_str(), cred.password.size());

        const wchar_t* domains[] = {cred.domain.c_str(), L".", L"Sampath", L""};
        for (const wchar_t* domain : domains) {
            HANDLE token = nullptr;
            SetLastError(0);
            const BOOL ok = LogonUserW(cred.user.c_str(), domain, cred.password.c_str(),
                                       LOGON32_LOGON_NETWORK, LOGON32_PROVIDER_DEFAULT,
                                       &token);
            printf("LogonUser domain='%ls': %s (err %lu)\n", domain, ok ? "OK" : "FAIL",
                   ok ? 0UL : GetLastError());
            if (token != nullptr) CloseHandle(token);
        }

        // A Microsoft account is validated by the CloudAP package, so the local
        // machine\alias form fails even with the correct password. argv[2] carries the
        // MSA email; the stored password is reused, never reprinted.
        if (argc > 2) {
            const std::wstring email = std::wstring(argv[2], argv[2] + strlen(argv[2]));
            const wchar_t* users[] = {email.c_str(), cred.user.c_str()};
            for (const wchar_t* user : users) {
                HANDLE token = nullptr;
                SetLastError(0);
                const BOOL ok = LogonUserW(user, L"MicrosoftAccount", cred.password.c_str(),
                                           LOGON32_LOGON_NETWORK, LOGON32_PROVIDER_DEFAULT,
                                           &token);
                printf("LogonUser MicrosoftAccount\\%ls: %s (err %lu)\n", user,
                       ok ? "OK" : "FAIL", ok ? 0UL : GetLastError());
                if (token != nullptr) CloseHandle(token);
            }
        }
        return 0;
    }

    // Diagnostic mode: call LsaLogonUser directly with the stored credential, so the
    // blob/package combinations can be compared without locking the screen. Prints the
    // NTSTATUS and substatus for each; a wrong-password result means the format and the
    // package were accepted and only the secret was refused.
    if (argc > 1 && std::string(argv[1]) == "--lsa") {
        glance::StoredCredential cred;
        std::wstring error;
        if (!glance::LoadCredential(&cred, &error)) {
            printf("load failed: %ls\n", error.c_str());
            return 2;
        }
        printf("stored: %ls\\%ls len=%zu\n", cred.domain.c_str(), cred.user.c_str(),
               cred.password.size());

        LSA_HANDLE lsa = nullptr;
        const NTSTATUS connected = LsaConnectUntrusted(&lsa);
        printf("LsaConnectUntrusted 0x%08lX\n", static_cast<unsigned long>(connected));
        if (connected != 0) return 2;

        auto lookup = [&](const char* name, ULONG* id_out) -> bool {
            LSA_STRING string{};
            string.Buffer = const_cast<char*>(name);
            string.Length = static_cast<USHORT>(strlen(name));
            string.MaximumLength = static_cast<USHORT>(string.Length + 1);
            ULONG id = 0;
            const NTSTATUS status = LsaLookupAuthenticationPackage(lsa, &string, &id);
            printf("package %-38s status=0x%08lX id=%lu\n", name,
                   static_cast<unsigned long>(status), id);
            *id_out = id;
            return status == 0;
        };

        ULONG negotiate = 0;
        ULONG msv = 0;
        ULONG cloudap = 0;
        const bool have_negotiate = lookup("Negotiate", &negotiate);
        const bool have_msv = lookup("MICROSOFT_AUTHENTICATION_PACKAGE_V1_0", &msv);
        const bool have_cloudap = lookup("CloudAP", &cloudap);
        (void)have_msv;

        struct Attempt {
            const char* label;
            ULONG package;
            bool valid;
            const wchar_t* domain;
            bool unlock_type;
        };
        const Attempt attempts[] = {
            {"Negotiate(0) + MicrosoftAccount + logon", negotiate, have_negotiate,
             L"MicrosoftAccount", false},
            {"Negotiate(0) + MicrosoftAccount + unlock", negotiate, have_negotiate,
             L"MicrosoftAccount", true},
            {"Negotiate(0) + empty domain + logon", negotiate, have_negotiate, L"", false},
            {"MSV1_0 + MicrosoftAccount", msv, have_msv, L"MicrosoftAccount", false},
            {"CloudAP + MicrosoftAccount", cloudap, have_cloudap, L"MicrosoftAccount", false},
        };

        for (const Attempt& attempt : attempts) {
            if (!attempt.valid) {
                printf("%-38s skipped (lookup failed)\n", attempt.label);
                continue;
            }

            CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION packed{};
            if (FAILED(glance::PackUnlockLogon(
                    attempt.domain, cred.user, cred.password,
                    attempt.unlock_type ? glance::PackScenario::Unlock
                                        : glance::PackScenario::Logon,
                    &packed))) {
                printf("%-38s pack failed\n", attempt.label);
                continue;
            }

            TOKEN_SOURCE source{};
            memcpy(source.SourceName, "Glance", 6);
            AllocateLocallyUniqueId(&source.SourceIdentifier);

            LSA_STRING origin{};
            const char kOrigin[] = "Glance";
            origin.Buffer = const_cast<char*>(kOrigin);
            origin.Length = static_cast<USHORT>(strlen(kOrigin));
            origin.MaximumLength = static_cast<USHORT>(origin.Length + 1);

            PVOID profile = nullptr;
            ULONG profile_length = 0;
            LUID logon_id{};
            HANDLE token = nullptr;
            QUOTA_LIMITS quotas{};
            NTSTATUS substatus = 0;

            const NTSTATUS status =
                LsaLogonUser(lsa, &origin, Network, attempt.package, packed.rgbSerialization,
                             packed.cbSerialization, nullptr, &source, &profile,
                             &profile_length, &logon_id, &token, &quotas, &substatus);
            printf("%-38s status=0x%08lX sub=0x%08lX\n", attempt.label,
                   static_cast<unsigned long>(status), static_cast<unsigned long>(substatus));

            if (token != nullptr) CloseHandle(token);
            if (profile != nullptr) LsaFreeReturnBuffer(profile);
            CoTaskMemFree(packed.rgbSerialization);
        }

        LsaClose(lsa);
        return 0;
    }

    const wchar_t* dll_path = argc > 1 ? nullptr : L"provider\\build\\GlanceProvider.dll";
    std::wstring dll = argc > 1 ? std::wstring() : std::wstring(dll_path);
    if (argc > 1) {
        const std::string narrow = argv[1];
        dll.assign(narrow.begin(), narrow.end());
    }
    const std::string sid = argc > 2 ? argv[2] : "S-1-5-21-1111111111-2222222222-3333333333-1001";

    printf("=== GlanceProvider.dll harness ===\n");
    printf("dll: %ls\n\n", dll.c_str());

    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

    // A grant left behind by a real lock would break the "no grant" expectations
    // below, so the harness always starts from a clean slate.
    DeleteFileW(kGrantPath);

    // The load-by-path test below deliberately bypasses COM. That is useful for
    // testing the DLL, and it is exactly why a missing InprocServer32 went unnoticed
    // once: LogonUI resolves the CLSID, not the file. So the registration is checked
    // here too, as a note rather than a pass/fail (it depends on being installed).
    {
        IUnknown* com_object = nullptr;
        const HRESULT com_hr = CoCreateInstance(kProviderClsid, nullptr, CLSCTX_INPROC_SERVER,
                                                IID_IUnknown,
                                                reinterpret_cast<void**>(&com_object));
        const bool registered = SUCCEEDED(com_hr) && com_object != nullptr;
        Note("COM: CLSID -> InprocServer32 resolves", registered,
             registered ? "LogonUI can load the provider"
                        : "not installed yet (run install.ps1)");
        if (com_object != nullptr) com_object->Release();
    }

    auto load_start = std::chrono::steady_clock::now();
    HMODULE module = LoadLibraryW(dll.c_str());
    const double load_ms = Millis(load_start);
    if (module == nullptr) {
        printf("FAILED to load DLL: %lu\n", GetLastError());
        return 1;
    }
    Check("DLL loads", true, std::to_string(static_cast<int>(load_ms)) + " ms");

    auto get_class_object = reinterpret_cast<HRESULT (STDAPICALLTYPE*)(REFCLSID, REFIID, void**)>(
        GetProcAddress(module, "DllGetClassObject"));
    auto can_unload = reinterpret_cast<HRESULT (STDAPICALLTYPE*)()>(
        GetProcAddress(module, "DllCanUnloadNow"));

    Check("DllGetClassObject exported", get_class_object != nullptr);
    Check("DllCanUnloadNow exported", can_unload != nullptr);
    if (get_class_object == nullptr) return 1;

    // 1. unknown CLSID must be refused
    IClassFactory* factory = nullptr;
    const CLSID bogus = {0, 0, 0, {0, 0, 0, 0, 0, 0, 0, 0}};
    HRESULT hr = get_class_object(bogus, IID_IClassFactory, reinterpret_cast<void**>(&factory));
    Check("unknown CLSID refused", hr == CLASS_E_CLASSNOTAVAILABLE,
          "hr=" + std::to_string(static_cast<long>(hr)));

    // 2. create the provider
    hr = get_class_object(kProviderClsid, IID_IClassFactory, reinterpret_cast<void**>(&factory));
    Check("class factory created", SUCCEEDED(hr) && factory != nullptr);
    if (factory == nullptr) return 1;

    ICredentialProvider* provider = nullptr;
    hr = factory->CreateInstance(nullptr, IID_ICredentialProvider,
                                 reinterpret_cast<void**>(&provider));
    Check("provider instantiated", SUCCEEDED(hr) && provider != nullptr);
    if (provider == nullptr) return 1;

    // 3. a scenario that is not unlock must claim nothing
    auto start = std::chrono::steady_clock::now();
    provider->SetUsageScenario(CPUS_LOGON, 0);
    const double logon_ms = Millis(start);

    DWORD count = 0, default_index = 0;
    BOOL auto_logon = TRUE;
    start = std::chrono::steady_clock::now();
    provider->GetCredentialCount(&count, &default_index, &auto_logon);
    const double logon_count_ms = Millis(start);

    Check("CPUS_LOGON: SetUsageScenario fast", logon_ms < 10.0,
          std::to_string(logon_ms).substr(0, 5) + " ms");
    Check("CPUS_LOGON: zero credentials", count == 0, "count=" + std::to_string(count));
    Check("CPUS_LOGON: GetCredentialCount fast", logon_count_ms < 10.0,
          std::to_string(logon_count_ms).substr(0, 5) + " ms");

    ICredentialProviderCredential* credential = nullptr;
    hr = provider->GetCredentialAt(0, &credential);
    Check("CPUS_LOGON: credential withheld", credential == nullptr);

    // 4. unlock scenario, no grant on disk
    start = std::chrono::steady_clock::now();
    provider->SetUsageScenario(CPUS_UNLOCK_WORKSTATION, 0);
    const double unlock_ms = Millis(start);

    start = std::chrono::steady_clock::now();
    provider->GetCredentialCount(&count, &default_index, &auto_logon);
    const double unlock_count_ms = Millis(start);

    Check("unlock: SetUsageScenario fast", unlock_ms < 10.0,
          std::to_string(unlock_ms).substr(0, 5) + " ms");
    Check("unlock: one credential", count == 1, "count=" + std::to_string(count));
    Check("unlock: GetCredentialCount fast", unlock_count_ms < 10.0,
          std::to_string(unlock_count_ms).substr(0, 5) + " ms");
    Check("unlock: no auto-logon without a grant", auto_logon == FALSE);

    hr = provider->GetCredentialAt(0, &credential);
    Check("unlock: credential provided", SUCCEEDED(hr) && credential != nullptr);

    ICredentialProviderCredential2* credential2 = nullptr;
    if (credential != nullptr) {
        credential->QueryInterface(IID_ICredentialProviderCredential2,
                                   reinterpret_cast<void**>(&credential2));
        Check("unlock: ICredentialProviderCredential2 available", credential2 != nullptr);
    }

    if (credential != nullptr) {
        wchar_t* value = nullptr;
        credential->GetStringValue(0, &value);
        Check("tile label is Face", value != nullptr && wcscmp(value, L"Face") == 0,
              Narrow(value));
        if (value) CoTaskMemFree(value);

        value = nullptr;
        credential->GetStringValue(1, &value);
        Check("button label is Unlock", value != nullptr && wcscmp(value, L"Unlock") == 0,
              Narrow(value));
        if (value) CoTaskMemFree(value);

        DWORD adjacent = 0xFFFF;
        Check("submit button targets the label",
              credential->GetSubmitButtonValue(1, &adjacent) == S_OK && adjacent == 0);

        CREDENTIAL_PROVIDER_FIELD_STATE state{};
        CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE interactive{};
        Check("label field is displayable",
              credential->GetFieldState(0, &state, &interactive) == S_OK &&
                  state == CPFS_DISPLAY_IN_BOTH);

        // 5. failing closed: no grant exists, so no serialization may be produced
        CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE response = CPGSR_NO_CREDENTIAL_FINISHED;
        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION serialization{};
        wchar_t* status = nullptr;
        CREDENTIAL_PROVIDER_STATUS_ICON icon = CPSI_NONE;

        start = std::chrono::steady_clock::now();
        hr = credential->GetSerialization(&response, &serialization, &status, &icon);
        const double serial_ms = Millis(start);

        Check("missing grant: GetSerialization fast", serial_ms < 250.0,
              std::to_string(serial_ms).substr(0, 6) + " ms");
        Check("missing grant: no credential returned", response == CPGSR_NO_CREDENTIAL_FINISHED);
        Check("missing grant: nothing serialized", serialization.cbSerialization == 0);
        Check("missing grant: reason reported", status != nullptr, Narrow(status));
        if (status) CoTaskMemFree(status);

        credential->ReportResult(0, 0, nullptr, nullptr);
    }

    // 6. a grant on disk makes the provider offer auto-logon, and is consumed
    if (WriteGrant(sid, glance::CurrentSessionId(), 10)) {
        provider->GetCredentialCount(&count, &default_index, &auto_logon);
        Check("valid grant file: auto-logon offered", auto_logon == TRUE && default_index == 0);

        // The locked-workstation screen on this machine reports CPUS_LOGON, so the
        // provider has to treat a sign-in screen with a fresh grant as unlockable.
        provider->SetUsageScenario(CPUS_LOGON, 0);
        provider->GetCredentialCount(&count, &default_index, &auto_logon);
        Check("logon scenario + fresh grant: tile offered with auto-logon",
              count == 1 && auto_logon == TRUE,
              "count=" + std::to_string(count));

        ICredentialProviderCredential* logon_credential = nullptr;
        const HRESULT logon_hr = provider->GetCredentialAt(0, &logon_credential);
        Check("logon scenario: credential can be fetched",
              SUCCEEDED(logon_hr) && logon_credential != nullptr,
              "hr=" + std::to_string(static_cast<long>(logon_hr)));
        if (logon_credential != nullptr) {
            BOOL logon_auto = FALSE;
            logon_credential->SetSelected(&logon_auto);
            Check("logon scenario: selection auto-submits", logon_auto == TRUE);
            logon_credential->Release();
        }
        provider->SetUsageScenario(CPUS_UNLOCK_WORKSTATION, 0);

        CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE response = CPGSR_NO_CREDENTIAL_FINISHED;
        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION serialization{};
        wchar_t* status = nullptr;
        CREDENTIAL_PROVIDER_STATUS_ICON icon = CPSI_NONE;

        start = std::chrono::steady_clock::now();
        credential->GetSerialization(&response, &serialization, &status, &icon);
        const double used_ms = Millis(start);

        Check("grant consumed on first attempt", !GrantExists());
        Check("grant attempt stayed fast", used_ms < 250.0,
              std::to_string(used_ms).substr(0, 6) + " ms");
        // As a standard user the session SID cannot be resolved (needs SE_TCB_NAME),
        // so this must refuse rather than guess.
        Check("non-SYSTEM: refused instead of guessing",
              response == CPGSR_NO_CREDENTIAL_FINISHED && serialization.cbSerialization == 0,
              Narrow(status));
        if (status) CoTaskMemFree(status);
    } else {
        Check("could not write test grant (needs C:\\ProgramData\\Glance)", false);
    }

    // 7. serialization packing. Winlogon reads this buffer as a byte-offset "packed"
    //    structure, not as live pointers, so both shapes get asserted explicitly.
    {
        const std::wstring domain_in = L"LAPTOP";
        const std::wstring user_in = L"sampa";
        const std::wstring password_in = L"correct horse battery staple";

        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION packed{};
        const HRESULT pack_hr = glance::PackUnlockLogon(
            domain_in, user_in, password_in, glance::PackScenario::Unlock, &packed);
        Check("pack: succeeds", SUCCEEDED(pack_hr) && packed.rgbSerialization != nullptr);

        std::wstring domain, user, password;
        uint32_t type = 0;
        const bool consistent =
            glance::InspectUnlockLogon(packed, &domain, &user, &password, &type);
        Check("pack: buffer internally consistent", consistent);
        Check("pack: unlock uses KerbWorkstationUnlockLogon", type == 7, std::to_string(type));
        Check("pack: domain round-trips", domain == domain_in, Narrow(domain.c_str()));
        Check("pack: user round-trips", user == user_in, Narrow(user.c_str()));
        Check("pack: password round-trips", password == password_in,
              std::to_string(password.size()) + " chars");

        if (packed.rgbSerialization != nullptr) {
            auto* unlock =
                reinterpret_cast<KERB_INTERACTIVE_UNLOCK_LOGON*>(packed.rgbSerialization);
            const DWORD expected_cb =
                static_cast<DWORD>(sizeof(KERB_INTERACTIVE_UNLOCK_LOGON)) +
                static_cast<DWORD>((domain_in.size() + user_in.size() + password_in.size()) * 2);
            Check("pack: no terminator bytes in the buffer",
                  packed.cbSerialization == expected_cb,
                  std::to_string(packed.cbSerialization) + " vs " + std::to_string(expected_cb));

            const ULONG_PTR domain_offset =
                reinterpret_cast<ULONG_PTR>(unlock->Logon.LogonDomainName.Buffer);
            const ULONG_PTR user_offset =
                reinterpret_cast<ULONG_PTR>(unlock->Logon.UserName.Buffer);
            Check("pack: string buffers are offsets, not addresses",
                  domain_offset == sizeof(KERB_INTERACTIVE_UNLOCK_LOGON) &&
                      user_offset == domain_offset + domain_in.size() * 2 &&
                      domain_offset < packed.cbSerialization,
                  std::to_string(domain_offset) + "/" + std::to_string(user_offset));

            const UNICODE_STRING saved = unlock->Logon.Password;
            std::wstring ignored;

            unlock->Logon.Password.Buffer = reinterpret_cast<PWSTR>(0x1000);
            Check("pack: out-of-buffer offset refused",
                  !glance::InspectUnlockLogon(packed, &ignored, &ignored, &ignored));

            unlock->Logon.Password = saved;
            unlock->Logon.MessageType = static_cast<KERB_LOGON_SUBMIT_TYPE>(0);
            Check("pack: wrong message type refused",
                  !glance::InspectUnlockLogon(packed, &ignored, &ignored, &ignored));

            unlock->Logon.MessageType = KerbWorkstationUnlockLogon;
            const DWORD saved_cb = packed.cbSerialization;
            packed.cbSerialization = saved_cb - 2;
            Check("pack: truncated buffer refused",
                  !glance::InspectUnlockLogon(packed, &ignored, &ignored, &ignored));
            packed.cbSerialization = saved_cb;

            CoTaskMemFree(packed.rgbSerialization);
        }

        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION logon_pack{};
        const HRESULT logon_hr = glance::PackUnlockLogon(
            domain_in, user_in, password_in, glance::PackScenario::Logon, &logon_pack);
        uint32_t logon_type = 0;
        const bool logon_ok = SUCCEEDED(logon_hr) && glance::InspectUnlockLogon(
            logon_pack, &domain, &user, &password, &logon_type);
        Check("pack: logon uses KerbInteractiveLogon",
              logon_ok && logon_type == 2, std::to_string(logon_type));
        if (logon_pack.rgbSerialization != nullptr) CoTaskMemFree(logon_pack.rgbSerialization);
    }

    if (credential2) credential2->Release();
    if (credential) credential->Release();
    provider->Release();
    factory->Release();

    // 7. everything released means the DLL is unloadable
    if (can_unload != nullptr) {
        Check("DllCanUnloadNow reports unloadable", can_unload() == S_OK);
    }

    FreeLibrary(module);
    CoUninitialize();

    printf("\n%d passed, %d failed\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
