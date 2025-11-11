#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, glob, shutil
from typing import Optional, List
from unidecode import unidecode
from dotenv import load_dotenv
from pathlib import Path

# ---------------------- Config ----------------------

SEARCH_WINDOW_NEXT = 12  # ahora miramos hasta 12 líneas debajo del label


# --- Carga robusta del .env ---
ROOT = Path(__file__).resolve().parent
dotenv_file = ROOT / ".env"
# Fuerzo ruta + codificación UTF-8 y permito override
load_dotenv(dotenv_path=dotenv_file, encoding="utf-8", override=True)

# Si no hay variable, default a ./facturas_pdf dentro del proyecto
DEFAULT_PATH = os.getenv("PDF_INPUT_PATH") or str(ROOT / "facturas_pdf")


# ---------------------- Extracción de texto ----------------------
def extract_text_pypdf(pdf_path: str) -> Optional[str]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        return "\n".join([(p.extract_text() or "") for p in reader.pages])
    except Exception:
        return None

def extract_text_pdfminer(pdf_path: str) -> Optional[str]:
    try:
        from pdfminer.high_level import extract_text
        return extract_text(pdf_path)
    except Exception:
        return None

def extract_text(pdf_path: str) -> str:
    text = extract_text_pypdf(pdf_path) or extract_text_pdfminer(pdf_path)
    if not text:
        raise RuntimeError(f"No se pudo extraer texto de: {pdf_path}")
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{2,}", "\n", text)
    return text

# ---------------------- Detección del cliente ----------------------
LABEL_PATTERN = re.compile(
    r"Apellido\s*y\s*Nombre\s*/\s*Raz[oó]n\s*Social\s*:?\s*(.+)$", re.IGNORECASE
)
LABEL_ONLY_PATTERN = re.compile(
    r"^Apellido\s*y\s*Nombre\s*/\s*Raz[oó]n\s*Social\s*:?\s*$", re.IGNORECASE
)
CUIT_LABEL = re.compile(r"\bCUIT\b\s*:?", re.IGNORECASE)

STOP_LABELS = re.compile(
    r"\b(Domicilio|Condici[oó]n|Punto\s*de\s*Venta|Comprobante|Per[ií]odo|Fecha|CAE|Ingresos\s*Brutos)\b",
    re.IGNORECASE,
)

COMPANY_SUFFIX = re.compile(r"\b(S\.?A\.?|S\.?R\.?L\.?|SAS|SAU|SAIC|SAICyF|SAICF|U\.?T\.?E\.?)\b", re.IGNORECASE)

def is_viable_name(s: str) -> bool:
    # Debe tener al menos dos tokens alfabéticos (permite siglas con puntos)
    tokens = re.findall(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü]{2,}", s)
    return len(tokens) >= 2

def strip_trailing_labels(s: str) -> str:
    # corta si aparecen otras etiquetas a continuación
    s = s.split("  ")[0].strip()
    if STOP_LABELS.search(s):
        s = STOP_LABELS.split(s, maxsplit=1)[0].strip()
    return s

def find_after_label_flexible(lines: List[str], start_idx: int) -> Optional[str]:
    """
    Desde el label, escanea hasta SEARCH_WINDOW_NEXT líneas.
    Ignora otros labels intermedios y, cuando encuentra una línea viable,
    concatena hasta 2 líneas siguientes si parecen continuar el nombre.
    """
    for j in range(start_idx + 1, min(start_idx + 1 + SEARCH_WINDOW_NEXT, len(lines))):
        cand = lines[j].strip()
        if not cand:
            continue
        if STOP_LABELS.search(cand):
            # no cortar: saltar y seguir buscando dentro de la ventana
            continue

        # primera línea candidata
        chunk = strip_trailing_labels(cand)
        if not is_viable_name(chunk):
            continue

        # concatenar hasta 2 líneas más si continúan el nombre
        k = 0
        t = j + 1
        while k < 2 and t < len(lines):
            nxt = lines[t].strip()
            if not nxt:
                t += 1
                continue
            if STOP_LABELS.search(nxt):
                break
            nxt2 = strip_trailing_labels(nxt)
            if not nxt2:
                break
            chunk = (chunk + " " + nxt2).strip()
            k += 1
            t += 1

        if is_viable_name(chunk):
            return chunk

    return None


