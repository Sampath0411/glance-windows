// Glance for Windows - grant file format and validation.
//
// The agent (user session) writes a grant after a face + liveness match. The
// credential provider (SYSTEM, inside LogonUI.exe) validates it and only then
// submits the stored credential. This header is the contract between them.
//
// File layout (little-endian, packed, 84 bytes total):
//
//   0   char     magic[4]        "GLNC"
//   4   uint16   version         1
//   6   uint16   reserved        0
//   8   uint32   session_id      Windows session the grant was issued in
//  12   uint32   issued_unix     seconds since epoch
//  16   uint32   expires_unix    seconds since epoch
//  20   uint8    nonce[16]       one-time value, provider deletes the file
//  36   uint8    sid_hash[16]    SHA-256(UTF-8 SID string)[0..16]
//  52   uint8    hmac[32]        HMAC-SHA256(key, bytes 0..51)
//
// The SID is hashed rather than stored so the field is fixed width and the file
// does not leak which account it belongs to.
//
// Everything here fails closed: an unreadable, oversized, stale, foreign or
// forged grant is a refusal, never a fallback to "assume it was fine".

#pragma once

#include <windows.h>
#include <cstddef>
#include <cstdint>
#include <string>

namespace glance {

constexpr size_t kGrantBodySize = 52;
constexpr size_t kMacSize = 32;
constexpr size_t kGrantFileSize = kGrantBodySize + kMacSize;  // 84
constexpr size_t kKeySize = 32;
constexpr size_t kNonceSize = 16;
constexpr size_t kSidHashSize = 16;

// A grant older than this is not worth honouring even if its timestamps agree.
constexpr uint32_t kMaxGrantLifetimeSeconds = 60;
// Tolerated clock skew between the agent and the provider.
constexpr uint32_t kClockSkewToleranceSeconds = 5;

enum class GrantStatus {
    Ok = 0,
    Missing,        // no file where one was expected
    TooLarge,       // bigger than a grant can be - do not read it
    BadSize,        // truncated or padded
    BadMagic,       // not a Glance grant
    BadVersion,     // written by a different version
    BadMac,         // forged, corrupted, or the wrong key
    Expired,
    NotYetValid,
    WindowTooLong,  // lifetime longer than policy allows
    WrongSession,
    WrongUser,
    KeyUnavailable, // key missing or wrong length
    ReadError,
};

const wchar_t* StatusText(GrantStatus status);

// True only when the two buffers are equal, compared in constant time so the
// result does not leak how many bytes matched.
bool ConstantTimeEqual(const uint8_t* a, const uint8_t* b, size_t len);

bool Sha256(const void* data, size_t len, uint8_t out[32]);
bool HmacSha256(const uint8_t* key, size_t key_len,
                const void* data, size_t len, uint8_t out[32]);

// SHA-256 of the UTF-8 bytes of the SID string, first 16 bytes. The Python agent
// computes the same thing, so this definition is the interop contract.
bool SidHashFromString(const std::wstring& sid, uint8_t out[kSidHashSize]);

// Reads HKLM\SOFTWARE\Glance\GrantKey (REG_BINARY, 32 bytes).
bool LoadGrantKey(uint8_t out[kKeySize]);

// Validates an in-memory grant.
GrantStatus VerifyGrantBytes(const uint8_t* buffer, size_t size,
                             const uint8_t key[kKeySize],
                             const std::wstring& expected_sid,
                             uint32_t expected_session,
                             uint64_t now_unix);

// Reads (bounded) and validates. Never reads more than kGrantFileSize + 1 bytes.
GrantStatus VerifyGrantFile(const std::wstring& path,
                            const uint8_t key[kKeySize],
                            const std::wstring& expected_sid,
                            uint32_t expected_session,
                            uint64_t now_unix);

// Cheap header test: magic, version, sane lifetime, not expired. No HMAC - this only
// decides whether advertising a credential is worth it. VerifyGrantBytes is what
// actually authorises anything. It exists so a stale grant cannot make a Face tile
// appear on the boot screen.
bool GrantIsFresh(const uint8_t grant[kGrantFileSize], uint64_t now_unix);

// Reads the grant without deleting it. Bounded the same way.
GrantStatus ReadGrantFile(const std::wstring& path, uint8_t out[kGrantFileSize]);

// Reads the grant and deletes it before verifying. Deleting first is deliberate:
// one attempt per grant must hold even when verification cannot run at all (for
// example when the session SID is unavailable), otherwise a stale grant stays
// replayable inside its validity window.
GrantStatus ConsumeGrantFile(const std::wstring& path,
                             const uint8_t key[kKeySize],
                             const std::wstring& expected_sid,
                             uint32_t expected_session,
                             uint64_t now_unix);

// Builds a grant. Used by the test harness and the agent's C++ helper; the
// production agent writes the same bytes from Python.
bool BuildGrant(const uint8_t key[kKeySize],
                const std::wstring& sid,
                uint32_t session_id,
                uint32_t issued_unix,
                uint32_t expires_unix,
                const uint8_t nonce[kNonceSize],
                uint8_t out[kGrantFileSize],
                bool* hmac_ok);

// SID string for the account owning a session. Needs SE_TCB_NAME, which SYSTEM
// has; the provider calls this, the tool takes a SID on the command line.
std::wstring SidStringForSession(uint32_t session_id);

// Win32 error from the most recent SidStringForSession call on this thread, or 0.
// Diagnostics only - the empty string is what callers act on.
DWORD LastSidError();
uint32_t CurrentSessionId();
uint64_t NowUnix();

}  // namespace glance
