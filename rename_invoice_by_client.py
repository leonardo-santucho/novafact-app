#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import glob
import shutil
from pathlib import Path
from typing import Optional, List

from dotenv import load_dotenv
from unidecode import unidecode

# ====================== Configuración ======================

VENTANA_BUSQUEDA_SIGUIENTES = 12  # líneas a escanear debajo del rótulo

# Carga robusta del .env (siempre desde la carpeta del script)
RUTA_PROYECTO = Path(__file__).resolve().parent
ARCHIVO_ENV = RUTA_PROYECTO / ".env"
load_dotenv(dotenv_path=ARCHIVO_ENV, encoding="utf-8", override=True)

# Variables configurables
RUTA_POR_DEFECTO = os.getenv("PDF_INPUT_PATH") or str(RUTA_PROYECTO / "facturas_pdf")
RUTA_SALIDA = os.getenv("PDF_OUTPUT_PATH") or str(RUTA_PROYECTO / "facturas_renombradas")
os.makedirs(RUTA_SALIDA, exist_ok=True)

LIMITE_NOMBRE_CLIENTE = int(os.getenv("PDF_CLIENT_NAME_MAXLEN", "80"))
FORMATO_NOMBRE_ARCHIVO = os.getenv("PDF_FILENAME_FORMAT", "YYYYMMDD_NOMBRE_CLIENTE").upper()
FORZAR_LAYOUT = (os.getenv("PDF_FORCE_LAYOUT") or "AUTO").upper()  # AUTO | AFIP_MONO | PROV_B

# =================== Extracción de texto ===================

def extraer_texto_pypdf(ruta_pdf: str) -> Optional[str]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(ruta_pdf)
        return "\n".join([(p.extract_text() or "") for p in reader.pages])
    except Exception:
        return None

def extraer_texto_pdfminer(ruta_pdf: str) -> Optional[str]:
    try:
        from pdfminer.high_level import extract_text
        return extract_text(ruta_pdf)
    except Exception:
        return None

def extraer_texto(ruta_pdf: str) -> str:
    texto = extraer_texto_pypdf(ruta_pdf) or extraer_texto_pdfminer(ruta_pdf)
    if not texto:
        raise RuntimeError(f"No se pudo extraer texto de: {ruta_pdf}")
    texto = texto.replace("\r", "\n")
    texto = re.sub(r"\n{2,}", "\n", texto)
    return texto

# ================== Patrones / Heurísticas ==================

PATRON_ETIQUETA_INLINE = re.compile(
    r"Apellido\s*y\s*Nombre\s*/\s*Raz[oó]n\s*Social\s*:?\s*(.+)$", re.IGNORECASE)
PATRON_ETIQUETA_SOLO = re.compile(
    r"^Apellido\s*y\s*Nombre\s*/\s*Raz[oó]n\s*Social\s*:?\s*$", re.IGNORECASE)
PATRON_RAZON_INLINE = re.compile(r"Raz[oó]n\s*Social\s*:?\s*(.+)$", re.IGNORECASE)
PATRON_RAZON_SOLO = re.compile(r"^Raz[oó]n\s*Social\s*:?\s*$", re.IGNORECASE)

PATRONES_CORTE = re.compile(
    r"\b(Domicilio|Condici[oó]n|Punto\s*de\s*Venta|Comprobante|Per[ií]odo|Fecha|CAE|Ingresos\s*Brutos)\b",
    re.IGNORECASE,
)

SUFIJOS_SOCIALES = re.compile(
    r"\b(S\.?A\.?|S\.?R\.?L\.?|SAS|SAU|SAIC|SAICyF|SAICF|U\.?T\.?E\.?)\b", re.IGNORECASE)

INDICIOS_DIRECCION = re.compile(
    r"\d|,| - |\b(Domicilio|Calle|Av\.?|Avenida|Piso|Depto|Capital|Buenos\s*Aires|Provincia|CP|Código\s*Postal)\b",
    re.IGNORECASE,
)

def parece_direccion(s: str) -> bool:
    return bool(INDICIOS_DIRECCION.search(s))

# =================== Utilidades de parsing ===================

def es_nombre_viable(s: str) -> bool:
    tokens = re.findall(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü]{2,}", s)
    return len(tokens) >= 2

def cortar_en_siguientes_etiquetas(s: str) -> str:
    s = s.split("  ")[0].strip()
    if PATRONES_CORTE.search(s):
        s = PATRONES_CORTE.split(s, maxsplit=1)[0].strip()
    return s

# ================== Detección de Layout ==================

