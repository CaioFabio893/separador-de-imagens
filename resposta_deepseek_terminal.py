"""Entrega somente a resposta numerica do DeepSeek ao console do robo.

Nao abre arquivos do robo, nao usa o clipboard e nao inicia processos.
"""
from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
import logging
import os
import re
import sys
import threading
import time

_LOG = logging.getLogger(__name__)
_ENVIO = threading.Lock()
_ROBO = os.path.normcase(os.path.join(os.path.dirname(__file__), "imagem", "ROBO.bat"))


class _Processo(ct.Structure):
    _fields_ = [
        ("size", wt.DWORD), ("usage", wt.DWORD), ("pid", wt.DWORD),
        ("heap", ct.c_size_t), ("module", wt.DWORD), ("threads", wt.DWORD),
        ("parent", wt.DWORD), ("priority", wt.LONG), ("flags", wt.DWORD),
        ("exe", wt.WCHAR * 260),
    ]


class _Texto(ct.Structure):
    _fields_ = [("length", wt.USHORT), ("maximum", wt.USHORT), ("buffer", ct.c_void_p)]


class _Tecla(ct.Structure):
    _fields_ = [
        ("down", wt.BOOL), ("repeat", wt.WORD), ("key", wt.WORD),
        ("scan", wt.WORD), ("char", wt.WCHAR), ("control", wt.DWORD),
    ]


class _Evento(ct.Union):
    _fields_ = [("key", _Tecla), ("padding", wt.DWORD * 4)]


class _Entrada(ct.Structure):
    _fields_ = [("kind", wt.WORD), ("event", _Evento)]


