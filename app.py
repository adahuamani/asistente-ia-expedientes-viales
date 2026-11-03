
import streamlit as st
from docx import Document
from openpyxl import load_workbook
import pymupdf
from io import BytesIO
from pathlib import Path
import os
import shutil
import signal
import subprocess
import tempfile


def ids_de_requisitos(filas):
    return {(fila.get("requisito_id") or "").strip() for fila in filas}


def estado_vinculacion(requisito_id, ids_existentes):
    identificador = (requisito_id or "").strip()
    if identificador in ("", "Sin asignar"):
        return "Sin asignar"
    return "Vinculada" if identificador in ids_existentes else "Revisar identificador"


def localizar_libreoffice():
    """Buscar el ejecutable sin iniciar Office ni cambiar su configuración."""
    candidatos = []
    if os.environ.get("LIBREOFFICE_PATH"):
        candidatos.append(os.environ["LIBREOFFICE_PATH"])
    for nombre in ("soffice.com", "soffice.exe", "soffice", "libreoffice"):
        encontrado = shutil.which(nombre)
        if encontrado:
            candidatos.append(encontrado)
    if os.name == "nt":
        for raiz in (
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ):
            if raiz:
                for ejecutable in ("soffice.com", "soffice.exe"):
                    candidatos.append(str(Path(raiz) / "LibreOffice" / "program" / ejecutable))
    return next((str(Path(p).resolve()) for p in candidatos if Path(p).is_file()), None)


