"""Handle-based Windows protection for the permanent settlement ledger.

POSIX mode bits and uid checks cannot establish NTFS ownership. Check the
owner SID and DACL on the opened object, reject reparse points, and keep the
directory open without delete sharing while the ledger lock is held. Writes
use FILE_FLAG_WRITE_THROUGH; the caller also fsyncs each complete record.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _api() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    pointer = ctypes.c_void_p
    out_pointer = ctypes.POINTER(pointer)
    declarations = (
        (
            kernel.CreateFileW,
            wintypes.HANDLE,
            [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                pointer,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ],
        ),
        (kernel.CloseHandle, wintypes.BOOL, [wintypes.HANDLE]),
        (kernel.CreateDirectoryW, wintypes.BOOL, [wintypes.LPCWSTR, pointer]),
        (kernel.LocalFree, pointer, [pointer]),
        (kernel.GetCurrentProcess, wintypes.HANDLE, []),
        (
            kernel.GetFileInformationByHandleEx,
            wintypes.BOOL,
            [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD],
        ),
        (
            security.OpenProcessToken,
            wintypes.BOOL,
            [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)],
        ),
        (
            security.GetTokenInformation,
            wintypes.BOOL,
            [
                wintypes.HANDLE,
                ctypes.c_int,
                pointer,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ],
        ),
        (security.ConvertSidToStringSidW, wintypes.BOOL, [pointer, out_pointer]),
        (
            security.ConvertStringSecurityDescriptorToSecurityDescriptorW,
            wintypes.BOOL,
            [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                out_pointer,
                ctypes.POINTER(wintypes.DWORD),
            ],
        ),
        (
            security.GetSecurityInfo,
            wintypes.DWORD,
            [
                wintypes.HANDLE,
                ctypes.c_int,
                wintypes.DWORD,
                out_pointer,
                out_pointer,
                out_pointer,
                out_pointer,
                out_pointer,
            ],
        ),
        (security.GetAce, wintypes.BOOL, [pointer, wintypes.DWORD, out_pointer]),
    )
    for function, result, arguments in declarations:
        function.restype = result
        function.argtypes = arguments
    return kernel, security


def _sid_text(sid: ctypes.c_void_p | int) -> str:
    kernel, security = _api()
    text = ctypes.c_void_p()
    if not security.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.wstring_at(text)
    finally:
        kernel.LocalFree(text)


@lru_cache(maxsize=2)
def _token_sid(information_class: int) -> str:
    kernel, security = _api()
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(
        kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        security.GetTokenInformation(
            token, information_class, None, 0, ctypes.byref(size)
        )
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(
            token, information_class, buffer, size, ctypes.byref(size)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        # TOKEN_USER and TOKEN_OWNER both start with a PSID.
        return _sid_text(ctypes.c_void_p.from_buffer(buffer))
    finally:
        kernel.CloseHandle(token)


def _validate(handle: int, *, label: str, directory: bool) -> None:
    kernel, security = _api()
    attributes = (wintypes.DWORD * 2)()
    # FileAttributeTagInfo: FileAttributes, ReparseTag.
    if not kernel.GetFileInformationByHandleEx(
        handle, 9, attributes, ctypes.sizeof(attributes)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if attributes[0] & 0x400:
        raise PermissionError(f"{label} must not be a reparse point")
    if bool(attributes[0] & 0x10) != directory:
        raise PermissionError(f"{label} has the wrong file type")
    owner, dacl, descriptor = (ctypes.c_void_p() for _ in range(3))
    result = security.GetSecurityInfo(
        handle,
        1,
        0x5,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result:
        raise ctypes.WinError(result)
    try:
        current = _token_sid(1)
        default_owner = _token_sid(4)
        privileged = {"S-1-5-18", "S-1-5-32-544"}  # SYSTEM, Administrators
        owners = {current}
        # Elevated administrators create objects owned by their TokenOwner
        # group. Admit that owner only when the active token selects it; never
        # accept an arbitrary group owner as a substitute for private state.
        if default_owner in privileged:
            owners.add(default_owner)
        if not owner.value or _sid_text(owner) not in owners:
            raise PermissionError(f"{label} is not owned by current user")
        if not dacl.value:
            raise PermissionError(f"{label} has an unrestricted DACL")
        # ACL's fixed header contains AceCount at byte offset 4.
        count = ctypes.c_uint16.from_address(dacl.value + 4).value
        trusted = {current, *privileged}
        for index in range(count):
            ace = ctypes.c_void_p()
            if not security.GetAce(dacl, index, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            kind, flags = ctypes.string_at(ace, 2)
            if flags & 0x8 or kind == 1:  # inherit-only or ACCESS_DENIED_ACE
                continue
            if kind != 0:  # fail closed on unhandled conditional/object ACEs
                raise PermissionError(f"{label} has an unsupported DACL entry")
            mask = ctypes.c_uint32.from_address(ace.value + 4).value
            # Generic write/all, ownership/DACL/delete and every file/directory write bit.
            if mask & 0x500D0156 and _sid_text(ace.value + 8) not in trusted:
                raise PermissionError(f"{label} is writable by another principal")
    finally:
        kernel.LocalFree(descriptor)


def _open(path: Path, *, writable: bool, create: bool, directory: bool) -> int:
    kernel, _ = _api()
    access = 0x80000000 | (0x40000000 if writable else 0)
    # Deny rename/delete for the life of the handle; allow cooperating readers/writers.
    attributes = 0x00200000 | 0x02000000  # OPEN_REPARSE_POINT | BACKUP_SEMANTICS
    if writable:
        attributes |= 0x80000000  # WRITE_THROUGH flushes data and NTFS metadata
    handle = kernel.CreateFileW(
        str(path), access, 0x3, None, 4 if create else 3, attributes, None
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        _validate(handle, label=str(path), directory=directory)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    return handle


@contextmanager
def directory_guard(path: Path) -> Iterator[None]:
    """Validate and pin the ledger directory until its operation finishes."""
    handle = _open(path, writable=False, create=False, directory=True)
    try:
        yield
    finally:
        _api()[0].CloseHandle(handle)


def create_private_directory(path: Path) -> None:
    """Create with a protected DACL; never rewrite an existing object's ACL."""
    kernel, security = _api()
    current = _token_sid(1)
    # Only this user, SYSTEM and Administrators can modify new state. OI/CI
    # carries that policy to new ledger/lock files and snapshot descendants.
    sddl = f"O:{current}D:P(A;OICI;FA;;;{current})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
    descriptor = ctypes.c_void_p()
    if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD),
            ("descriptor", ctypes.c_void_p),
            ("inherit_handle", wintypes.BOOL),
        ]

    attributes = SecurityAttributes(
        ctypes.sizeof(SecurityAttributes), descriptor, False
    )
    try:
        if not kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
            error = ctypes.get_last_error()
            if error != 183:  # ERROR_ALREADY_EXISTS: subsequent validation is mandatory
                raise ctypes.WinError(error)
    finally:
        kernel.LocalFree(descriptor)


def open_private_file(path: Path, flags: int) -> int:
    """Return a binary CRT fd for an atomically opened, validated NTFS file."""
    import msvcrt

    handle = _open(
        path,
        writable=bool(flags & (os.O_RDWR | os.O_WRONLY)),
        create=bool(flags & os.O_CREAT),
        directory=False,
    )
    try:
        return msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
    except BaseException:
        _api()[0].CloseHandle(handle)
        raise


def validate_private_file(fd: int, *, label: str) -> None:
    """Recheck the same descriptor, never a later pathname lookup."""
    import msvcrt

    _validate(msvcrt.get_osfhandle(fd), label=label, directory=False)
