"""Verificador local del caso RQ-DEMO-01. No escribe ni llama servicios.

Ejemplo: validar_respuesta(diccionario_o_texto_json).
Una respuesta técnicamente aceptada sigue pendiente de revisión humana.
"""
import csv
import hashlib
import json
from pathlib import Path

import pymupdf

REQUISITO_ID = "RQ-DEMO-01"
REQUISITO_TEXTO = "Identificar la memoria descriptiva del expediente"
EVIDENCIA_ID = "EV-PUBLICO-01"
PDF_PUBLICO = "EXPEDIENTE_VIAL_DEMOSTRACION.pdf"
SHA256_ESPERADO = "751098bb3d5ab094c8709a9d3d1f3024f88eb0c3babf92edaf3a1ff4f1e962a9"
ESTADOS = {"Documento identificado", "Evidencia insuficiente", "No evaluable"}
CAMPOS = {
    "requisito_id", "estado_documental_propuesto", "explicacion",
    "fragmentos_citados", "citas_textuales", "informacion_faltante", "limitaciones",
}
RAIZ = Path(__file__).resolve().parent.parent


class ErrorValidacion(ValueError):
    pass


def _exigir(condicion, mensaje):
    if not condicion:
        raise ErrorValidacion(mensaje)


def _objeto_sin_duplicados(pares):
    resultado = {}
    for clave, valor in pares:
        _exigir(clave not in resultado, f"Clave JSON duplicada: {clave}")
        resultado[clave] = valor
    return resultado


def _json(texto):
    def constante_invalida(valor):
        raise ErrorValidacion(f"Constante JSON no permitida: {valor}")
    return json.loads(texto, object_pairs_hook=_objeto_sin_duplicados,
                      parse_constant=constante_invalida)


def _leer_contexto(raiz):
    return _json((raiz / "pruebas_ia/RQ-DEMO-01_EV-PUBLICO-01_fragmentos.json")
                 .read_text(encoding="utf-8"))


def leer_pdf_publico(raiz=RAIZ):
    carpeta = (Path(raiz) / "documentos_fuente").resolve()
    ruta = (carpeta / PDF_PUBLICO).resolve()
    _exigir(ruta.parent == carpeta, "Ruta del PDF sintético incorrecta.")
    contenido = ruta.read_bytes()
    _exigir(hashlib.sha256(contenido).hexdigest() == SHA256_ESPERADO,
            "El PDF no coincide con el documento sintético autorizado.")
    return contenido


def verificar_contexto(raiz=RAIZ):
    """Relee PDF, CSV y JSON; verifica las 3 páginas y autoriza solo 1 y 2."""
    raiz = Path(raiz).resolve()
    datos = _leer_contexto(raiz)
    with (raiz / "requisitos.csv").open(encoding="utf-8-sig", newline="") as f:
        requisitos = [r for r in csv.DictReader(f) if r["requisito_id"] == REQUISITO_ID]
    _exigir(len(requisitos) == 1, "Requisito ausente o duplicado en CSV.")
    _exigir(requisitos[0]["requisito"] == REQUISITO_TEXTO, "Cambió el requisito autorizado.")
    _exigir(datos["requisito"] == requisitos[0], "Requisito del JSON distinto del CSV.")
    with (raiz / "evidencias.csv").open(encoding="utf-8-sig", newline="") as f:
        evidencias = [r for r in csv.DictReader(f) if r["evidencia_id"] == EVIDENCIA_ID]
    _exigir(len(evidencias) == 1, "EV-PUBLICO-01 ausente o duplicada.")
    _exigir(datos["evidencia_origen"] == evidencias[0], "EV-PUBLICO-01 del JSON distinta del CSV.")
    _exigir(evidencias[0]["requisito_id"] == REQUISITO_ID, "Vínculo de EV-PUBLICO-01 incorrecto.")
    meta = datos["documento"]
    nombre = evidencias[0]["archivo_fuente"]
    _exigir(Path(nombre).name == nombre and "/" not in nombre and "\\" not in nombre,
            "Nombre de documento inválido.")
    carpeta = (raiz / "documentos_fuente").resolve()
    _exigir(nombre == PDF_PUBLICO, "Nombre ajeno al caso público.")
    ruta = (carpeta / PDF_PUBLICO).resolve()
    _exigir(ruta.parent == carpeta, "Documento fuera de documentos_fuente.")
    relativa = Path(meta["ruta_conservada_relativa"].replace("\\", "/"))
    _exigir(not relativa.is_absolute() and (raiz / relativa).resolve() == ruta,
            "Ruta documental distinta de la autorizada.")
    contenido = leer_pdf_publico(raiz)
    digest = hashlib.sha256(contenido).hexdigest()
    _exigir(digest == SHA256_ESPERADO == meta["sha256"], "SHA-256 del original no coincide.")
    _exigir(meta["nombre_original"] == nombre and meta["bytes"] == len(contenido),
            "Metadatos del documento inconsistentes.")
    fragmentos = datos["fragmentos"]
    _exigir(type(fragmentos) is list, "Lista de fragmentos inválida.")
    autorizados = {}
    with pymupdf.open(stream=contenido, filetype="pdf") as pdf:
        _exigir(len(pdf) == meta["paginas"] == len(fragmentos) == 3,
                "Cobertura de páginas incorrecta.")
        for pagina, fragmento in enumerate(fragmentos, 1):
            fid = f"FR-{digest}-P{pagina:04d}"
            texto = pdf[pagina - 1].get_text("text", sort=False)
            _exigir(fragmento["fragmento_id"] == fid, "ID de fragmento incorrecto o duplicado.")
            _exigir(type(fragmento["pagina_pdf"]) is int and fragmento["pagina_pdf"] == pagina,
                    "Página de fragmento incorrecta.")
            _exigir(fragmento["documento_original"] == nombre and
                    fragmento["documento_sha256"] == digest, "Fuente de fragmento incorrecta.")
            _exigir(fragmento["texto_extraido"] == texto, "Texto distinto del PDF original.")
            _exigir(type(fragmento["offset_inicio"]) is int and
                    type(fragmento["offset_fin_exclusivo"]) is int and
                    fragmento["offset_inicio"] == 0 and
                    fragmento["offset_fin_exclusivo"] == len(texto), "Offsets incorrectos.")
            if pagina in (1, 2):
                autorizados[fid] = fragmento
    return autorizados


