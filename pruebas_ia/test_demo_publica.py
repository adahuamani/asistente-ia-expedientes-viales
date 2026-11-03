"""Pruebas de la demostración pública. Ejecutar desde la raíz con unittest."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1.errors import AppTestError
from streamlit.testing.v1 import AppTest
from pruebas_ia import revision_documental as ui

v = ui.verificador


def respuesta_simulada():
    fid = f"FR-{v.SHA256_ESPERADO}-P0002"
    return {
        "requisito_id": v.REQUISITO_ID, "estado_documental_propuesto": "Documento identificado",
        "explicacion": "El texto de la página sintética contiene el encabezado citado.",
        "fragmentos_citados": [fid],
        "citas_textuales": [{"fragmento_id": fid, "texto": "1.0 MEMORIA DESCRIPTIVA"}],
        "informacion_faltante": [], "limitaciones": ["Solo dos páginas. Requiere revisión humana."],
    }


class ContextoTest(unittest.TestCase):
    def test_integridad_y_alcance(self):
        datos = v._leer_contexto(v.RAIZ)
        self.assertEqual(len(datos["fragmentos"]), 3)
        ids = [f["pagina_pdf"] for f in v.verificar_contexto().values()]
        self.assertEqual(ids, [1, 2])
        self.assertEqual(datos["evidencia_origen"]["archivo_fuente"], v.PDF_PUBLICO)

    def test_hash_distinto(self):
        with patch.object(Path, "read_bytes", return_value=b"PDF alterado en memoria"):
            self.assertFalse(v.validar_respuesta(respuesta_simulada())["integridad_verificada"])

    def test_tercera_pagina_alterada(self):
        datos = v._leer_contexto(v.RAIZ)
        datos["fragmentos"][2]["texto_extraido"] += "x"
        with patch.object(v, "_leer_contexto", return_value=datos):
            self.assertFalse(v.validar_respuesta(respuesta_simulada())["integridad_verificada"])

    def test_pagina_no_autorizada(self):
        propuesta = respuesta_simulada()
        fid = f"FR-{v.SHA256_ESPERADO}-P0003"
        propuesta.update(fragmentos_citados=[fid], citas_textuales=[{"fragmento_id":fid, "texto":"Continuación"}])
        self.assertFalse(v.validar_respuesta(propuesta)["valida_tecnicamente"])


class DiagnosticoTest(unittest.TestCase):
    def test_excepciones_por_etapa(self):
        for etapa in ("Creación del cliente", "Envío de solicitud", "Lectura de respuesta", "Verificación local"):
            with self.subTest(etapa=etapa):
                sdk = Mock()
                error = ValueError("detalle privado simulado")
                cliente = sdk.Client.return_value
                generar = cliente.models.generate_content
                respuesta = Mock(candidates=[SimpleNamespace(finish_reason="STOP")], text="{}")
                generar.return_value = respuesta
                if etapa == "Creación del cliente":
                    sdk.Client.side_effect = error
                elif etapa == "Envío de solicitud":
                    error.response = SimpleNamespace(status_code=404)
                    generar.side_effect = error
                elif etapa == "Lectura de respuesta":
                    type(respuesta).text = property(lambda _: self._fallar(error))
                with self.assertLogs(ui.conector._LOG, level="INFO") as captura, patch.object(ui.conector, "_leer_clave", return_value="simulada"), patch.object(
                    ui.conector, "_cargar_sdk", return_value=sdk
                ), patch.object(v, "validar_respuesta", side_effect=error):
                    resultado = ui.conector.consultar_gemini(modelo=ui.MODELO, id_accion="demo",
                        control={}, accion_explicita=True, permitir_red=True)
                mensajes = [r.getMessage() for r in captura.records]
                self.assertIn(f"evento=fallo etapa={etapa} categoria=", mensajes[-1])
                self.assertNotIn("detalle privado", str(mensajes))
                self.assertNotIn("verificacion_finalizada", str(mensajes))
                self.assertEqual(any("sdk_devolvio_respuesta" in m for m in mensajes),
                                 etapa in ("Lectura de respuesta", "Verificación local"))
                self.assertEqual(" http=" in mensajes[-1], etapa == "Envío de solicitud")
                self.assertEqual(resultado["etapa"], etapa)
                self.assertEqual(resultado["http"], 404 if etapa == "Envío de solicitud" else None)
                self.assertNotIn("detalle privado", str(resultado))
                self.assertFalse(resultado["valida_tecnicamente"])

    @staticmethod
    def _fallar(error):
        raise error

    def test_http_400_estructurado(self):
        from google.genai.errors import ClientError

        marcador = "dato sensible simulado"
        info = {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID",
                "metadata": {"detalle": marcador}}
        casos = [
            ({"status": "INVALID_ARGUMENT"}, "Argumento no válido (INVALID_ARGUMENT)."),
            ({"status": "FAILED_PRECONDITION"}, "Condición previa no cumplida (FAILED_PRECONDITION)."),
            ({"error": {"status": "INVALID_ARGUMENT", "details": [info]}}, "Clave API no válida (API_KEY_INVALID)."),
            ({"details": [info]}, "Clave API no válida (API_KEY_INVALID)."),
            ({"message": "API_KEY_INVALID " + marcador}, "Solicitud rechazada."),
            ({"status": marcador, "details": [{"reason": "API_KEY_INVALID"}]}, "Solicitud rechazada."),
            ({"status": ["INVALID_ARGUMENT"], "details": marcador}, "Solicitud rechazada."),
            ({}, "Solicitud rechazada."),
        ]
        for i, (cuerpo, esperado) in enumerate(casos):
            with self.subTest(caso=i):
                error = ClientError(400, {"message": marcador, **cuerpo})
                sdk = Mock()
                sdk.Client.return_value.models.generate_content.side_effect = error
                with patch.object(ui.conector, "_leer_clave", return_value="simulada"), patch.object(
                    ui.conector, "_cargar_sdk", return_value=sdk
                ):
                    resultado = ui.conector.consultar_gemini(modelo=ui.MODELO, id_accion="demo",
                        control={}, accion_explicita=True, permitir_red=True)
                self.assertEqual(resultado["mensaje"], esperado)
                self.assertEqual(resultado["etapa"], "Envío de solicitud")
                self.assertEqual(resultado["http"], 400)
                self.assertNotIn(marcador, str(resultado))

    def test_categorias_http(self):
        for http, categoria in ((400, "solicitud_invalida"), (401, "autenticacion"),
                                (403, "permiso_denegado"), (404, "recurso_no_encontrado"),
                                (429, "cuota"), (500, "error_servicio"), (504, "tiempo_agotado")):
            with self.subTest(http=http):
                error = RuntimeError()
                error.code = str(http)
                codigo = ui.conector._codigo_http(error)
                self.assertEqual(codigo, http)
                self.assertEqual(ui.conector._clasificar_error(error, codigo)[0], categoria)
        error.code = "403 detalle privado"
        self.assertIsNone(ui.conector._codigo_http(error))


class InterfazTest(unittest.TestCase):
    def setUp(self):
        self.destino = Path(self.enterContext(TemporaryDirectory(dir=v.RAIZ / "pruebas_ia")))
        self.enterContext(patch.object(ui, "CARPETA_DECISIONES", self.destino))
        sdk = Mock()
        self.enterContext(patch.object(ui.conector, "_cargar_sdk", return_value=sdk))
        self.llamada = sdk.Client.return_value.models.generate_content
        self.propuesta = respuesta_simulada()
        self.llamada.return_value = SimpleNamespace(text=json.dumps(self.propuesta),
            candidates=[SimpleNamespace(finish_reason="STOP")])
        self.at = AppTest.from_file(str(v.RAIZ / "app.py"), default_timeout=25)
        self.password = uuid4().hex
        self.at.secrets["DEMO_PASSWORD"] = self.password
        self.at.secrets["GEMINI_API_KEY"] = uuid4().hex
        self.at.run()
        self.assertFalse(self.at.exception)
        self.assertFalse(self.at.error)
        self.assertTrue(any("9. Revisión documental" in w.value for w in self.at.subheader))
        self.llamada.assert_not_called()

    def autenticar(self, entrada=None):
        self.at.text_input(key="ia_entrada").input(self.password if entrada is None else entrada)
        next(b for b in self.at.button if b.label == "Habilitar análisis").click().run()
        self.assertFalse(self.at.exception)
        self.llamada.assert_not_called()

    aviso_sin_resultado = ("El intento se registró, pero no hay un resultado disponible en esta sesión. "
                           "La consulta no se repetirá.")

    def test_traza_exito_y_guardado_antes_del_evento(self):
        self.autenticar()
        guardado = []
        log = ui.conector._LOG
        emitir = log.handle

        def observar(registro):
            if registro.getMessage().endswith("evento=resultado_guardado"):
                guardado.append(ui.st.session_state["ia_resultado"]["valida_tecnicamente"])
            emitir(registro)

        with self.assertLogs(log, level="INFO") as captura, patch.object(
            log, "handle", side_effect=observar
        ), patch.object(ui.conector, "consultar_gemini", wraps=ui.conector.consultar_gemini) as consulta:
            self.at.button(key="ia_analizar").click().run()
            self.at.run()
        consulta.assert_called_once()
        self.llamada.assert_called_once()
        self.assertEqual(guardado, [True])
        accion = self.at.session_state["ia_analisis_id"]
        self.assertEqual([r.getMessage() for r in captura.records], [
            f"intento={accion} evento={evento}" for evento in (
                "intento_registrado", "antes_de_generate_content", "sdk_devolvio_respuesta",
                "verificacion_finalizada", "resultado_guardado")])

    def test_parada_antes_de_terminar_verificacion(self):
        self.autenticar()
        with self.assertLogs(ui.conector._LOG, level="INFO") as captura, patch.object(
            v, "validar_respuesta", side_effect=lambda *_: ui.st.stop()
        ):
            self.at.button(key="ia_analizar").click().run()
            self.at.run()
        accion = self.at.session_state["ia_analisis_id"]
        self.assertEqual([r.getMessage() for r in captura.records], [
            f"intento={accion} evento={evento}" for evento in (
                "intento_registrado", "antes_de_generate_content", "sdk_devolvio_respuesta")])
        self.assertIn(self.aviso_sin_resultado, [w.value for w in self.at.warning])
        self.assertTrue(self.at.button(key="ia_analizar").disabled)
        self.llamada.assert_called_once()

    def test_traza_error_http_sin_datos_privados(self):
        self.autenticar()
        error = RuntimeError(self.password + " " + self.at.secrets["GEMINI_API_KEY"])
        error.status_code = 403
        self.llamada.side_effect = error
        with self.assertLogs(ui.conector._LOG, level="INFO") as captura:
            self.at.button(key="ia_analizar").click().run()
            self.at.run()
        accion = self.at.session_state["ia_analisis_id"]
        self.assertEqual([r.getMessage() for r in captura.records], [
            f"intento={accion} evento={valor}" for valor in (
                "intento_registrado", "antes_de_generate_content",
                "fallo etapa=Envío de solicitud categoria=permiso_denegado http=403",
                "resultado_guardado")])
        self.assertTrue(all(r.exc_info is None for r in captura.records))
        self.llamada.assert_called_once()

    def test_sin_resultado_antes_del_intento(self):
        self.autenticar()
        self.assertNotIn(self.aviso_sin_resultado, [w.value for w in self.at.warning])
        self.assertFalse(self.at.button(key="ia_analizar").disabled)
        self.llamada.assert_not_called()

    def test_sin_resultado_con_intento_consumido(self):
        self.autenticar()
        self.at.session_state["ia_consulta_consumida"] = True
        self.at.run()
        self.assertFalse(self.at.exception)
        self.assertIn(self.aviso_sin_resultado, [w.value for w in self.at.warning])
        self.assertTrue(self.at.button(key="ia_analizar").disabled)
        self.llamada.assert_not_called()

    def test_incorrecta(self):
        self.autenticar(uuid4().hex)
        self.assertTrue(any("incorrecta" in x.value for x in self.at.warning))
        self.assertTrue(self.at.button(key="ia_analizar").disabled)
        with self.assertRaises(AppTestError):
            self.at.button(key="ia_analizar").click()
        self.llamada.assert_not_called()

    def test_sin_password(self):
        del self.at.secrets["DEMO_PASSWORD"]
        self.at.run()
        self.assertTrue(any("no está configurada" in x.value for x in self.at.warning))
        self.assertTrue(self.at.button(key="ia_analizar").disabled)
        self.assertTrue(any("PDF sintético" in x.label for x in self.at.get("download_button")))
        with self.assertRaises(AppTestError):
            self.at.button(key="ia_analizar").click()
        self.llamada.assert_not_called()

    def test_error_sin_reintento(self):
        self.autenticar()
        self.at.run()
        self.assertFalse(self.at.button(key="ia_analizar").disabled)
        self.llamada.assert_not_called()
        self.llamada.side_effect = TimeoutError(self.password)
        self.llamada.side_effect.status_code = 504
        self.at.button(key="ia_analizar").click().run()
        self.assertTrue(self.at.error)
        self.assertEqual(self.at.error[0].value,
            "Envío de solicitud · Se agotó el tiempo de espera; no se reintentó. · HTTP 504")
        self.assertNotIn(self.password, self.at.error[0].value)
        self.assertTrue(self.at.button(key="ia_analizar").disabled)
        with self.assertRaises(AppTestError):
            self.at.button(key="ia_analizar").click()
        self.at.run()
        self.llamada.assert_called_once()

    def test_sin_clave_no_usa_entorno(self):
        del self.at.secrets["GEMINI_API_KEY"]
        with patch.dict("os.environ", {"GEMINI_API_KEY": uuid4().hex}):
            self.autenticar()
            self.at.button(key="ia_analizar").click().run()
        self.assertTrue(self.at.error)
        self.llamada.assert_not_called()

    def test_descargas_y_recarga(self):
        self.at.selectbox(key="consulta_fuente_evidencia").select(v.EVIDENCIA_ID)
        self.at.button(key="boton_ver_fuente").click().run()
        etiquetas = [x.label for x in self.at.get("download_button")]
        self.assertIn("Abrir o descargar documento fuente", etiquetas)
        self.assertIn("Descargar PDF sintético de demostración", etiquetas)
        self.assertIn("Descargar reporte preliminar en Word", etiquetas)
        self.at.run()
        self.assertFalse(self.at.error)
        self.llamada.assert_not_called()

    def decision(self, opcion):
        self.autenticar()
        self.at.button(key="ia_analizar").click().run()
        self.assertTrue(any(x.value == "Pendiente de validación humana" for x in self.at.info))
        self.assertIn("1.0 MEMORIA DESCRIPTIVA", [x.value for x in self.at.text])
        enviado = json.loads(self.llamada.call_args.kwargs["contents"])
        self.assertEqual([f["pagina_pdf"] for f in enviado["fragmentos"]], [1, 2])
        self.assertTrue(self.at.button(key="ia_analizar").disabled)
        with self.assertRaises(AppTestError):
            self.at.button(key="ia_analizar").click()
        aid = self.at.session_state["ia_analisis_id"]
        self.at.radio(key=f"ia_decision_{aid}").set_value(opcion).run()
        if opcion != "Aceptar":
            self.at.button(key=f"ia_guardar_{aid}").click().run()
            self.assertFalse(list(self.destino.iterdir()))
            self.at.text_area(key=f"ia_obs_{aid}").input("Observación ficticia.")
        if opcion == "Corregir":
            self.at.selectbox(key=f"ia_estado_{aid}").select("Evidencia insuficiente")
        self.at.button(key=f"ia_guardar_{aid}").click().run()
        self.at.run()
        self.assertFalse(self.at.exception)
        archivos = list(self.destino.glob("*.json"))
        self.assertEqual(len(archivos), 1)
        registro = json.loads(archivos[0].read_text(encoding="utf-8"))
        self.assertEqual(registro["propuesta_ia"], self.propuesta)
        self.assertEqual(registro["decision_humana"], opcion)
        self.assertEqual(registro["evidencia_origen"], v.EVIDENCIA_ID)
        self.assertEqual(registro["documento"]["sha256"], v.SHA256_ESPERADO)
        self.llamada.assert_called_once()

    def test_aceptar(self):
        self.decision("Aceptar")

    def test_corregir(self):
        self.decision("Corregir")

    def test_rechazar(self):
        self.decision("Rechazar")

    def test_cita_inventada_bloqueada(self):
        self.autenticar()
        propuesta = copy.deepcopy(self.propuesta)
        propuesta["citas_textuales"][0]["texto"] = "No es una cita literal"
        self.llamada.return_value.text = json.dumps(propuesta)
        self.at.button(key="ia_analizar").click().run()
        self.assertTrue(any("verificación local" in e.value for e in self.at.error))
        self.assertFalse(self.at.radio)
        self.assertFalse(list(self.destino.iterdir()))
