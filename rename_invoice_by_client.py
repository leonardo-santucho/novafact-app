#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Importaciones: una por línea (PEP 8)
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

# Si no hay variable, por defecto ./facturas_pdf
RUTA_POR_DEFECTO = os.getenv("PDF_INPUT_PATH") or str(RUTA_PROYECTO / "facturas_pdf")

RUTA_SALIDA = os.getenv("PDF_OUTPUT_PATH") or str(RUTA_PROYECTO / "facturas_renombradas")
os.makedirs(RUTA_SALIDA, exist_ok=True)  # crea la carpeta si no existe

LIMITE_NOMBRE_CLIENTE = int(os.getenv("PDF_CLIENT_NAME_MAXLEN", "80"))
FORMATO_NOMBRE_ARCHIVO = os.getenv("PDF_FILENAME_FORMAT", "YYYYMMDD_NOMBRE_CLIENTE").upper()



# =================== Extracción de texto ===================

def extraer_texto_pypdf(ruta_pdf: str) -> Optional[str]:
    """Extrae texto con pypdf; devuelve None si falla."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(ruta_pdf)
        return "\n".join([(p.extract_text() or "") for p in reader.pages])
    except Exception:
        return None


def extraer_texto_pdfminer(ruta_pdf: str) -> Optional[str]:
    """Extrae texto con pdfminer.six; devuelve None si falla."""
    try:
        from pdfminer.high_level import extract_text
        return extract_text(ruta_pdf)
    except Exception:
        return None


def extraer_texto(ruta_pdf: str) -> str:
    """Intenta con pypdf y cae a pdfminer; normaliza saltos de línea."""
    texto = extraer_texto_pypdf(ruta_pdf) or extraer_texto_pdfminer(ruta_pdf)
    if not texto:
        raise RuntimeError(f"No se pudo extraer texto de: {ruta_pdf}")
    texto = texto.replace("\r", "\n")
    texto = re.sub(r"\n{2,}", "\n", texto)
    return texto

# ============== Patrones / Heurísticas de layout ==============

PATRON_ETIQUETA_INLINE = re.compile(
    r"Apellido\s*y\s*Nombre\s*/\s*Raz[oó]n\s*Social\s*:?\s*(.+)$", re.IGNORECASE
)
PATRON_ETIQUETA_SOLO = re.compile(
    r"^Apellido\s*y\s*Nombre\s*/\s*Raz[oó]n\s*Social\s*:?\s*$", re.IGNORECASE
)

PATRON_CUIT = re.compile(r"\bCUIT\b\s*:?", re.IGNORECASE)

PATRONES_CORTE = re.compile(
    r"\b(Domicilio|Condici[oó]n|Punto\s*de\s*Venta|Comprobante|Per[ií]odo|Fecha|CAE|Ingresos\s*Brutos)\b",
    re.IGNORECASE,
)

SUFIJOS_SOCIALES = re.compile(
    r"\b(S\.?A\.?|S\.?R\.?L\.?|SAS|SAU|SAIC|SAICyF|SAICF|U\.?T\.?E\.?)\b", re.IGNORECASE
)

# Soporte explícito para “Razón Social”
PATRON_RAZON_INLINE = re.compile(r"Raz[oó]n\s*Social\s*:?\s*(.+)$", re.IGNORECASE)
PATRON_RAZON_SOLO = re.compile(r"^Raz[oó]n\s*Social\s*:?\s*$", re.IGNORECASE)

# Indicadores del layout “B proveedor”
INDICIOS_LAYOUT_B = re.compile(
    r"\b(?:Comprobantes\s+asociados|C\.U\.I\.T\.|C[óo]digo\s*00\d|^B$|Factura\s+B\b)",
    re.IGNORECASE
)

# Heurística para detectar posibles direcciones
INDICIOS_DIRECCION = re.compile(
    r"\d|,| - |\b(Domicilio|Calle|Av\.?|Avenida|Piso|Depto|Capital|Buenos\s*Aires|Provincia|CP|Código\s*Postal)\b",
    re.IGNORECASE,
)

def parece_direccion(s: str) -> bool:
    return bool(INDICIOS_DIRECCION.search(s))

# =================== Utilidades de parsing ===================

def es_nombre_viable(s: str) -> bool:
    """Al menos dos tokens alfabéticos (permite tildes y ñ)."""
    tokens = re.findall(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü]{2,}", s)
    return len(tokens) >= 2


def cortar_en_siguientes_etiquetas(s: str) -> str:
    """Corta si aparecen otras etiquetas relevantes a continuación."""
    s = s.split("  ")[0].strip()
    if PATRONES_CORTE.search(s):
        s = PATRONES_CORTE.split(s, maxsplit=1)[0].strip()
    return s


def buscar_despues_de_etiqueta_flexible(lineas: List[str], idx_inicio: int) -> Optional[str]:
    """
    Desde el rótulo, escanea hasta VENTANA_BUSQUEDA_SIGUIENTES líneas.
    Ignora otros rótulos intermedios y concatena hasta 2 líneas si continúan el nombre.
    """
    for j in range(idx_inicio + 1, min(idx_inicio + 1 + VENTANA_BUSQUEDA_SIGUIENTES, len(lineas))):
        cand = lineas[j].strip()
        if not cand:
            continue
        if PATRONES_CORTE.search(cand):
            # ignorar rótulos intermedios y seguir buscando
            continue

        trozo = cortar_en_siguientes_etiquetas(cand)
        if not es_nombre_viable(trozo):
            continue

        # concatenar hasta 2 líneas más si continúan el nombre
        k, t = 0, j + 1
        while k < 2 and t < len(lineas):
            nxt = lineas[t].strip()
            if not nxt:
                t += 1
                continue
            if PATRONES_CORTE.search(nxt):
                break
            nxt2 = cortar_en_siguientes_etiquetas(nxt)
            if not nxt2:
                break
            trozo = (trozo + " " + nxt2).strip()
            k += 1
            t += 1

        if es_nombre_viable(trozo):
            return trozo

    return None

# ================== Detectores por tipo de layout ==================

def determinar_layout(lineas: List[str]) -> str:
    """
    Heurística:
      - Si aparece 'Apellido y Nombre / Razón Social' => AFIP_MONO
      - Si hay 'Razón Social:' y además indicios de B/CUIT/Código => PROV_B
      - Si hay muchas 'Razón Social:' => PROV_B
      - En otro caso => UNKNOWN
    """
    texto = "\n".join(lineas)
    tiene_afip = any(PATRON_ETIQUETA_SOLO.search(ln) or PATRON_ETIQUETA_INLINE.search(ln) for ln in lineas)
    conteo_razon = sum(1 for ln in lineas if PATRON_RAZON_INLINE.search(ln) or PATRON_RAZON_SOLO.search(ln))
    hay_indicios_b = bool(INDICIOS_LAYOUT_B.search(texto))

    if tiene_afip:
        return "AFIP_MONO"
    if conteo_razon and hay_indicios_b:
        return "PROV_B"
    if conteo_razon >= 2:
        return "PROV_B"
    return "UNKNOWN"


def extraer_afip_mono(lineas: List[str]) -> Optional[str]:
    """Extractor para layout AFIP con 'Apellido y Nombre / Razón Social'."""
    # 1) Misma línea + posible continuación (evitando direcciones)
    for idx, ln in enumerate(lineas):
        m = PATRON_ETIQUETA_INLINE.search(ln)
        if not m:
            continue

        trozo = cortar_en_siguientes_etiquetas(m.group(1).strip())

        # Si ya parece empresa (sufijo social), devolvemos
        if SUFIJOS_SOCIALES.search(trozo) and es_nombre_viable(trozo):
            return trozo

        # Si no, concatenar hasta 2 líneas si NO parecen dirección
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

    # 2) Rótulo solo y nombre en líneas siguientes (tolerante, evitando direcciones)
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
    """Extractor para layout 'B proveedor' con 'Razón Social:' inline o en la/s siguiente/s."""
    # 1) Inline + posible continuación
    for idx, ln in enumerate(lineas):
        m = PATRON_RAZON_INLINE.search(ln)
        if m:
            trozo = cortar_en_siguientes_etiquetas(m.group(1).strip())
            k, j = 0, idx + 1
            while k < 2 and j < len(lineas):
                nxt = lineas[j].strip()
                j += 1
                if not nxt:
                    continue
                if PATRONES_CORTE.search(nxt):
                    break
                trozo = (trozo + " " + cortar_en_siguientes_etiquetas(nxt)).strip()
                k += 1
            if es_nombre_viable(trozo):
                return trozo

    # 2) Rótulo solo y nombre más abajo (buscador flexible)
    for i, ln in enumerate(lineas):
        if PATRON_RAZON_SOLO.search(ln):
            cand = buscar_despues_de_etiqueta_flexible(lineas, i)
            if cand:
                return cand

    return None

# ================== Detección del nombre de cliente ==================

def detectar_nombre_cliente(texto: str, debug: bool = False) -> Optional[str]:
    lineas = [ln.strip() for ln in texto.split("\n") if ln is not None]
    layout = determinar_layout(lineas)

    if debug:
        print(f"---- DEBUG layout: {layout} ----")
        for i, ln in enumerate(lineas[:100]):
            print(f"{i:02d}: {ln}")

    nombre = None
    if layout == "AFIP_MONO":
        nombre = extraer_afip_mono(lineas) or extraer_razon_social(lineas)
    elif layout == "PROV_B":
        nombre = extraer_razon_social(lineas) or extraer_afip_mono(lineas)
    else:
        nombre = extraer_afip_mono(lineas) or extraer_razon_social(lineas)

    if nombre and es_nombre_viable(nombre):
        return nombre

    # Recurso final: líneas con sufijo societario
    candidatos = []
    for ln in lineas:
        if SUFIJOS_SOCIALES.search(ln):
            s = cortar_en_siguientes_etiquetas(ln)
            if es_nombre_viable(s):
                candidatos.append(s)
    if candidatos:
        candidatos.sort(key=len, reverse=True)
        return candidatos[0]

    return None

# =================== Utilidades de nombres/archivos ===================

def sanear_para_nombre_archivo(nombre: str, largo_max: int = 80) -> str:
    """Quita tildes, caracteres inválidos y recorta."""
    nombre = unidecode(nombre)
    nombre = re.sub(r"[^\w\s\-\.\&]", "", nombre)
    nombre = re.sub(r"\s+", " ", nombre).strip()
    return nombre[:largo_max].strip(" ._-")


def construir_nuevo_nombre(ruta_pdf: str, cliente: str) -> str:
    base, ext = os.path.splitext(os.path.basename(ruta_pdf))
    seguro = sanear_para_nombre_archivo(cliente)
    if not seguro:
        raise ValueError("No se pudo sanear el nombre del cliente.")
    nuevo_base = base if seguro.lower() in base.lower() else f"{base} - {seguro}"
    return os.path.join(os.path.dirname(ruta_pdf), nuevo_base + ext)

def detectar_fecha_emision(texto: str) -> Optional[str]:
    """
    Busca la primera fecha del documento que parezca la de emisión o la principal.
    Formatos admitidos:
      - dd/mm/yyyy
      - dd/mm/yy
    Devuelve en formato YYYYMMDD.
    """
    patrones = [
        re.compile(r"Fecha\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE),
        re.compile(r"Fecha\s*de\s*Emisi[oó]n\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE),
        re.compile(r"\b(\d{2}/\d{2}/\d{4})\b"),  # fallback general (año completo)
        re.compile(r"\b(\d{2}/\d{2}/\d{2})\b"),  # fallback con año corto
    ]

    for patron in patrones:
        m = patron.search(texto)
        if m:
            fecha = m.group(1)
            # Normalizar formato
            partes = fecha.split("/")
            if len(partes[-1]) == 2:
                partes[-1] = "20" + partes[-1]  # convierte 25 → 2025
            return "".join(reversed(partes))  # dd/mm/yyyy → yyyymmdd

    return None

def renombrar_pdf(ruta_pdf: str, simulacion: bool = True, debug: bool = False) -> str:
    """
    Renombra el PDF con el formato:
    [YYYYMMDD]_[NOMBRE_CLIENTE]_[NOMBREORIGINAL].pdf
    y lo copia a la carpeta definida en PDF_OUTPUT_PATH.
    """
    texto = extraer_texto(ruta_pdf)
    cliente = detectar_nombre_cliente(texto, debug=debug)
    fecha = detectar_fecha_emision(texto)

    if not cliente:
        raise ValueError("No se encontró el nombre del cliente en el PDF.")
    if not fecha:
        fecha = "SINFECHA"

    # Sanea y convierte espacios en guiones bajos
    cliente_saneado = (
    sanear_para_nombre_archivo(cliente, largo_max=LIMITE_NOMBRE_CLIENTE)
    .replace(" ", "_")
    )
    base_original = os.path.splitext(os.path.basename(ruta_pdf))[0]

    # Construcción dinámica del formato
    if FORMATO_NOMBRE_ARCHIVO == "YYYYMMDD_NOMBRE_CLIENTE":
        nuevo_nombre = f"{fecha}_{cliente_saneado}_{base_original}.pdf"
    elif FORMATO_NOMBRE_ARCHIVO == "NOMBRE_CLIENTE_YYYYMMDD":
        nuevo_nombre = f"{cliente_saneado}_{fecha}_{base_original}.pdf"
    else:
        # fallback seguro
        nuevo_nombre = f"{fecha}_{cliente_saneado}_{base_original}.pdf"


    destino_final = os.path.join(RUTA_SALIDA, nuevo_nombre)

    # Evitar colisiones
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

    parser = argparse.ArgumentParser(
        description="Renombra facturas PDF agregando el nombre del cliente (AFIP/Proveedor)."
    )

    # Alias inglés + español
    parser.add_argument(
        "--apply", "--aplicar",
        dest="apply",
        action="store_true",
        help="Apply changes (default is dry-run). / Aplica cambios (por defecto solo muestra)."
    )
    parser.add_argument(
        "--path", "--ruta",
        dest="path",
        type=str,
        default=RUTA_POR_DEFECTO,
        help="Path to invoices folder (default from PDF_INPUT_PATH in .env). / "
             "Ruta de facturas (por defecto la de PDF_INPUT_PATH del .env)."
    )
    parser.add_argument(
        "--debug", "--depurar",
        dest="debug",
        action="store_true",
        help="Show extracted text lines for diagnostics. / Muestra líneas leídas para diagnóstico."
    )

    args = parser.parse_args()

    ruta = os.path.abspath(args.path)
    if not os.path.isdir(ruta):
        print(f"❌ Ruta inválida o inexistente: {ruta}")
        sys.exit(2)

    # Soportar .pdf y .PDF (mayúsculas/minúsculas)
    archivos: List[str] = []
    for patron in ("*.pdf", "*.PDF"):
        archivos.extend(glob.glob(os.path.join(ruta, patron)))

    if not archivos:
        print(f"No se encontraron archivos PDF en {ruta}")
        sys.exit(0)


    print(f"📂 Procesando {len(archivos)} archivos desde: {ruta}")
    print(f"📦 Archivos renombrados se guardarán en: {RUTA_SALIDA}")

    for ruta_pdf in archivos:
        try:
            mensaje = renombrar_pdf(
                ruta_pdf,
                simulacion=not args.apply,
                debug=args.debug
            )
            print(mensaje)
        except Exception as e:
            print(f"❌ {os.path.basename(ruta_pdf)}: {e}")


if __name__ == "__main__":
    main()

