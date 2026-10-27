// Glance for Windows - stored credential (DPAPI machine scope). See GlanceSecret.h.

#include "GlanceSecret.h"

#include <dpapi.h>
#include <vector>

#pragma comment(lib, "crypt32.lib")

namespace glance {
namespace {

constexpr wchar_t kAccountValue[] = L"AccountName";
constexpr wchar_t kPasswordValue[] = L"PasswordBlob";

// Extra entropy bound into the blob. It is not a secret (it is in the binary), it just
// ties the blob to this purpose so another DPAPI blob on the machine cannot be swapped in.
constexpr char kEntropy[] = "Glance/v1/credential";

std::wstring LastError(const wchar_t* what) {
    return std::wstring(what) + L" (error " + std::to_wstring(GetLastError()) + L")";
}

bool ReadBinary(const wchar_t* name, std::vector<uint8_t>* out) {
    DWORD size = 0;
    if (RegGetValueW(HKEY_LOCAL_MACHINE, kRegRoot, name, RRF_RT_REG_BINARY,
                     nullptr, nullptr, &size) != ERROR_SUCCESS || size == 0) {
        return false;
    }
    out->resize(size);
    const LSTATUS rc = RegGetValueW(HKEY_LOCAL_MACHINE, kRegRoot, name, RRF_RT_REG_BINARY,
                                    nullptr, out->data(), &size);
    if (rc != ERROR_SUCCESS) return false;
    out->resize(size);
    return true;
}

bool ReadString(const wchar_t* name, std::wstring* out) {
    DWORD size = 0;
    if (RegGetValueW(HKEY_LOCAL_MACHINE, kRegRoot, name, RRF_RT_REG_SZ,
                     nullptr, nullptr, &size) != ERROR_SUCCESS || size == 0) {
        return false;
    }
    std::vector<wchar_t> buffer(size / sizeof(wchar_t) + 1, L'\0');
    if (RegGetValueW(HKEY_LOCAL_MACHINE, kRegRoot, name, RRF_RT_REG_SZ, nullptr,
                     buffer.data(), &size) != ERROR_SUCCESS) {
        return false;
    }
    out->assign(buffer.data());
    return true;
}

bool WriteBinary(const wchar_t* name, const void* data, DWORD size) {
    HKEY key = nullptr;
    if (RegCreateKeyExW(HKEY_LOCAL_MACHINE, kRegRoot, 0, nullptr, 0, KEY_WRITE,
                        nullptr, &key, nullptr) != ERROR_SUCCESS) {
        return false;
    }
    const LSTATUS rc = RegSetValueExW(key, name, 0, REG_BINARY,
                                      static_cast<const BYTE*>(data), size);
    RegCloseKey(key);
    return rc == ERROR_SUCCESS;
}

bool WriteString(const wchar_t* name, const std::wstring& value) {
    HKEY key = nullptr;
    if (RegCreateKeyExW(HKEY_LOCAL_MACHINE, kRegRoot, 0, nullptr, 0, KEY_WRITE,
                        nullptr, &key, nullptr) != ERROR_SUCCESS) {
        return false;
    }
    const DWORD bytes = static_cast<DWORD>((value.size() + 1) * sizeof(wchar_t));
    const LSTATUS rc = RegSetValueExW(key, name, 0, REG_SZ,
                                      reinterpret_cast<const BYTE*>(value.c_str()), bytes);
    RegCloseKey(key);
    return rc == ERROR_SUCCESS;
}

void SplitAccount(const std::wstring& account, std::wstring* domain, std::wstring* user) {
    const size_t slash = account.find_last_of(L'\\');
    if (slash == std::wstring::npos) {
        *domain = L".";
        *user = account;
    } else {
        *domain = account.substr(0, slash);
        *user = account.substr(slash + 1);
        if (domain->empty()) *domain = L".";
    }
}

}  // namespace

bool StoreCredential(const std::wstring& account,
                     const std::wstring& password,
                     std::wstring* error) {
    if (account.empty()) {
        if (error) *error = L"account name is empty";
        return false;
    }
    if (password.empty()) {
        // Refused on purpose: an empty password would make the tile a no-op that looks
        // like it authenticated, and local accounts with blank passwords are a hole.
        if (error) *error = L"refusing to store an empty password";
        return false;
    }

    DATA_BLOB plain{};
    plain.cbData = static_cast<DWORD>(password.size() * sizeof(wchar_t));
    plain.pbData = reinterpret_cast<BYTE*>(const_cast<wchar_t*>(password.data()));

    DATA_BLOB entropy{};
    entropy.cbData = static_cast<DWORD>(sizeof(kEntropy) - 1);
    entropy.pbData = reinterpret_cast<BYTE*>(const_cast<char*>(kEntropy));

    DATA_BLOB sealed{};
    if (!CryptProtectData(&plain, L"Glance credential", &entropy, nullptr, nullptr,
                          CRYPTPROTECT_LOCAL_MACHINE | CRYPTPROTECT_UI_FORBIDDEN,
                          &sealed)) {
        if (error) *error = LastError(L"CryptProtectData failed");
        return false;
    }

    const bool wrote = WriteBinary(kPasswordValue, sealed.pbData, sealed.cbData) &&
                       WriteString(kAccountValue, account);
    LocalFree(sealed.pbData);

    if (!wrote) {
        if (error) *error = LastError(L"could not write to HKLM\\SOFTWARE\\Glance");
        return false;
    }
    return true;
}

bool LoadCredential(StoredCredential* out, std::wstring* error) {
    if (out == nullptr) return false;

    std::wstring account;
    if (!ReadString(kAccountValue, &account) || account.empty()) {
        if (error) *error = L"no stored account";
        return false;
    }

    std::vector<uint8_t> sealed;
    if (!ReadBinary(kPasswordValue, &sealed)) {
        if (error) *error = L"no stored password";
        return false;
    }

    DATA_BLOB cipher{};
    cipher.cbData = static_cast<DWORD>(sealed.size());
    cipher.pbData = sealed.data();

    DATA_BLOB entropy{};
    entropy.cbData = static_cast<DWORD>(sizeof(kEntropy) - 1);
    entropy.pbData = reinterpret_cast<BYTE*>(const_cast<char*>(kEntropy));

    DATA_BLOB plain{};
    if (!CryptUnprotectData(&cipher, nullptr, &entropy, nullptr, nullptr,
                            CRYPTPROTECT_UI_FORBIDDEN, &plain)) {
        if (error) *error = LastError(L"CryptUnprotectData failed");
        return false;
    }

    std::wstring password;
    if (plain.pbData != nullptr && plain.cbData >= sizeof(wchar_t)) {
        password.assign(reinterpret_cast<const wchar_t*>(plain.pbData),
                        plain.cbData / sizeof(wchar_t));
        const size_t nul = password.find(L'\0');
        if (nul != std::wstring::npos) password.resize(nul);
    }
    LocalFree(plain.pbData);

    if (password.empty()) {
        if (error) *error = L"decrypted password was empty";
        return false;
    }

    SplitAccount(account, &out->domain, &out->user);
    out->password = password;
    return true;
}

bool ClearCredential(std::wstring* error) {
    HKEY key = nullptr;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, kRegRoot, 0, KEY_SET_VALUE, &key) != ERROR_SUCCESS) {
        return true;  // nothing to clear
    }
    const LSTATUS a = RegDeleteValueW(key, kAccountValue);
    const LSTATUS b = RegDeleteValueW(key, kPasswordValue);
    RegCloseKey(key);

    const bool clear = (a == ERROR_SUCCESS || a == ERROR_FILE_NOT_FOUND) &&
                       (b == ERROR_SUCCESS || b == ERROR_FILE_NOT_FOUND);
    if (!clear && error) *error = L"could not delete credential values";
    return clear;
}

bool CredentialPresent() {
    StoredCredential credential;
    std::wstring error;
    return LoadCredential(&credential, &error);
}

}  // namespace glance
