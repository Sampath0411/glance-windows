"""DPAPI sealing for on-disk sensitive data (user scope).

The Windows Data Protection API is used with its default USER scope, so only
the Windows account that sealed a blob can unseal it. Single-purpose entropy
binds blobs to Glance's face store.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_CRYPTPROTECT_UI_FORBIDDEN = 0x1

_ENTROPY = b"GlanceFaces/v1"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR,
    ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
]
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPCWSTR),
    ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
_kernel32.LocalFree.restype = wintypes.HLOCAL


def _blob(data: bytes) -> _DATA_BLOB:
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return _DATA_BLOB(len(data), buffer)


def _to_bytes(blob: _DATA_BLOB) -> bytes:
    if blob.cbData == 0 or not blob.pbData:
        return b""
    return ctypes.string_at(blob.pbData, blob.cbData)


def protect(data: bytes) -> bytes:
    """Seal bytes with DPAPI user scope; returns the opaque blob."""
    if not data:
        raise ValueError("cannot protect empty data")
    plain = _blob(data)
    entropy = _blob(_ENTROPY)
    out = _DATA_BLOB()
    if not _crypt32.CryptProtectData(
        ctypes.byref(plain), None, ctypes.byref(entropy),
        None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out),
    ):
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    try:
        return _to_bytes(out)
    finally:
        _kernel32.LocalFree(out.pbData)


def unprotect(blob: bytes) -> bytes:
    """Unseal a DPAPI blob; raises OSError for any failure (wrong account,
    corruption, unavailable data)."""
    if not blob:
        raise ValueError("cannot unprotect empty data")
    sealed = _blob(blob)
    entropy = _blob(_ENTROPY)
    out = _DATA_BLOB()
    if not _crypt32.CryptUnprotectData(
        ctypes.byref(sealed), None, ctypes.byref(entropy),
        None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out),
    ):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    try:
        return _to_bytes(out)
    finally:
        _kernel32.LocalFree(out.pbData)
