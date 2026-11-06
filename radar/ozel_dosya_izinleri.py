"""Doğrulanmış yerel dosya veya dizini POSIX moduyla ya da korumalı Windows DACL ile sınırlar."""

import ctypes
import os


def ozel_dosya_izni(path, directory=False):
    """Yol/bağlantı denetimi çağırandadır; izin hataları özel yolu açıklamaz.

    Windows: sahip, SYSTEM ve yöneticiler; dizinde yeni çocuklara miras verilir.
    POSIX: dizin 0700, dosya 0600. Mevcut alt dosyaları dolaşmaz; yedekleri
    değiştirmez. Özel dizin dosya/journal oluşturulmadan önce sınırlandırılmalı.
    """
    try:
        if os.name != "nt":
            os.chmod(path, 0o700 if directory else 0o600)
            return
        from ctypes import wintypes
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
        convert.restype = wintypes.BOOL
        apply = advapi.SetFileSecurityW
        apply.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
        apply.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        flags = "OICI" if directory else ""
        descriptor = ctypes.c_void_p()
        sddl = "D:P" + "".join(f"(A;{flags};FA;;;{sid})" for sid in ("SY", "BA", "OW"))
        if not convert(sddl, 1, ctypes.byref(descriptor), None):
            raise OSError()
        try:
            if not apply(str(path), 0x00000004 | 0x80000000, descriptor):
                raise OSError()
        finally:
            kernel.LocalFree(descriptor)
    except (OSError, ValueError):
        raise OSError("Özel dosya veya dizin izinleri uygulanamadı.") from None