def determinar_layout(lineas: List[str]) -> str:
    """
    Reglas:
      - AFIP_MONO: si aparece 'Apellido y Nombre / Razón Social'
      - PROV_B   : si aparece 'Razón Social:' y hay indicios de factura de proveedor
                   (Comprobantes asociados, C.U.I.T., Código 00x, Factura A/B, Nº 0010-...)
      - UNKNOWN  : si no hay señales claras
    """
    texto = "\n".join(lineas)

    if any(PATRON_ETIQUETA_SOLO.search(ln) or PATRON_ETIQUETA_INLINE.search(ln) for ln in lineas):
        return "AFIP_MONO"

    conteo_razon = sum(1 for ln in lineas if PATRON_RAZON_INLINE.search(ln) or PATRON_RAZON_SOLO.search(ln))
    indicios_b = False
    if conteo_razon:
        if re.search(r"\bComprobantes\s+asociados\b", texto, re.IGNORECASE):
            indicios_b = True
        if re.search(r"\bC\.U\.I\.T\.\b", texto):
            indicios_b = True
        if re.search(r"\bC[oó]digo\s*00\d\b", texto, re.IGNORECASE):
            indicios_b = True
        if re.search(r"\bFactura\s+[AB]\b", texto, re.IGNORECASE):
            indicios_b = True
        if re.search(r"\bN[º°:]\s*\d{3,4}-\d{6,8}\b", texto):
            indicios_b = True

    if indicios_b or conteo_razon >= 2:
        return "PROV_B"

    return "UNKNOWN"

# ================== Extractores ==================

def extraer_afip_mono(lineas: List[str]) -> Optional[str]:
    for idx, ln in enumerate(lineas):
        m = PATRON_ETIQUETA_INLINE.search(ln)
        if m:
            trozo = cortar_en_siguientes_etiquetas(m.group(1).strip())
            if SUFIJOS_SOCIALES.search(trozo) and es_nombre_viable(trozo):
                return trozo
            k, j = 0, idx + 1
            while k < 2 and j < len(lineas):
                nxt = lineas[j].strip()
                j += 1
                if not nxt:
                    continue
                if PATRONES_CORTE.search(nxt) or parece_direccion(nxt):
                    break
                nxt = cortar_en_siguientes_etiquetas(nxt)
                if nxt:
                    trozo = (trozo + " " + nxt).strip()
                    k += 1
            if es_nombre_viable(trozo):
                return trozo

    for i, ln in enumerate(lineas):
        if PATRON_ETIQUETA_SOLO.search(ln):
            for j in range(i + 1, min(i + 1 + VENTANA_BUSQUEDA_SIGUIENTES, len(lineas))):
                cand = lineas[j].strip()
                if not cand or PATRONES_CORTE.search(cand) or parece_direccion(cand):
                    continue
                cand = cortar_en_siguientes_etiquetas(cand)
                if es_nombre_viable(cand):
                    return cand
    return None

def extraer_razon_social(lineas: List[str]) -> Optional[str]:
    """Devuelve solo lo que sigue a 'Razón Social:'."""
    for idx, ln in enumerate(lineas):
        m = PATRON_RAZON_INLINE.search(ln)
        if m:
            return cortar_en_siguientes_etiquetas(m.group(1).strip())
    for i, ln in enumerate(lineas):
        if PATRON_RAZON_SOLO.search(ln):
            for j in range(i + 1, min(i + 1 + VENTANA_BUSQUEDA_SIGUIENTES, len(lineas))):
                cand = lineas[j].strip()
                if not cand or PATRONES_CORTE.search(cand) or parece_direccion(cand):
                    continue
                return cortar_en_siguientes_etiquetas(cand)
    return None

# ================== Detección del cliente ==================

def detectar_nombre_cliente(texto: str, debug: bool = False, layout_forzado: str = "AUTO") -> Optional[str]:
    lineas = [ln.strip() for ln in texto.split("\n") if ln is not None]
    layout = determinar_layout(lineas) if layout_forzado == "AUTO" else layout_forzado

    if debug:
        print(f"---- DEBUG layout: {layout} ----")
        for i, ln in enumerate(lineas[:100]):
            print(f"{i:02d}: {ln}")

    if layout == "AFIP_MONO":
        nombre = extraer_afip_mono(lineas) or extraer_razon_social(lineas)
        if nombre and es_nombre_viable(nombre):
            return nombre
    elif layout == "PROV_B":
        nombre = extraer_razon_social(lineas)
        if nombre:
            return nombre.strip()
        return None
    else:
        nombre = extraer_afip_mono(lineas) or extraer_razon_social(lineas)
        if nombre and es_nombre_viable(nombre):
            return nombre

    return None

# =================== Utilidades de nombres/archivos ===================

