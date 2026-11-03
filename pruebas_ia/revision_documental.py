"""Sección Streamlit para el único caso autorizado, RQ-DEMO-01 / EV-PUBLICO-01."""
from datetime import datetime, timedelta, timezone
import hmac
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

import streamlit as st

from . import conector_gemini as conector
from . import verificador_respuestas as verificador

MODELO = "gemini-3.1-flash-lite"
CARPETA_DECISIONES = Path(__file__).resolve().parent / "decisiones"


def _guardar_decision(accion, documento, resultado, decision, observacion, estado_revisado):
    """Publicar un JSON completo por análisis sin sobrescribir decisiones previas."""
    registro = {
        "analisis_id": accion, "fecha": datetime.now(timezone(timedelta(hours=-5))).isoformat(),
        "requisito_id": verificador.REQUISITO_ID, "requisito": verificador.REQUISITO_TEXTO,
        "evidencia_origen": "EV-PUBLICO-01", "documento": documento, "modelo": MODELO,
        "propuesta_ia": resultado["propuesta"],
        "referencias_verificadas": resultado["referencias_verificadas"],
        "decision_humana": decision, "observacion": observacion,
        "estado_documental_revisado": estado_revisado,
    }
    CARPETA_DECISIONES.mkdir(exist_ok=True)
    destino = CARPETA_DECISIONES / f"{accion}.json"
    temporal = None
    try:
        with NamedTemporaryFile(dir=CARPETA_DECISIONES, suffix=".tmp", delete=False,
                                mode="w", encoding="utf-8") as archivo:
            temporal = Path(archivo.name)
            json.dump(registro, archivo, ensure_ascii=False, indent=2)
            archivo.write("\n")
        os.link(temporal, destino)
    finally:
        if temporal is not None:
            temporal.unlink(missing_ok=True)
    return registro


def _password_demo():
    try:
        valor = st.secrets.get("DEMO_PASSWORD")
    except Exception:
        return None
    return valor if isinstance(valor, str) and valor.strip() else None


def _autenticar():
    esperada = _password_demo()
    entrada = st.session_state.pop("ia_entrada", "")
    correcta = esperada is not None and isinstance(entrada, str) and hmac.compare_digest(
        entrada.encode("utf-8"), esperada.encode("utf-8"))
    st.session_state["ia_autenticada"] = correcta
    st.session_state["ia_acceso_denegado"] = not correcta


