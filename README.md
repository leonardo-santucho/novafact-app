# 🧾 Novafact App

Script en **Python** que renombra automáticamente facturas PDF agregando el nombre del cliente al final del archivo.  
El nombre se detecta leyendo el texto del PDF (layout AFIP estándar con “Apellido y Nombre / Razón Social”).

---

## ⚙️ Requisitos previos

### 🪟 Windows
- **Python 3.10 o superior**  
  👉 [Descargar desde python.org](https://www.python.org/downloads/windows/)  
  > Durante la instalación marcá la opción **“Add Python to PATH”**.
- **Git** → [Descargar desde git-scm.com](https://git-scm.com/download/win)
- (Opcional) **Visual Studio Code** → [Descargar VS Code](https://code.visualstudio.com/)

---

### 🍏 macOS
Verificá que tengas Python y Git:
```bash
python3 --version
git --version
```
Si no están instalados:
```bash
brew install python git
```
> (Requiere tener [Homebrew](https://brew.sh/))

---

## 🚀 Instalación del proyecto

1. **Clonar el repositorio**
   ```bash
   git clone https://github.com/leonardo-santucho/novafact-app.git
   cd novafact-app
   ```

2. **Crear el entorno virtual**

   **macOS / Linux**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

   **Windows (PowerShell)**
   ```bash
   python -m venv venv
   venv\Scripts\activate
   ```

3. **Instalar dependencias**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configurar el archivo `.env`**
   Crear un archivo llamado `.env` en la raíz del proyecto con esta variable:
   ```bash
   PDF_INPUT_PATH=./facturas_pdf
   ```

---

## 🧪 Uso del script

Colocá tus facturas PDF en la carpeta `facturas_pdf/`.

### 🔍 Modo prueba (sin renombrar)
```bash
python rename_invoice_by_client.py
```

### ✍️ Modo real (renombra los archivos)
```bash
python rename_invoice_by_client.py --apply
```

### 📁 Usar otra carpeta puntual (ignora el .env)
```bash
python rename_invoice_by_client.py --path ./pendientes --apply
```

---

## 🧩 Estructura del proyecto

```
novafact-app/
├─ facturas_pdf/            # Carpeta donde se colocan los PDFs
│  └─ .gitkeep
├─ rename_invoice_by_client.py
├─ requirements.txt
├─ .gitignore
├─ .env                     # Ruta configurada para tus PDFs (no se sube a Git)
└─ README.md
```

---

## 🧰 Dependencias principales

| Librería        | Uso principal |
|-----------------|----------------|
| **pypdf**        | Lectura del texto de los PDF |
| **pdfminer.six** | Parsing de texto alternativo |
| **unidecode**    | Limpieza de nombres (acentos y símbolos) |
| **python-dotenv**| Carga del archivo `.env` |

---

## 🧹 Buenas prácticas

- No subas tus archivos PDF reales ni tu carpeta `venv/` (ya están ignorados en `.gitignore`).
- Siempre activá tu entorno virtual antes de ejecutar el script.
- Si actualizás dependencias, exportalas:
  ```bash
  pip freeze > requirements.txt
  ```

---

## 💡 Ejemplo rápido

```bash
# Activar entorno virtual
source venv/bin/activate

# Ejecutar en modo prueba
python rename_invoice_by_client.py

# Aplicar renombrado real
python rename_invoice_by_client.py --apply
```

Salida esperada:
```
📂 Procesando 1 archivos en: /Users/leonardo/Documents/dev/projects/novafact-app/facturas_pdf
[DRY-RUN] 20282114055_011_00001_00000005.pdf -> 20282114055_011_00001_00000005 - CS TECH CONSULTING SA.pdf
```

---