def _api():
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    native = ct.WinDLL("ntdll")
    signatures = {
        "CreateToolhelp32Snapshot": ([wt.DWORD, wt.DWORD], wt.HANDLE),
        "Process32FirstW": ([wt.HANDLE, ct.POINTER(_Processo)], wt.BOOL),
        "Process32NextW": ([wt.HANDLE, ct.POINTER(_Processo)], wt.BOOL),
        "OpenProcess": ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
        "CloseHandle": ([wt.HANDLE], wt.BOOL),
        "GetConsoleProcessList": ([ct.POINTER(wt.DWORD), wt.DWORD], wt.DWORD),
        "AttachConsole": ([wt.DWORD], wt.BOOL),
        "FreeConsole": ([], wt.BOOL),
        "GetStdHandle": ([wt.DWORD], wt.HANDLE),
        "SetStdHandle": ([wt.DWORD, wt.HANDLE], wt.BOOL),
        "CreateFileW": ([wt.LPCWSTR, wt.DWORD, wt.DWORD, ct.c_void_p,
                         wt.DWORD, wt.DWORD, wt.HANDLE], wt.HANDLE),
        "WriteConsoleInputW": ([wt.HANDLE, ct.POINTER(_Entrada), wt.DWORD,
                                ct.POINTER(wt.DWORD)], wt.BOOL),
        "WaitForSingleObject": ([wt.HANDLE, wt.DWORD], wt.DWORD),
    }
    for name, (args, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    native.NtQueryInformationProcess.argtypes = [
        wt.HANDLE, wt.ULONG, ct.c_void_p, wt.ULONG, ct.POINTER(wt.ULONG)]
    native.NtQueryInformationProcess.restype = wt.LONG
    return kernel, native


def _comando(kernel, native, pid):
    """Consulta apenas os argumentos do processo; nenhum arquivo e aberto."""
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        size = wt.ULONG()
        native.NtQueryInformationProcess(handle, 60, None, 0, ct.byref(size))
        if not ct.sizeof(_Texto) <= size.value <= 131072:
            return ""
        data = ct.create_string_buffer(size.value)
        if native.NtQueryInformationProcess(
                handle, 60, data, size.value, ct.byref(size)) < 0:
            return ""
        value = _Texto.from_buffer(data)
        start = ct.addressof(data)
        if (not value.buffer or value.length % 2
                or not start <= value.buffer <= start + len(data) - value.length):
            return ""
        return ct.wstring_at(value.buffer, value.length // 2)
    finally:
        kernel.CloseHandle(handle)


def _localizar(kernel, native):
    """Exige um unico Python filho do ROBO.bat desta pasta."""
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ct.c_void_p(-1).value:
        raise ct.WinError(ct.get_last_error())
    processes = []
    try:
        entry = _Processo()
        entry.size = ct.sizeof(entry)
        found = kernel.Process32FirstW(snapshot, ct.byref(entry))
        while found:
            if entry.exe.lower() in ("cmd.exe", "python.exe", "pythonw.exe", "py.exe"):
                processes.append((entry.pid, entry.parent, entry.exe.lower()))
            found = kernel.Process32NextW(snapshot, ct.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    parents = set()
    for pid, _, exe in processes:
        if exe == "cmd.exe":
            command = os.path.normcase(_comando(kernel, native, pid))
            if ('"' + _ROBO + '"') in command:
                parents.add(pid)
    # py.exe pode intermediar o Python, sem ser o consumidor da resposta.
    launchers = {pid for pid, parent, exe in processes
                 if parent in parents and exe == "py.exe"}
    candidates = []
    for pid, parent, exe in processes:
        if parent not in parents | launchers or exe not in ("python.exe", "pythonw.exe"):
            continue
        command = _comando(kernel, native, pid)
        if re.search(r'(?:^|[\s"\\/])robo_ecac\.py(?=$|[\s"])', command, re.I):
            candidates.append(pid)
    if len(candidates) > 1:
        raise RuntimeError("Mais de um robo aberto; resposta preservada no TXT.")
    return candidates[0] if candidates else None


def _membros(kernel):
    size = 16
    while True:
        values = (wt.DWORD * size)()
        count = kernel.GetConsoleProcessList(values, size)
        if count <= size:
            return list(values[:count])
        size = count


def _eventos(text):
    events = (_Entrada * (2 * (len(text) + 1)))()
    for index, char in enumerate(text + "\r"):
        for offset, down in enumerate((True, False)):
            event = events[2 * index + offset]
            event.kind = 1
            event.event.key = _Tecla(down, 1, 13 if char == "\r" else 0, 0, char, 0)
    return events


def _entregar(kernel, pid, text):
    """Escreve no console identificado, sem depender da janela em foco."""
    process = kernel.OpenProcess(0x101000, False, pid)
    if not process:
        raise ct.WinError(ct.get_last_error())
    previous = _membros(kernel)
    restore = [member for member in previous if member != os.getpid()]
    if previous and not restore:
        kernel.CloseHandle(process)
        raise RuntimeError("Console exclusivo do servidor; entrega cancelada sem altera-lo.")
    standard = [(kind, kernel.GetStdHandle(kind)) for kind in (-10, -11, -12)]
    attached = False
    handle = None
    detached = False
    try:
        if previous:
            if not kernel.FreeConsole():
                raise ct.WinError(ct.get_last_error())
            detached = True
        if not kernel.AttachConsole(pid):
            raise ct.WinError(ct.get_last_error())
        attached = True
        if pid not in _membros(kernel) or kernel.WaitForSingleObject(process, 0) != 258:
            raise RuntimeError("O robo encerrou antes de receber a resposta.")
        handle = kernel.CreateFileW("CONIN$", 0x40000000, 3, None, 3, 0, None)
        if handle == ct.c_void_p(-1).value:
            handle = None
            raise ct.WinError(ct.get_last_error())
        events = _eventos(text)
        written = wt.DWORD()
        if not kernel.WriteConsoleInputW(handle, events, len(events), ct.byref(written)):
            raise ct.WinError(ct.get_last_error())
        if written.value != len(events):
            raise RuntimeError("Envio parcial; nao repetir automaticamente.")
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)
        if attached:
            kernel.FreeConsole()
        if detached:
            if not any(kernel.AttachConsole(member) for member in restore):
                _LOG.error("Nao foi possivel restaurar o console do servidor.")
        for kind, value in standard:
            kernel.SetStdHandle(kind, value)
        kernel.CloseHandle(process)


def encaminhar(texto):
    """Agenda uma entrega no proprio servidor, depois que o TXT foi salvo."""
    if sys.platform != "win32":
        return
    # Apenas uma linha numerica: nunca interpretar comandos ou texto livre.
    linha = texto.strip()
    if len(linha) > 128 or not re.fullmatch(r"[1-9](?:[ ,;]+[1-9])*", linha):
        _LOG.warning("Resposta fora do formato numerico; consulte o TXT.")
        return
    if not _ENVIO.acquire(blocking=False):
        _LOG.warning("Ja existe uma entrega pendente; nova resposta preservada no TXT.")
        return

    def enviar():
        try:
            kernel, native = _api()
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                pid = _localizar(kernel, native)
                if pid is not None:
                    # Nunca repetir depois de uma tentativa de escrita.
                    _entregar(kernel, pid, linha)
                    _LOG.info("Resposta do DeepSeek enviada ao terminal do robo com Enter.")
                    return
                time.sleep(1)
            _LOG.warning("Terminal do robo nao encontrado; resposta preservada no TXT.")
        except Exception:
            _LOG.exception("Falha ao entregar a resposta ao terminal; TXT preservado.")
        finally:
            _ENVIO.release()

    try:
        threading.Thread(target=enviar, name="entrega-resposta-deepseek", daemon=True).start()
    except Exception:
        _ENVIO.release()
        raise