def sanear_para_nombre_archivo(nombre: str, largo_max: int = 80) -> str:
    nombre = unidecode(nombre)
    nombre = re.sub(r"[^\w\s\-\.\&]", "", nombre)
    nombre = re.sub(r"\s+", " ", nombre).strip()
    return nombre[:largo_max].strip(" ._-")

def detectar_fecha_emision(texto: str) -> Optional[str]:
    patrones = [
        re.compile(r"Fecha\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE),
        re.compile(r"Fecha\s*de\s*Emisi[oó]n\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE),
        re.compile(r"\b(\d{2}/\d{2}/\d{4})\b"),
        re.compile(r"\b(\d{2}/\d{2}/\d{2})\b"),
    ]
    for patron in patrones:
        m = patron.search(texto)
        if m:
            fecha = m.group(1)
            partes = fecha.split("/")
            if len(partes[-1]) == 2:
                partes[-1] = "20" + partes[-1]
            return "".join(reversed(partes))
    return None

def renombrar_pdf(ruta_pdf: str, simulacion: bool = True, debug: bool = False) -> str:
    texto = extraer_texto(ruta_pdf)
    cliente = detectar_nombre_cliente(texto, debug=debug, layout_forzado=FORZAR_LAYOUT)
    fecha = detectar_fecha_emision(texto)
    if not cliente:
        raise ValueError("No se encontró el nombre del cliente en el PDF.")
    if not fecha:
        fecha = "SINFECHA"

    cliente_saneado = sanear_para_nombre_archivo(cliente, largo_max=LIMITE_NOMBRE_CLIENTE).replace(" ", "_")
    base_original = os.path.splitext(os.path.basename(ruta_pdf))[0]

    if FORMATO_NOMBRE_ARCHIVO == "YYYYMMDD_NOMBRE_CLIENTE":
        nuevo_nombre = f"{fecha}_{cliente_saneado}_{base_original}.pdf"
    elif FORMATO_NOMBRE_ARCHIVO == "NOMBRE_CLIENTE_YYYYMMDD":
        nuevo_nombre = f"{cliente_saneado}_{fecha}_{base_original}.pdf"
    else:
        nuevo_nombre = f"{fecha}_{cliente_saneado}_{base_original}.pdf"

    destino_final = os.path.join(RUTA_SALIDA, nuevo_nombre)
    i = 2
    base_sin_ext, ext = os.path.splitext(destino_final)
    while os.path.exists(destino_final):
        destino_final = f"{base_sin_ext}({i}){ext}"
        i += 1

    if simulacion:
        return f"[DRY-RUN] {os.path.basename(ruta_pdf)} -> {os.path.basename(destino_final)}"

    shutil.copy2(ruta_pdf, destino_final)
    return f"✅ Copiado: {os.path.basename(ruta_pdf)} -> {os.path.basename(destino_final)}"

# ============================= CLI =============================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Renombra facturas PDF agregando el nombre del cliente (AFIP/Proveedor).")

    parser.add_argument("--apply", "--aplicar", dest="apply", action="store_true")
    parser.add_argument("--path", "--ruta", dest="path", type=str, default=RUTA_POR_DEFECTO)
    parser.add_argument("--debug", "--depurar", dest="debug", action="store_true")
    parser.add_argument("--layout", "--formato", dest="layout",
                        choices=["AUTO", "AFIP_MONO", "PROV_B"], default=FORZAR_LAYOUT)

    args = parser.parse_args()
    ruta = os.path.abspath(args.path)

    if not os.path.isdir(ruta):
        print(f"❌ Ruta inválida o inexistente: {ruta}")
        sys.exit(2)

    # ⬇️ REEMPLAZÁ ESTA PARTE POR ESTE BLOQUE ⬇️
    archivos: List[str] = []
    with os.scandir(ruta) as it:
        for entry in it:
            if entry.is_file() and entry.name.lower().endswith(".pdf"):
                archivos.append(entry.path)
    # ⬆️ ACÁ ESTÁ LA SOLUCIÓN AL PROBLEMA DE DUPLICADOS ⬆️

    if not archivos:
        print(f"No se encontraron archivos PDF en {ruta}")
        sys.exit(0)

    print(f"📂 Procesando {len(archivos)} archivos desde: {ruta}")
    print(f"📦 Archivos renombrados se guardarán en: {RUTA_SALIDA}")

    for ruta_pdf in archivos:
        try:
            mensaje = renombrar_pdf(ruta_pdf, simulacion=not args.apply, debug=args.debug)
            print(mensaje)
        except Exception as e:
            print(f"❌ {os.path.basename(ruta_pdf)}: {e}")


if __name__ == "__main__":
    main()