def mostrar_revision_documental():
    st.write(f"{verificador.REQUISITO_ID} · {verificador.REQUISITO_TEXTO}")
    try:
        fragmentos = verificador.verificar_contexto()
        fuente = next(iter(fragmentos.values()))
        documento = {"nombre": fuente["documento_original"], "sha256": fuente["documento_sha256"]}
        st.write(f"EV-PUBLICO-01 · {documento['nombre']}")
        st.download_button("Descargar PDF sintético de demostración", data=verificador.leer_pdf_publico(),
                           file_name=documento["nombre"], mime="application/pdf",
                           key="ia_pdf_original", on_click="ignore")
    except Exception:
        st.error("No se pudo verificar el PDF de EV-PUBLICO-01 y sus fragmentos. Revisa los archivos del caso.")
        return

    sesion = st.session_state
    control = sesion.setdefault("ia_control_acciones", {})
    st.caption("Al pulsar el botón se enviarán a Google el requisito y las páginas 1 y 2 "
               "(P0001 y P0002), con nombre del PDF, identificadores y hash.")
    configurada = _password_demo() is not None
    if not configurada:
        sesion["ia_autenticada"] = False
        st.warning("Análisis bloqueado: la contraseña de demostración no está configurada.")
    elif not sesion.get("ia_autenticada"):
        with st.form("ia_acceso", clear_on_submit=True):
            st.text_input("Contraseña de demostración", type="password", key="ia_entrada")
            st.form_submit_button("Habilitar análisis", on_click=_autenticar)
        if sesion.get("ia_acceso_denegado"):
            st.warning("Contraseña incorrecta.")
    consumida = sesion.get("ia_consulta_consumida", False)
    habilitado = configurada and sesion.get("ia_autenticada", False) and not consumida
    st.caption("Una consulta por sesión, incluso si falla. Sin reintentos.")
    if consumida:
        st.info("La consulta de esta sesión ya fue utilizada.")
    if st.button("Analizar con Gemini", key="ia_analizar", disabled=not habilitado) and habilitado:
        sesion["ia_consulta_consumida"] = True
        accion = uuid4().hex
        sesion["ia_analisis_id"] = accion
        conector.registrar_hito(accion, "intento_registrado")
        sesion.pop("ia_decision_guardada", None)
        try:
            resultado = conector.consultar_gemini(
                modelo=MODELO, id_accion=accion, control=control, accion_explicita=True,
                permitir_red=True,
            )
        except Exception:
            conector.registrar_fallo(accion, "Consulta", "error_no_clasificado")
            resultado = {"valida_tecnicamente": False, "mensaje": "No se pudo completar el análisis."}

        try:
            sesion["ia_resultado"] = resultado
        except Exception:
            conector.registrar_fallo(accion, "Guardado en sesión", "error_de_guardado")
            sesion["ia_resultado"] = {"valida_tecnicamente": False, "mensaje": "No se pudo completar el análisis."}
        conector.registrar_hito(accion, "resultado_guardado")

        st.rerun()

    resultado = sesion.get("ia_resultado")
    if resultado is None:
        if consumida:
            st.warning("El intento se registró, pero no hay un resultado disponible en esta sesión. "
                       "La consulta no se repetirá.")
        return
    if not resultado["valida_tecnicamente"]:
        partes = [resultado["mensaje"]]
        if resultado.get("etapa"):
            partes.insert(0, resultado["etapa"])
        if resultado.get("http") is not None:
            partes.append(f"HTTP {resultado['http']}")
        st.error(" · ".join(partes))
        return

    propuesta = resultado["propuesta"]
    accion = sesion["ia_analisis_id"]
    guardada = sesion.get("ia_decision_guardada")
    st.markdown("**Propuesta de IA**")
    if guardada is None:
        st.info("Pendiente de validación humana")
    st.text(propuesta["estado_documental_propuesto"])
    st.text(propuesta["explicacion"])
    st.markdown("**Citas verificadas en el PDF original**")
    for ref in resultado["referencias_verificadas"]:
        st.caption(f"Página {ref['pagina_pdf']} · {ref['fragmento_id']}")
        st.text(ref["texto"])
    if not resultado["referencias_verificadas"]:
        st.caption("La propuesta no contiene citas.")
    for titulo, campo in (("Información faltante", "informacion_faltante"), ("Limitaciones", "limitaciones")):
        st.markdown(f"**{titulo}**")
        st.text("\n".join(propuesta[campo]) or "Ninguna indicada por el modelo.")

    st.markdown("**Decisión humana sobre la propuesta**")
    if guardada is not None:
        st.success(f"Decisión registrada: {guardada['decision_humana']}")
        if guardada["estado_documental_revisado"]:
            st.text(guardada["estado_documental_revisado"])
        if guardada["observacion"]:
            st.text(guardada["observacion"])
        return
    decision = st.radio("Decisión", ["Aceptar", "Corregir", "Rechazar"],
                        index=None, key=f"ia_decision_{accion}")
    estado = propuesta["estado_documental_propuesto"] if decision == "Aceptar" else None
    if decision == "Corregir":
        estado = st.selectbox("Estado documental corregido", sorted(verificador.ESTADOS),
                              index=None, key=f"ia_estado_{accion}")
    observacion = st.text_area("Observación (obligatoria para corregir o rechazar)",
                               key=f"ia_obs_{accion}").strip()
    if st.button("Registrar decisión", key=f"ia_guardar_{accion}", disabled=decision is None):
        if decision in ("Corregir", "Rechazar") and not observacion:
            st.warning("Escribe una observación antes de registrar esta decisión.")
        elif decision == "Corregir" and estado is None:
            st.warning("Selecciona el estado documental corregido.")
        else:
            try:
                sesion["ia_decision_guardada"] = _guardar_decision(
                    accion, documento, resultado, decision, observacion, estado)
            except OSError:
                st.error("No se pudo registrar la decisión en el almacenamiento local.")
                return
            st.rerun()
