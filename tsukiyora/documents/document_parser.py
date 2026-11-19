"""Small, dependency-free extractors for explicitly selected documents.

DOCX: WordprocessingML text/paragraphs in document.xml, no macros/external
resources. PDF: basic machine-readable text streams, NOT a general PDF engine.
Scanned, encrypted, complex font/CMap and image-only PDFs are rejected rather
than silently claiming complete extraction. Never executes embedded content.
"""
from __future__ import annotations

import io
import re
import zipfile
import zlib
import xml.etree.ElementTree as ET

MAX_BINARY = 1_000_000
MAX_OUTPUT = 44000
PDF_STREAM_LIMIT = 2_000_000


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 200 or sum(x.file_size for x in entries) > 5_000_000:
                raise ValueError("DOCX содержит слишком много данных.")
            path = "word/document.xml"
            if path not in archive.namelist():
                raise ValueError("DOCX не содержит основного текста.")
            record = archive.getinfo(path)
            if record.file_size > 2_000_000:
                raise ValueError("Слишком большой XML внутри DOCX.")
            xml = archive.read(path)
    except (zipfile.BadZipFile, RuntimeError, OSError):
        raise ValueError("DOCX повреждён или зашифрован.") from None
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise ValueError("Внешние сущности XML не поддерживаются.")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ValueError("Не удалось разобрать структуру DOCX.") from None
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(ns+"p"):
        parts = []
        for tag in paragraph.iter():
            if tag.tag == ns+"t":
                parts.append(tag.text or "")
            elif tag.tag == ns+"tab":
                parts.append("\t")
            elif tag.tag in (ns+"br", ns+"cr"):
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
        if sum(map(len,paragraphs)) > MAX_OUTPUT:
            raise ValueError("Текст документа превышает 44 000 символов.")
    result = "\n".join(paragraphs)
    if not result.strip():
        raise ValueError("В DOCX не обнаружен читаемый текст.")
    return result


PDF_LITERAL = re.compile(rb"\((?:\\.|[^\\)])*\)")
PDF_HEX = re.compile(rb"<([0-9A-Fa-f\s]+)>")
PDF_TEXT = re.compile(rb"(\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]+>)\s*Tj\b")
PDF_ARRAY = re.compile(rb"\[(.{1,50000}?)\]\s*TJ\b", re.S)
PDF_STREAM = re.compile(rb"<<(.*?)>>\s*stream\r?\n(.*?)\r?\nendstream", re.S)


def _pdf_unescape(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] != 92:
            out.append(data[i]); i += 1; continue
        i += 1
        if i >= len(data):
            break
        ch = data[i]
        replacements = {ord("n"):10, ord("r"):13, ord("t"):9,
                        ord("b"):8, ord("f"):12}
        if ch in replacements:
            out.append(replacements[ch]); i += 1
        elif ch in (10,13):
            i += 1
            if ch == 13 and i < len(data) and data[i] == 10:
                i += 1
        elif 48 <= ch <= 55:
            j = i
            while i < len(data) and i-j < 3 and 48 <= data[i] <= 55:
                i += 1
            out.append(int(data[j:i],8)&255)
        else:
            out.append(ch); i += 1
    return bytes(out)


def _pdf_decode(token: bytes) -> str:
    if token.startswith(b"("):
        raw = _pdf_unescape(token[1:-1])
    else:
        raw = bytes.fromhex(re.sub(rb"\s+", b"", token[1:-1]).decode("ascii"))
    if raw.startswith((b"\xfe\xff", b"\xff\xfe")):
        return raw.decode("utf-16", "replace")
    if len(raw)>=4 and raw[0::2].count(0)>len(raw)//4:
        return raw.decode("utf-16-be","replace")
    return raw.decode("cp1252","replace")


def _pdf_text(data: bytes) -> str:
    if not data.startswith(b"%PDF-"):
        raise ValueError("Неверный заголовок PDF.")
    if re.search(rb"/Encrypt\b",data):
        raise ValueError("Зашифрованные PDF не поддерживаются.")
    parts = []
    for meta, stream in PDF_STREAM.findall(data):
        if len(stream)>MAX_BINARY:
            continue
        if b"/FlateDecode" in meta:
            try:
                dec=zlib.decompressobj()
                stream=dec.decompress(stream,PDF_STREAM_LIMIT+1)
                if len(stream)>PDF_STREAM_LIMIT or not dec.eof:
                    continue
            except zlib.error:
                continue
        elif b"/Filter" in meta:
            continue
        for token in PDF_TEXT.findall(stream):
            value=_pdf_decode(token).strip()
            if value:
                parts.append(value)
        for series in PDF_ARRAY.findall(stream):
            words=[]
            for token in PDF_LITERAL.findall(series):
                words.append(_pdf_decode(token))
            for token in PDF_HEX.findall(series):
                words.append(_pdf_decode(b"<"+token+b">"))
            line="".join(words).strip()
            if line:
                parts.append(line)
        if sum(len(p) for p in parts)>MAX_OUTPUT:
            raise ValueError("PDF содержит слишком много текста.")
    result="\n".join(parts)
    if len(result.strip())<3:
        raise ValueError(
            "Не удалось извлечь текст PDF. Скан, сложные шрифты или другой "
            "формат требуют полноценного PDF-парсера/OCR.")
    return result


def extract_document(filename: str, data: bytes) -> dict:
    if not isinstance(filename,str) or not isinstance(data,bytes):
        raise ValueError("Неверный документ.")
    if not 1<=len(data)<=MAX_BINARY:
        raise ValueError("Файл должен иметь размер от 1 байта до 1 МБ.")
    extension=filename.rsplit(".",1)[-1].lower()
    if extension in ("txt","md"):
        try:
            text=data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("TXT/Markdown должен быть в UTF-8.") from None
        method="utf8"
    elif extension=="docx":
        text=_docx_text(data)
        method="docx_wordprocessingml"
    elif extension=="pdf":
        text=_pdf_text(data)
        method="basic_pdf_text_streams"
    else:
        raise ValueError("Поддерживаются TXT, MD, DOCX и текстовые PDF.")
    if not 1<=len(text.strip())<=MAX_OUTPUT:
        raise ValueError("Извлечённый текст слишком велик или пуст.")
    return {"text":text,"method":method,"input_bytes":len(data),
            "complete_extraction_guaranteed":extension in ("txt","md")}