def _texto(valor):
    return type(valor) is str and bool(valor.strip())


def validar_respuesta(respuesta, raiz=RAIZ):
    """Devuelve aceptación técnica, errores y citas resueltas localmente.

    Admite JSON en texto o un dict. Para detectar claves duplicadas debe
    recibirse el texto original, antes de cualquier json.loads externo.
    """
    resultado = {
        "valida_tecnicamente": False,
        "estado_tecnico": "Respuesta rechazada por verificación local",
        "integridad_verificada": False,
        "revision_humana": "Pendiente",
        "evidencia_validada": False,
        "errores": [],
        "referencias_verificadas": [],
    }
    try:
        fragmentos = verificar_contexto(raiz)
        resultado["integridad_verificada"] = True
    except Exception as error:
        resultado["errores"].append(f"Integridad/contexto: {error}")
        return resultado
    try:
        obj = _json(respuesta) if type(respuesta) is str else respuesta
        _exigir(type(obj) is dict and set(obj) == CAMPOS, "Campos obligatorios o estructura incorrectos.")
        _exigir(obj["requisito_id"] == REQUISITO_ID, "Requisito no autorizado.")
        _exigir(type(obj["estado_documental_propuesto"]) is str and
                obj["estado_documental_propuesto"] in ESTADOS, "Estado no permitido.")
        _exigir(_texto(obj["explicacion"]), "Explicación vacía o inválida.")
        for campo in ("informacion_faltante", "limitaciones", "fragmentos_citados"):
            _exigir(type(obj[campo]) is list and all(_texto(x) for x in obj[campo]),
                    f"Lista inválida: {campo}")
        declarados = obj["fragmentos_citados"]
        _exigir(len(set(declarados)) == len(declarados), "Fragmentos declarados duplicados.")
        _exigir(set(declarados) <= set(fragmentos), "Fragmento no autorizado.")
        citas = obj["citas_textuales"]
        _exigir(type(citas) is list, "Citas deben ser una lista.")
        referencias = []
        pares = set()
        citados = set()
        for cita in citas:
            _exigir(type(cita) is dict and set(cita) == {"fragmento_id", "texto"},
                    "Estructura de cita incorrecta.")
            fid, literal = cita["fragmento_id"], cita["texto"]
            _exigir(_texto(fid) and fid in fragmentos, "ID citado no autorizado.")
            _exigir(_texto(literal), "Cita vacía o de tipo incorrecto.")
            fragmento = fragmentos[fid]
            _exigir(literal in fragmento["texto_extraido"], "Cita no literal o inventada.")
            _exigir((fid, literal) not in pares, "Cita duplicada.")
            pares.add((fid, literal))
            citados.add(fid)
            referencias.append({
                "fragmento_id": fid, "texto": literal,
                "pagina_pdf": fragmento["pagina_pdf"],
                "documento_original": fragmento["documento_original"],
                "documento_sha256": fragmento["documento_sha256"],
            })
        _exigir(set(declarados) == citados, "Fragmentos declarados y citados no coinciden.")
        _exigir(obj["estado_documental_propuesto"] != "Documento identificado" or bool(citas),
                "Documento identificado exige al menos una cita.")
        resultado.update(valida_tecnicamente=True,
                         estado_tecnico="Aceptada técnicamente; interpretación pendiente de revisión humana",
                         referencias_verificadas=referencias)
    except Exception as error:
        resultado["errores"].append(f"Respuesta: {error}")
    return resultado