def convertir_doc_a_docx(contenido, limite_segundos=60):
    """Convertir una copia aislada; devolver bytes antes de limpiar temporales."""
    ejecutable = localizar_libreoffice()
    if not ejecutable:
        raise RuntimeError(
            "No se encontró LibreOffice. Instala LibreOffice o configura "
            "LIBREOFFICE_PATH con la ruta a soffice. El original se conserva."
        )
    with tempfile.TemporaryDirectory(prefix="mvp_doc_") as temporal:
        carpeta = Path(temporal)
        entrada = carpeta / "entrada.doc"
        salida = carpeta / "salida"
        salida.mkdir()
        perfil = carpeta / "perfil"
        usuario = perfil / "user"
        usuario.mkdir(parents=True)
        # Perfil nuevo, sin ubicaciones confiables y con macros deshabilitadas.
        (usuario / "registrymodifications.xcu").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="DisableMacrosExecution" oor:op="fuse">'
            '<value>true</value></prop>'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse">'
            '<value>3</value></prop></item></oor:items>',
            encoding="utf-8",
        )
        entrada.write_bytes(contenido)
        comando = [
            ejecutable, f"-env:UserInstallation={perfil.as_uri()}",
            "--headless", "--nologo", "--nodefault", "--norestore",
            "--convert-to", "docx:Office Open XML Text",
            "--outdir", str(salida), str(entrada),
        ]
        opciones = (
            {"creationflags": subprocess.CREATE_NO_WINDOW}
            if os.name == "nt" else {"start_new_session": True}
        )
        with subprocess.Popen(
            comando, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **opciones
        ) as proceso:
            try:
                proceso.communicate(timeout=limite_segundos)
            except subprocess.TimeoutExpired:
                # Terminar también hijos de este proceso, nunca otras sesiones.
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proceso.pid), "/T", "/F"],
                        capture_output=True, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    try:
                        os.killpg(proceso.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                proceso.kill()
                proceso.communicate(timeout=10)
                raise RuntimeError(
                    "La conversión DOC superó el tiempo límite. "
                    "El archivo puede estar protegido o dañado."
                ) from None
            convertido = salida / "entrada.docx"
            if proceso.returncode != 0 or not convertido.is_file():
                raise RuntimeError(
                    "LibreOffice no pudo convertir el DOC. Comprueba si está "
                    "dañado, protegido por contraseña o no es un DOC compatible."
                )
            resultado = convertido.read_bytes()
            try:
                Document(BytesIO(resultado))
            except Exception as error:
                raise RuntimeError("La conversión no produjo un DOCX legible.") from error
            return resultado

ruta_documentos_fuente = Path(__file__).resolve().parent / "documentos_fuente"

st.set_page_config(
    page_title="Asistente IA - Expedientes Viales",
    page_icon="📁",
    layout="wide"
)

st.title("Asistente IA para Expedientes Técnicos Viales")
st.caption("MVP - Hito 3 | Etapa 1: Lectura de documentos Word, PDF y Excel")

st.subheader("1. Carga de documentos")
st.write(
    "Selecciona un documento de prueba en formato "
    "Word (.docx, .doc), PDF (.pdf) o Excel (.xlsx, .xls)."
)

archivo = st.file_uploader(
    "Seleccionar documento Word, PDF o Excel",
    type=["docx", "doc", "pdf", "xlsx", "xls"],
    accept_multiple_files=False
)

if archivo is not None:
    st.success(f"Archivo recibido: {archivo.name}")
    st.write(f"Tamaño: {archivo.size:,} bytes")

    try:
        nombre = archivo.name.lower()

        # CONSERVAR COPIA DEL DOCUMENTO FUENTE
        import hashlib

        contenido_original = archivo.getvalue()
        identificador_archivo = hashlib.sha256(
            contenido_original
        ).hexdigest()[:12]

        nombre_original = Path(archivo.name).name
        ruta_copia = ruta_documentos_fuente / (
            f"{identificador_archivo}_{nombre_original}"
        )

        if not ruta_copia.exists():
            ruta_copia.write_bytes(contenido_original)

        st.caption(
            f"Documento fuente conservado: {ruta_copia.name}"
        )

        texto = ""
        hay_texto = False

        if nombre.endswith(".docx"):
            documento = Document(BytesIO(archivo.getvalue()))
            partes = []

            for parrafo in documento.paragraphs:
                if parrafo.text.strip():
                    partes.append(parrafo.text)

            for numero, tabla in enumerate(documento.tables, start=1):
                partes.append(f"[TABLA {numero}]")
                for fila in tabla.rows:
                    celdas = [celda.text for celda in fila.cells]
                    partes.append(" | ".join(celdas))

            texto = "\n".join(partes)
            hay_texto = bool(texto.strip())

        elif nombre.endswith(".doc"):
            documento = Document(BytesIO(convertir_doc_a_docx(contenido_original)))
            partes = []
            for parrafo in documento.paragraphs:
                if parrafo.text.strip():
                    partes.append(parrafo.text)
            for numero, tabla in enumerate(documento.tables, start=1):
                partes.append(f"[TABLA {numero}]")
                for fila in tabla.rows:
                    partes.append(" | ".join(celda.text for celda in fila.cells))
            texto = "\n".join(partes)
            hay_texto = bool(texto.strip())
            st.info(
                "DOC: lectura mediante conversión temporal. El original se "
                "conserva intacto. Comprueba tablas y contenido con el original; "
                "la conversión puede cambiar el formato y la paginación."
            )

        elif nombre.endswith(".pdf"):
            partes = []
            paginas_sin_texto = []
            paginas_con_texto = 0

            with pymupdf.open(
                stream=archivo.getvalue(),
                filetype="pdf"
            ) as pdf:
                st.write(f"Páginas del PDF: {len(pdf)}")

                for numero, pagina in enumerate(pdf, start=1):
                    contenido = pagina.get_text("text")

                    if contenido.strip():
                        paginas_con_texto += 1
                        partes.append(
                            f"--- PÁGINA {numero} ---\n{contenido}"
                        )
                    else:
                        paginas_sin_texto.append(numero)
                        partes.append(
                            f"--- PÁGINA {numero}: sin texto extraíble ---"
                        )

            texto = "\n\n".join(partes)
            hay_texto = paginas_con_texto > 0

            if paginas_sin_texto:
                st.warning(
                    "Páginas sin texto extraíble: "
                    + ", ".join(map(str, paginas_sin_texto))
                    + ". Podrían contener imágenes o texto escaneado."
                )

        elif nombre.endswith(".xlsx"):
            partes = []
            celdas_con_contenido = 0

            libro = load_workbook(
                BytesIO(archivo.getvalue()),
                read_only=True,
                data_only=True
            )

            try:
                st.write(f"Hojas del Excel: {len(libro.sheetnames)}")
                st.write(
                    "Nombres de las hojas: "
                    + ", ".join(libro.sheetnames)
                )

                for hoja in libro.worksheets:
                    partes.append(f"--- HOJA: {hoja.title} ---")

                    for fila in hoja.iter_rows():
                        for celda in fila:
                            if celda.value is not None and str(celda.value).strip():
                                partes.append(
                                    f"Hoja: {hoja.title} | "
                                    f"Celda: {celda.coordinate} | "
                                    f"Valor: {celda.value}"
                                )
                                celdas_con_contenido += 1

            finally:
                libro.close()

            texto = "\n".join(partes)
            hay_texto = celdas_con_contenido > 0

            st.write(
                f"Celdas con contenido detectadas: {celdas_con_contenido}"
            )
            st.info(
                "Excel: se muestran valores almacenados o calculados "
                "previamente. Las fórmulas no se recalculan y no se "
                "evalúa la validez de los datos."
            )

        elif nombre.endswith(".xls"):
            import xlrd

            partes = []
            celdas_con_contenido = 0
            libro = xlrd.open_workbook(
                file_contents=archivo.getvalue(),
                on_demand=True
            )

            try:
                st.write(f"Hojas del Excel: {libro.nsheets}")
                st.write(
                    "Nombres de las hojas: "
                    + ", ".join(libro.sheet_names())
                )

                for hoja in libro.sheets():
                    partes.append(f"--- HOJA: {hoja.name} ---")
                    for fila in range(hoja.nrows):
                        for columna in range(hoja.ncols):
                            celda = hoja.cell(fila, columna)
                            if celda.ctype in (
                                xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK
                            ):
                                continue

                            valor = celda.value
                            if celda.ctype == xlrd.XL_CELL_DATE:
                                valor = xlrd.xldate_as_datetime(
                                    valor, libro.datemode
                                )
                            elif celda.ctype == xlrd.XL_CELL_BOOLEAN:
                                valor = bool(valor)
                            elif celda.ctype == xlrd.XL_CELL_ERROR:
                                valor = xlrd.error_text_from_code.get(
                                    valor, f"Error Excel ({valor})"
                                )

                            if not str(valor).strip():
                                continue
                            partes.append(
                                f"Hoja: {hoja.name} | "
                                f"Celda: {xlrd.cellname(fila, columna)} | "
                                f"Valor: {valor}"
                            )
                            celdas_con_contenido += 1
            finally:
                libro.release_resources()

            texto = "\n".join(partes)
            hay_texto = celdas_con_contenido > 0
            st.write(
                f"Celdas con contenido detectadas: {celdas_con_contenido}"
            )
            st.info(
                "Excel XLS: se muestran valores almacenados o calculados "
                "previamente. Las fórmulas no se recalculan y no se "
                "evalúa la validez de los datos. Los archivos protegidos "
                "por contraseña no están admitidos."
            )

        st.subheader("2. Contenido extraído")

        if hay_texto:
            st.text_area(
                "Texto del documento",
                value=texto,
                height=400
            )
            st.success(
                "Se extrajo contenido. Comprueba los resultados "
                "con el documento original."
            )
        else:
            st.warning(
                "No se encontró contenido extraíble en el documento."
            )

    except Exception as error:
        st.error(f"No se pudo leer el documento: {error}")

else:
    st.info("Todavía no se ha seleccionado un documento.")


# ETAPA 2: MATRIZ BASE DE REQUISITOS
st.divider()
st.subheader("3. Matriz base de requisitos")
st.caption(
    "Registros demostrativos. No constituyen evaluación normativa ni técnica."
)

import csv
from pathlib import Path

ruta_requisitos = Path(__file__).resolve().parent / "requisitos.csv"

if ruta_requisitos.exists():
    try:
        with ruta_requisitos.open(
            "r", encoding="utf-8-sig", newline=""
        ) as archivo_csv:
            lector = csv.DictReader(archivo_csv)
            requisitos = list(lector)

        if requisitos:
            st.dataframe(
                requisitos,
                use_container_width=True,
                hide_index=True
            )
            st.info(
                f"Registros cargados: {len(requisitos)}. "
                "Todavía no se ha realizado ninguna evaluación."
            )
        else:
            st.warning(
                "El archivo requisitos.csv no contiene registros."
            )

    except Exception as error:
        st.error(f"No se pudo leer requisitos.csv: {error}")
else:

    st.warning(
        "No se encontró requisitos.csv en la carpeta del proyecto."
    )


# ETAPA 2: REGISTRO Y VINCULACION DE EVIDENCIAS
st.divider()
st.subheader("4. Registro de evidencias documentales")
st.caption("Registros demostrativos, sin evidencia documental validada.")

ruta_evidencias = Path(__file__).resolve().parent / "evidencias.csv"

if ruta_evidencias.exists():
    try:
        with ruta_evidencias.open(
            "r", encoding="utf-8-sig", newline=""
        ) as archivo_csv:
            lector_evidencias = csv.DictReader(archivo_csv)
            evidencias = list(lector_evidencias)

        if evidencias:
            st.dataframe(
                evidencias,
                use_container_width=True,
                hide_index=True
            )
            st.info(
                f"Registros de evidencia cargados: {len(evidencias)}"
            )

            # CONSULTA INDEPENDIENTE DEL DOCUMENTO FUENTE
            st.markdown("**Consultar documento fuente de una evidencia**")
            ids_consulta = [
                fila.get("evidencia_id", "").strip()
                for fila in evidencias
                if fila.get("evidencia_id", "").strip()
            ]
            if ids_consulta:
                id_consulta = st.selectbox(
                    "Selecciona una evidencia para consultar su fuente",
                    ids_consulta,
                    key="consulta_fuente_evidencia",
                )
                if st.button("Ver fuente", key="boton_ver_fuente"):
                    st.session_state["fuente_solicitada"] = id_consulta

                if st.session_state.get("fuente_solicitada") == id_consulta:
                    registro_consulta = next(
                        (fila for fila in evidencias
                         if fila.get("evidencia_id", "").strip() == id_consulta),
                        None,
                    )
                    nombre_consulta = (
                        registro_consulta.get("archivo_fuente", "").strip()
                        if registro_consulta else ""
                    )
                    if not nombre_consulta or nombre_consulta.casefold() == "sin asignar":
                        st.warning("Esta evidencia no tiene documento fuente asignado.")
                    elif id_consulta == "EV-PUBLICO-01":
                        from pruebas_ia import verificador_respuestas as caso_publico
                        if nombre_consulta == caso_publico.PDF_PUBLICO:
                            st.download_button(
                                "Abrir o descargar documento fuente", data=caso_publico.leer_pdf_publico(),
                                file_name=nombre_consulta, mime="application/pdf", key="descargar_fuente_consulta",
                            )
                        else:
                            st.warning("El nombre registrado no coincide con el PDF de demostración.")
                    elif not ruta_documentos_fuente.is_dir():
                        st.warning("No existe la carpeta documentos_fuente.")
                    else:
                        coincidencias_fuente = sorted(
                            ruta for ruta in ruta_documentos_fuente.iterdir()
                            if ruta.is_file()
                            and ruta.name.endswith("_" + nombre_consulta)
                            and len(ruta.name.split("_", 1)[0]) == 12
                            and all(c in "0123456789abcdef" for c in ruta.name.split("_", 1)[0])
                        )
                        if len(coincidencias_fuente) == 1:
                            ruta_fuente = coincidencias_fuente[0]
                            st.success("Documento fuente localizado.")
                            st.caption(f"Archivo: {nombre_consulta}")
                            st.download_button(
                                "Abrir o descargar documento fuente",
                                data=ruta_fuente.read_bytes(),
                                file_name=nombre_consulta,
                                mime={
                                    ".pdf": "application/pdf",
                                    ".doc": "application/msword",
                                    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                    ".xls": "application/vnd.ms-excel",
                                }.get(ruta_fuente.suffix.lower(), "application/octet-stream"),
                                key="descargar_fuente_consulta",
                            )
                        elif len(coincidencias_fuente) > 1:
                            st.warning(
                                "Se encontraron varias copias con este nombre. "
                                "No se selecciona ninguna automáticamente para evitar confusiones."
                            )
                        else:
                            st.warning(
                                "El documento indicado en la evidencia no está "
                                "disponible en documentos_fuente."
                            )

            if ruta_requisitos.exists():
                with ruta_requisitos.open(
                    "r", encoding="utf-8-sig", newline=""
                ) as archivo_csv:
                    lector_requisitos = csv.DictReader(archivo_csv)
                    ids_requisitos = {
                        fila["requisito_id"].strip()
                        for fila in lector_requisitos
                        if fila.get("requisito_id")
                    }

                vinculaciones = []

                for evidencia in evidencias:
                    identificador = evidencia.get(
                        "requisito_id", ""
                    ).strip()

                    vinculaciones.append({
                        "Evidencia": evidencia.get(
                            "evidencia_id", ""
                        ),
                        "Requisito": identificador,                    
                        "Vinculacion": (
                            "Pendiente de vinculacion"
                            if not identificador or identificador == "Sin asignar"
                            else (
                                "Vinculado a requisito"
                                if identificador in ids_requisitos
                                else "Revisar identificador"
                            )
                         )

                    })

                st.write("**Comprobación de identificadores**")
                st.dataframe(
                    vinculaciones,
                    use_container_width=True,
                    hide_index=True
                )

            else:
                st.warning("No se encontró requisitos.csv.")

        else:
            st.warning("evidencias.csv no contiene registros.")

    except Exception as error:
        st.error(
            f"No se pudo procesar evidencias.csv: {error}"
        )

else:
    st.warning(
        "No se encontró evidencias.csv en la carpeta del proyecto."
    )


# ETAPA 2: MATRIZ INTEGRADA DE TRAZABILIDAD
st.divider()
st.subheader("5. Matriz integrada de trazabilidad")
st.caption(
    "Relacion documental demostrativa. "
    "No constituye evaluacion de cumplimiento tecnico."
)

try:
    if ruta_requisitos.exists() and ruta_evidencias.exists():
        with ruta_requisitos.open(
            "r", encoding="utf-8-sig", newline=""
        ) as archivo_csv:
            requisitos_matriz = list(csv.DictReader(archivo_csv))

        with ruta_evidencias.open(
            "r", encoding="utf-8-sig", newline=""
        ) as archivo_csv:
            evidencias_matriz = list(csv.DictReader(archivo_csv))

        ids_requisitos_matriz = ids_de_requisitos(requisitos_matriz)
        for evidencia in evidencias_matriz:
            evidencia["Estado documental"] = estado_vinculacion(
                evidencia.get("requisito_id"), ids_requisitos_matriz
            )

        matriz_trazabilidad = []

        for requisito in requisitos_matriz:
            id_requisito = requisito.get("requisito_id", "").strip()
            evidencias_asociadas = [
                evidencia
                for evidencia in evidencias_matriz
                if evidencia["Estado documental"] == "Vinculada"
                and evidencia["requisito_id"].strip() == id_requisito
            ]

            if evidencias_asociadas:
                for evidencia in evidencias_asociadas:
                    matriz_trazabilidad.append({
                        "Requisito ID": id_requisito,
                        "Componente": requisito.get("componente", ""),
                        "Requisito": requisito.get("requisito", ""),
                        "Evidencia ID": evidencia.get("evidencia_id", ""),
                        "Archivo fuente": evidencia.get("archivo_fuente", ""),
                        "Ubicacion": evidencia.get("ubicacion", ""),
                        "Texto evidencia": evidencia.get("evidencia_textual", ""),
                        "Estado documental": evidencia["Estado documental"],
                        "Validacion": evidencia.get("validacion_especialista", "")
                    })
            else:
                matriz_trazabilidad.append({
                    "Requisito ID": id_requisito,
                    "Componente": requisito.get("componente", ""),
                    "Requisito": requisito.get("requisito", ""),
                    "Evidencia ID": "",
                    "Archivo fuente": "",
                    "Ubicacion": "",
                    "Texto evidencia": "",
                    "Estado documental": "Sin evidencia vinculada",
                    "Validacion": "No validada"
                })

        st.write("**Requisitos y evidencias asociadas**")
        st.dataframe(
            matriz_trazabilidad,
            use_container_width=True,
            hide_index=True
        )

        evidencias_sin_vinculo = [
            evidencia
            for evidencia in evidencias_matriz
            if evidencia["Estado documental"] != "Vinculada"
        ]

        st.write("**Evidencias pendientes de vinculacion**")

        if evidencias_sin_vinculo:
            st.dataframe(
                evidencias_sin_vinculo,
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("No hay evidencias pendientes de vinculacion.")

        st.info(
            "La vinculacion por identificador no demuestra "
            "cumplimiento normativo. La evaluacion corresponde "
            "al especialista."
        )
    else:
        st.warning(
            "Se requieren requisitos.csv y evidencias.csv "
            "en la carpeta del proyecto."
        )

except Exception as error:
    st.error(f"No se pudo construir la matriz: {error}")


# ETAPA 3: FORMULARIO Y GUARDADO DE EVIDENCIAS
st.divider()
st.subheader("6. Registrar nueva evidencia")
st.caption(
    "Registro manual. El guardado no implica validacion tecnica."
)

with st.form("formulario_evidencia"):
    nuevo_archivo = st.text_input(
        "Nombre del documento",
        placeholder="Ejemplo: memoria_descriptiva.pdf"
    )

    nuevo_tipo = st.selectbox(
        "Tipo de documento",
        ["PDF", "Word", "Excel"]
    )

    nueva_ubicacion = st.text_input(
        "Ubicacion de la evidencia",
        placeholder="Ejemplo: Pagina 20"
    )

    nuevo_texto = st.text_area(
        "Texto de la evidencia",
        placeholder="Copia aqui el fragmento encontrado"
    )

    opciones_requisitos = ["Sin asignar"]

    if ruta_requisitos.exists():
        try:
            with ruta_requisitos.open(
                "r", encoding="utf-8-sig", newline=""
            ) as archivo_csv:
                lector = csv.DictReader(archivo_csv)
                opciones_requisitos.extend(
                    fila["requisito_id"].strip()
                    for fila in lector
                    if fila.get("requisito_id", "").strip()
                )
        except Exception as error:
            st.warning(
                f"No se pudo cargar la lista de requisitos: {error}"
            )

    nuevo_requisito = st.selectbox(
        "Requisito asociado",
        opciones_requisitos
    )

    guardar = st.form_submit_button("Guardar evidencia")

if guardar:
    if (
        not nuevo_archivo.strip()
        or not nueva_ubicacion.strip()
        or not nuevo_texto.strip()
    ):
        st.warning(
            "Completa el documento, la ubicacion "
            "y el texto de la evidencia."
        )
    else:
        try:
            campos = [
                "evidencia_id",
                "requisito_id",
                "archivo_fuente",
                "tipo_documento",
                "ubicacion",
                "evidencia_textual",
                "origen_registro",
                "validacion_especialista"
            ]

            if not ruta_evidencias.exists():
                st.error(
                    "No se encontro evidencias.csv. "
                    "No se guardo ningun registro."
                )
            else:
                with ruta_evidencias.open(
                    "r", encoding="utf-8-sig", newline=""
                ) as archivo_csv:
                    lector = csv.DictReader(archivo_csv)
                    encabezados = lector.fieldnames
                    registros = list(lector)

                if encabezados != campos:
                    st.error(
                        "Las columnas de evidencias.csv no coinciden "
                        "con las esperadas. No se guardo el registro."
                    )
                else:
                    nuevo_registro = {
                        "requisito_id": nuevo_requisito,
                        "archivo_fuente": nuevo_archivo.strip(),
                        "tipo_documento": nuevo_tipo,
                        "ubicacion": nueva_ubicacion.strip(),
                        "evidencia_textual": nuevo_texto.strip(),
                        "origen_registro": "Manual",
                        "validacion_especialista": "No validada"
                    }

                    def normalizar(valor):
                        return " ".join(
                            str(valor).strip().casefold().split()
                        )

                    campos_comparacion = [
                        "requisito_id",
                        "archivo_fuente",
                        "tipo_documento",
                        "ubicacion",
                        "evidencia_textual"
                    ]

                    duplicado = any(
                        all(
                            normalizar(registro.get(campo, ""))
                            == normalizar(nuevo_registro[campo])
                            for campo in campos_comparacion
                        )
                        for registro in registros
                    )

                    if duplicado:
                        st.warning(
                            "Esta evidencia ya esta registrada "
                            "con los mismos datos. No se duplico."
                        )
                    else:
                        import uuid

                        nuevo_registro["evidencia_id"] = (
                            "EV-" + uuid.uuid4().hex[:12].upper()
                        )

                        
                        with ruta_evidencias.open("rb") as archivo_binario:
                            archivo_binario.seek(0, 2)
                            tamano = archivo_binario.tell()

                            if tamano > 0:
                                archivo_binario.seek(-1, 2)
                                ultimo_byte = archivo_binario.read(1)
                            else:
                                ultimo_byte = b""

                        with ruta_evidencias.open(
                            "a", encoding="utf-8", newline=""
                        ) as archivo_csv:
                            if tamano > 0 and ultimo_byte not in (b"\n", b"\r"):
                                archivo_csv.write("\n")

                            escritor = csv.DictWriter(
                                archivo_csv,
                                fieldnames=campos
                            )
                            escritor.writerow(nuevo_registro)


                        st.success(
                            "Evidencia guardada correctamente."
                        )
                        st.write(
                            "Identificador: "
                            + nuevo_registro["evidencia_id"]
                        )
                        st.info(
                            "Estado: No validada. "
                            "La evaluacion tecnica corresponde "
                            "al especialista."
                        )

        except Exception as error:
            st.error(
                f"No se pudo guardar la evidencia: {error}"
            )





# CONTROL DE CONFIRMACION DE VINCULACIONES
def reiniciar_confirmacion_vinculacion():
    st.session_state["revisar_pertinencia"] = False
    st.session_state["confirmar_vinculacion"] = False



# ETAPA 4: VINCULACION DE EVIDENCIAS CON REQUISITOS
st.divider()
st.subheader("7. Vincular evidencia existente con requisito")
st.caption(
    "Vinculacion documental preliminar. "
    "No implica cumplimiento ni validacion tecnica."
)

try:
    with ruta_evidencias.open(
        "r", encoding="utf-8-sig", newline=""
    ) as archivo_csv:
        lector = csv.DictReader(archivo_csv)
        campos_evidencias = lector.fieldnames
        evidencias_disponibles = list(lector)

    with ruta_requisitos.open(
        "r", encoding="utf-8-sig", newline=""
    ) as archivo_csv:
        lector = csv.DictReader(archivo_csv)
        requisitos_disponibles = list(lector)

    campos_esperados = [
        "evidencia_id",
        "requisito_id",
        "archivo_fuente",
        "tipo_documento",
        "ubicacion",
        "evidencia_textual",
        "origen_registro",
        "validacion_especialista"
    ]

    ids_requisitos = [
        fila.get("requisito_id", "").strip()
        for fila in requisitos_disponibles
        if fila.get("requisito_id", "").strip()
    ]


    evidencias_pendientes = [
        fila
        for fila in evidencias_disponibles
        if fila.get("requisito_id", "").strip()
        == "Sin asignar"
    ]


    
    st.markdown("**Evidencias pendientes de vinculacion**")

    if evidencias_pendientes:
          tabla_pendientes = [
              {
                  "ID de evidencia": fila.get(
                      "evidencia_id", ""
                  ),
                  "Documento fuente": fila.get(
                      "archivo_fuente", ""
                  ),
                  "Ubicacion": fila.get(
                      "ubicacion", ""
                  ),
                  "Validacion": fila.get(
                      "validacion_especialista", ""
                  )
              }
              for fila in evidencias_pendientes
          ]

          st.dataframe(
              tabla_pendientes,
              use_container_width=True,
              hide_index=True
          )

          st.caption(
              "Estas evidencias aun no tienen un requisito "
              "asociado. Su estado de validacion es independiente "
              "de la vinculacion documental."
          )
    else:
          st.info(
              "No existen evidencias pendientes de vinculacion."
          )


    if campos_evidencias != campos_esperados:
        st.error(
            "Las columnas de evidencias.csv no coinciden "
            "con las esperadas."
        )

    elif not evidencias_pendientes:
        st.info(
            "No hay evidencias sin requisito asignado."
        )

    elif not ids_requisitos:
        st.warning(
            "No hay requisitos disponibles."
        )

    else:
        with st.container():
            evidencia_seleccionada = st.selectbox(
                "Selecciona la evidencia",
                [
                    fila["evidencia_id"]
                    for fila in evidencias_pendientes
                ],
                on_change=reiniciar_confirmacion_vinculacion
            )

            requisito_seleccionado = st.selectbox(
                "Selecciona el requisito",
                ids_requisitos,
                on_change=reiniciar_confirmacion_vinculacion
            )

            requisito_para_revisar = next(
                (
                    fila
                    for fila in requisitos_disponibles
                    if fila.get("requisito_id", "").strip()
                    == requisito_seleccionado
                ),
                None
            )

            if requisito_para_revisar:
                st.markdown(
                    "**Detalle del requisito seleccionado**"
                )

                st.write(
                    "Componente:",
                    requisito_para_revisar.get(
                        "componente", ""
                    )
                )

                st.write(
                    "Requisito:",
                    requisito_para_revisar.get(
                        "requisito", ""
                    )
                )

                st.write(
                    "Fuente normativa registrada:",
                    requisito_para_revisar.get(
                        "fuente_normativa", ""
                    )
                )

                st.caption(
                    "Informacion referencial del registro. "
                    "No implica verificacion normativa "
                    "ni cumplimiento del requisito."
                )



            evidencia_para_revisar = next(
                (
                    fila
                    for fila in evidencias_pendientes
                    if fila.get("evidencia_id", "").strip()
                    == evidencia_seleccionada
                ),
                None
            )

            if evidencia_para_revisar:

                # LOCALIZAR DOCUMENTO FUENTE DE LA EVIDENCIA
                nombre_fuente = evidencia_para_revisar.get(
                    "archivo_fuente", ""
                ).strip()

                archivos_encontrados = []

                if nombre_fuente and nombre_fuente != "Sin asignar":
                    archivos_encontrados = [
                        ruta
                        for ruta in ruta_documentos_fuente.iterdir()
                        if ruta.is_file()
                        and ruta.name.endswith("_" + nombre_fuente)
                    ]
                st.markdown(
                    "**Detalle de la evidencia seleccionada**"
                )

                st.write(
                    "Documento fuente:",
                    evidencia_para_revisar.get(
                        "archivo_fuente", ""
                    )
                )

                st.write(
                    "Ubicacion:",
                    evidencia_para_revisar.get(
                        "ubicacion", ""
                    )
                )

                st.text_area(
                    "Texto registrado",
                    value=evidencia_para_revisar.get(
                        "evidencia_textual", ""
                    ),
                    height=120,
                    disabled=True
                )

                st.write(
                    "Validacion del especialista:",
                    evidencia_para_revisar.get(
                        "validacion_especialista", ""
                    )
                )


            
            
            revisar_pertinencia = st.checkbox(
                "He comparado el contenido de la evidencia "
                "con el requisito seleccionado.",
                value=False,
                key="revisar_pertinencia"
            )

            confirmar_vinculacion = st.checkbox(
                "Confirmo que deseo registrar esta "
                "vinculacion documental preliminar.",
                value=False,
                key="confirmar_vinculacion"
            )

            guardar_vinculacion = st.button(
                "Guardar vinculacion",
                key="boton_guardar_vinculacion",
                disabled=not (
                    revisar_pertinencia
                    and confirmar_vinculacion
                )
            )



        if guardar_vinculacion and revisar_pertinencia and confirmar_vinculacion:
            if requisito_seleccionado not in ids_requisitos:
                st.error(
                    "El requisito seleccionado no existe."
                )

            else:
                coincidencias = [
                    fila
                    for fila in evidencias_disponibles
                    if fila.get("evidencia_id", "").strip()
                    == evidencia_seleccionada
                ]

                if len(coincidencias) != 1:
                    st.error(
                        "No se pudo identificar una evidencia "
                        "unica. No se guardaron cambios."
                    )

                elif coincidencias[0].get(
                    "requisito_id", ""
                ).strip() != "Sin asignar":
                    st.warning(
                        "La evidencia ya tiene un requisito "
                        "asignado. No se modifico."
                    )

                else:
                    evidencia_elegida = coincidencias[0]

                    evidencia_elegida["requisito_id"] = (
                        requisito_seleccionado
                    )

                    # La vinculacion no valida la evidencia.
                    evidencia_elegida[
                        "validacion_especialista"
                    ] = "No validada"

                    # Preparar archivo temporal antes de reemplazar.
                    import os

                    ruta_temporal = ruta_evidencias.with_name(
                        "evidencias_temporal.csv"
                    )

                    try:
                        with ruta_temporal.open(
                            "w", encoding="utf-8",
                            newline=""
                        ) as archivo_csv:
                            escritor = csv.DictWriter(
                                archivo_csv,
                                fieldnames=campos_esperados
                            )
                            escritor.writeheader()
                            escritor.writerows(
                                evidencias_disponibles
                            )

                        os.replace(
                            ruta_temporal,
                            ruta_evidencias
                        )

                        st.success(
                            "Vinculacion guardada correctamente."
                        )
                        st.write(
                            "Evidencia:",
                            evidencia_seleccionada
                        )
                        st.write(
                            "Requisito asociado:",
                            requisito_seleccionado
                        )
                        st.info(
                            "Estado: No validada. "
                            "La evaluacion tecnica corresponde "
                            "al especialista."
                        )

                    except Exception as error_guardado:
                        st.error(
                            "No se pudo guardar la vinculacion: "
                            f"{error_guardado}"
                        )

except Exception as error:
    st.error(
        f"No se pudo preparar la vinculacion: {error}"
    )



# ETAPA 5: REPORTE PRELIMINAR DE TRAZABILIDAD
st.divider()

st.subheader(
    "8. Reporte preliminar de trazabilidad documental"
)

st.caption(
    "Reporte de apoyo a la revision del especialista. "
    "No constituye evaluacion de cumplimiento normativo "
    "ni conformidad tecnica del expediente."
)

st.info(
    "Esta seccion permitira consolidar los requisitos, "
    "las evidencias vinculadas y las evidencias pendientes "
    "para generar posteriormente un reporte en Word."
)



# ETAPA 5.1: REQUISITOS DEL REPORTE PRELIMINAR
st.markdown("**Requisitos registrados**")

ids_requisitos_reporte = None
try:
    with ruta_requisitos.open(
        "r", encoding="utf-8-sig", newline=""
    ) as archivo_csv:
        lector = csv.DictReader(archivo_csv)
        requisitos_reporte = list(lector)
    ids_requisitos_reporte = ids_de_requisitos(requisitos_reporte)

    if requisitos_reporte:
        tabla_requisitos_reporte = [
            {
                "ID": fila.get("requisito_id", ""),
                "Componente": fila.get("componente", ""),
                "Requisito": fila.get("requisito", ""),
                "Fuente normativa": fila.get(
                    "fuente_normativa", ""
                ),
                "Estado preliminar registrado": fila.get(
                    "estado_preliminar", ""
                )
            }
            for fila in requisitos_reporte
        ]

        st.dataframe(
            tabla_requisitos_reporte,
            use_container_width=True,
            hide_index=True
        )

        st.caption(
            "Los estados se muestran tal como estan "
            "registrados en requisitos.csv. "
            "No se realiza evaluacion automatica."
        )

    else:
        st.info("No hay requisitos registrados.")

except Exception as error:
    st.error(
        f"No se pudieron cargar los requisitos: {error}"
    )



# ETAPA 5.2: EVIDENCIAS DEL REPORTE PRELIMINAR
st.markdown("**Evidencias documentales registradas**")

try:
    with ruta_evidencias.open(
        "r", encoding="utf-8-sig", newline=""
    ) as archivo_csv:
        lector = csv.DictReader(archivo_csv)
        evidencias_reporte = list(lector)

    if ids_requisitos_reporte is None:
        st.warning("No se pudo verificar la vinculacion sin el catalogo de requisitos.")
    elif evidencias_reporte:
        tabla_evidencias_reporte = [
            {
                "ID de evidencia": fila.get(
                    "evidencia_id", ""
                ),
                "Requisito asociado": fila.get(
                    "requisito_id", ""
                ),
                "Documento fuente": fila.get(
                    "archivo_fuente", ""
                ),
                "Ubicacion": fila.get(
                    "ubicacion", ""
                ),
                "Situacion documental": estado_vinculacion(
                    fila.get("requisito_id"), ids_requisitos_reporte
                ),
                "Validacion del especialista": fila.get(
                    "validacion_especialista", ""
                )
            }
            for fila in evidencias_reporte
        ]

        st.dataframe(
            tabla_evidencias_reporte,
            use_container_width=True,
            hide_index=True
        )

        st.caption(
            "La situacion documental indica si existe "
            "una asociacion registrada con un requisito. "
            "No determina pertinencia, suficiencia de "
            "evidencia ni cumplimiento normativo."
        )

    else:
        st.info("No hay evidencias registradas.")

except Exception as error:
    st.error(
        f"No se pudieron cargar las evidencias: {error}"
    )



# ETAPA 5.3: PREPARACION DEL REPORTE WORD
def preparar_reporte_word():
    from io import BytesIO
    from docx import Document

    documento = Document()

    # FORMATO DE ENCABEZADOS DE TABLAS WORD
    def formatear_encabezado(tabla):
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Pt, RGBColor

        for celda in tabla.rows[0].cells:
            propiedades = celda._tc.get_or_add_tcPr()

            sombreado = OxmlElement("w:shd")
            sombreado.set(
                qn("w:fill"), "1F4E78"
            )
            propiedades.append(sombreado)

            for parrafo in celda.paragraphs:
                for texto in parrafo.runs:
                    texto.bold = True
                    texto.font.size = Pt(9)
                    texto.font.color.rgb = RGBColor(
                        255, 255, 255
                    )


    # FORMATO DEL CONTENIDO DE LAS TABLAS
    def formatear_contenido_tabla(tabla):
        # ANCHOS DE COLUMNAS: TABLA DE REQUISITOS
        tabla.autofit = False

        anchos_requisitos = [
            2.5, 4.0, 8.0, 5.0, 6.2
        ]

        for fila in tabla.rows:
            for indice, ancho in enumerate(
                anchos_requisitos
            ):
                fila.cells[indice].width = Cm(ancho)

        from docx.shared import Pt

        for fila in tabla.rows[1:]:
            for celda in fila.cells:
                for parrafo in celda.paragraphs:
                    parrafo.paragraph_format.space_before = Pt(0)
                    parrafo.paragraph_format.space_after = Pt(0)

                    for texto in parrafo.runs:
                        texto.font.size = Pt(9)




    # CONFIGURACION DE PAGINA DEL REPORTE
    from docx.enum.section import WD_ORIENT
    from docx.shared import Cm

    seccion = documento.sections[0]

    seccion.orientation = WD_ORIENT.LANDSCAPE
    seccion.page_width = Cm(29.7)
    seccion.page_height = Cm(21.0)

    seccion.top_margin = Cm(2.0)
    seccion.bottom_margin = Cm(2.0)
    seccion.left_margin = Cm(2.0)
    seccion.right_margin = Cm(2.0)


    documento.add_heading(
        "Reporte preliminar de trazabilidad documental",
        level=0
    )

    documento.add_paragraph(
        "Asistente IA para la Revision y Evaluacion "
        "de Perfiles y Expedientes Tecnicos "
        "de Proyectos Viales"
    )

    documento.add_paragraph(
        "Documento preliminar de apoyo al especialista. "
        "No constituye evaluacion de cumplimiento "
        "normativo ni conformidad tecnica del expediente."
    )


    # REQUISITOS REGISTRADOS EN EL PROYECTO
    documento.add_heading(
        "1. Requisitos registrados",
        level=1
    )

    with ruta_requisitos.open(
        "r", encoding="utf-8-sig", newline=""
    ) as archivo_csv:
        lector = csv.DictReader(archivo_csv)
        requisitos_word = list(lector)

    ids_requisitos_word = ids_de_requisitos(requisitos_word)
    if requisitos_word:
        tabla = documento.add_table(
            rows=1,
            cols=5
        )

        tabla.style = "Table Grid"

        encabezados = [
            "ID",
            "Componente",
            "Requisito",
            "Fuente normativa",
            "Estado preliminar registrado"
        ]

        for indice, encabezado in enumerate(encabezados):
            tabla.rows[0].cells[indice].text = encabezado

        formatear_encabezado(tabla)

        for fila in requisitos_word:
            celdas = tabla.add_row().cells

            celdas[0].text = fila.get("requisito_id", "")
            celdas[1].text = fila.get("componente", "")
            celdas[2].text = fila.get("requisito", "")
            celdas[3].text = fila.get(
                "fuente_normativa", ""
            )
            celdas[4].text = fila.get(
                "estado_preliminar", ""
            )

        formatear_contenido_tabla(tabla)

    else:
        documento.add_paragraph(
            "No hay requisitos registrados."
        )


    # EVIDENCIAS DOCUMENTALES REGISTRADAS
    documento.add_heading(
        "2. Evidencias documentales registradas",
        level=1
    )

    with ruta_evidencias.open(
        "r", encoding="utf-8-sig", newline=""
    ) as archivo_csv:
        lector = csv.DictReader(archivo_csv)
        evidencias_word = list(lector)

    if evidencias_word:
        tabla_evidencias = documento.add_table(
            rows=1,
            cols=6
        )

        tabla_evidencias.style = "Table Grid"

        # ANCHOS DE COLUMNAS: TABLA DE EVIDENCIAS
        tabla_evidencias.autofit = False

        anchos_evidencias = [
            2.7, 3.2, 7.0, 3.0, 4.5, 5.3
        ]

        for fila in tabla_evidencias.rows:
            for indice, ancho in enumerate(
                anchos_evidencias
            ):
                fila.cells[indice].width = Cm(ancho)

        encabezados_evidencias = [
            "ID de evidencia",
            "Requisito asociado",
            "Documento fuente",
            "Ubicacion",
            "Situacion documental",
            "Validacion del especialista"
        ]

        for indice, encabezado in enumerate(
            encabezados_evidencias
        ):
            tabla_evidencias.rows[0].cells[
                indice
            ].text = encabezado

        formatear_encabezado(tabla_evidencias)

        for fila in evidencias_word:
            celdas = tabla_evidencias.add_row().cells

            requisito_asociado = (fila.get("requisito_id") or "").strip()
            situacion = estado_vinculacion(requisito_asociado, ids_requisitos_word)

            celdas[0].text = fila.get(
                "evidencia_id", ""
            )
            celdas[1].text = requisito_asociado
            celdas[2].text = fila.get(
                "archivo_fuente", ""
            )
            celdas[3].text = fila.get(
                "ubicacion", ""
            )
            celdas[4].text = situacion
            celdas[5].text = fila.get(
                "validacion_especialista", ""
            )

        formatear_contenido_tabla(tabla_evidencias)

        # APLICAR ANCHOS A TODAS LAS FILAS DE EVIDENCIAS
        tabla_evidencias.autofit = False

        for fila_tabla in tabla_evidencias.rows:
            for indice, ancho in enumerate(anchos_evidencias):
                fila_tabla.cells[indice].width = Cm(ancho)

    else:
        documento.add_paragraph(
            "No hay evidencias registradas."
        )

    documento.add_paragraph(
        "Nota: La vinculacion documental no acredita "
        "la pertinencia o suficiencia de la evidencia "
        "ni el cumplimiento normativo. "
        "La validacion corresponde al especialista."
    )


    archivo_word = BytesIO()
    documento.save(archivo_word)
    archivo_word.seek(0)

    return archivo_word.getvalue()


st.markdown("**Preparacion del reporte Word**")

st.caption(
    "La funcion de generacion del documento Word "
    "esta incorporada. La descarga se habilitara "
    "despues de completar su contenido."
)



# ETAPA 5.4: DESCARGA DEL REPORTE WORD
try:
    contenido_word = preparar_reporte_word()

    st.download_button(
        label="Descargar reporte preliminar en Word",
        data=contenido_word,
        file_name="reporte_preliminar_trazabilidad.docx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        use_container_width=True
    )

except Exception as error:
    st.error(
        f"No se pudo preparar el reporte Word: {error}"
    )


from pruebas_ia.revision_documental import mostrar_revision_documental

mostrar_revision_documental()