def detect_client_name(text: str, debug: bool = False) -> Optional[str]:
    lines = [ln.strip() for ln in text.split("\n") if ln is not None]
    if debug:
        print("---- DEBUG: primeras 80 líneas ----")
        for i, ln in enumerate(lines[:80]):
            print(f"{i:02d}: {ln}")

    # 1) Label y valor en la misma línea (con posible continuación)
    for idx, ln in enumerate(lines):
        m = LABEL_PATTERN.search(ln)
        if m:
            tail = m.group(1).strip()
            chunk = strip_trailing_labels(tail)

            # concatenar hasta 2 líneas si sigue el nombre
            k = 0
            j = idx + 1
            while k < 2 and j < len(lines):
                nxt = lines[j].strip()
                if not nxt:
                    j += 1
                    continue
                if STOP_LABELS.search(nxt):
                    break
                chunk = (chunk + " " + strip_trailing_labels(nxt)).strip()
                k += 1
                j += 1

            if is_viable_name(chunk):
                return chunk

    # 2) Label solo, nombre en líneas siguientes (usa la versión flexible)
    for i, ln in enumerate(lines):
        if LABEL_ONLY_PATTERN.search(ln):
            cand = find_after_label_flexible(lines, i)
            if cand:
                return cand

    # 3) Cerca del CUIT (escaneo de respaldo)
    cuit_positions = [i for i, ln in enumerate(lines) if CUIT_LABEL.search(ln)]
    for pos in cuit_positions:
        for j in range(pos, min(pos + 1 + SEARCH_WINDOW_NEXT, len(lines))):
            m = LABEL_PATTERN.search(lines[j])
            if m:
                tail = strip_trailing_labels(m.group(1).strip())
                if is_viable_name(tail):
                    return tail

    # 4) Último intento: líneas con sufijo societario
    candidates = []
    for ln in lines:
        if COMPANY_SUFFIX.search(ln):
            s = strip_trailing_labels(ln)
            if is_viable_name(s):
                candidates.append(s)
    if candidates:
        candidates.sort(key=len, reverse=True)
        return candidates[0]

    return None

# ---------------------- Utilidades ----------------------
def sanitize_for_filename(name: str, maxlen: int = 80) -> str:
    name = unidecode(name)
    name = re.sub(r"[^\w\s\-\.\&]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:maxlen].strip(" ._-")

def build_new_name(pdf_path: str, client: str) -> str:
    base, ext = os.path.splitext(os.path.basename(pdf_path))
    safe = sanitize_for_filename(client)
    if not safe:
        raise ValueError("No se pudo sanear el nombre del cliente.")
    new_base = base if safe.lower() in base.lower() else f"{base} - {safe}"
    return os.path.join(os.path.dirname(pdf_path), new_base + ext)

def rename_pdf(pdf_path: str, dry_run: bool = True, debug: bool = False) -> str:
    text = extract_text(pdf_path)
    client = detect_client_name(text, debug=debug)
    if not client:
        raise ValueError("No se encontró el nombre del cliente en el PDF.")
    target = build_new_name(pdf_path, client)
    if dry_run:
        return f"[DRY-RUN] {os.path.basename(pdf_path)} -> {os.path.basename(target)}"
    final_target = target
    i = 2
    while os.path.exists(final_target):
        base, ext = os.path.splitext(target)
        final_target = f"{base} ({i}){ext}"
        i += 1
    shutil.move(pdf_path, final_target)
    return f"Renombrado: {os.path.basename(pdf_path)} -> {os.path.basename(final_target)}"

# ---------------------- CLI ----------------------
def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Renombra facturas PDF agregando el nombre del cliente (layout con 'Apellido y Nombre / Razón Social')."
    )
    ap.add_argument("--apply", action="store_true", help="Aplica cambios (por defecto dry-run).")
    ap.add_argument("--path", type=str, default=DEFAULT_PATH,
                    help="Ruta de facturas (por defecto toma PDF_INPUT_PATH del .env).")
    ap.add_argument("--debug", action="store_true", help="Muestra líneas leídas para diagnóstico.")
    args = ap.parse_args()

    path = os.path.abspath(args.path)
    if not os.path.isdir(path):
        print(f"❌ Ruta inválida o inexistente: {path}")
        sys.exit(2)

    files = glob.glob(os.path.join(path, "*.pdf"))
    if not files:
        print(f"No se encontraron archivos PDF en {path}")
        sys.exit(0)

    print(f"📂 Procesando {len(files)} archivos en: {path}")
    for f in files:
        try:
            msg = rename_pdf(f, dry_run=not args.apply, debug=args.debug)
            print(msg)
        except Exception as e:
            print(f"❌ {os.path.basename(f)}: {e}")

if __name__ == "__main__":
    main()
