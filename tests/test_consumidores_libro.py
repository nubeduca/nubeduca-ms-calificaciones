"""EVA-02: assignment.published crea la columna y submission.graded guarda la nota en DRAFT con su grade.changed."""

import json

import pytest

from api import comun
from consumidores import columnas, notas
from lib import esquemas, inbox
from tests.conftest import (ALUMNO, COLUMNA, CURSO, DOCENTE, TAREA, TENANT, entrega_calificada, eventos, items,
                            lote, tarea_publicada)

CAMBIO = "lms.grading.grade.changed.v1"


def _nota(alumno=ALUMNO):
    return comun.leer(TENANT, comun.sk_nota(CURSO, COLUMNA, alumno))


def _cumple(evento):
    detail = {"metadata": evento["metadata"], "data": evento["data"]}
    return esquemas.errores(evento["_detailType"], detail) == []


def test_tarea_publicada_crea_la_columna(aws):
    r = columnas.lambda_handler(lote(tarea_publicada(event_id="01JCD154R0KBWWZTF398XQKD7A")), None)
    assert r == {"batchItemFailures": []}
    (columna,) = items(comun.sk_columna(CURSO))
    assert comun.a_columna(columna) == {"gradeItemId": COLUMNA, "courseId": CURSO, "title": "Laboratorio 4: SQS y DLQ",
                                        "maxScore": 20, "assignmentId": TAREA}
    assert columna["dueAt"] == "2026-11-20T23:59:00Z"
    assert comun.leer(TENANT, comun.sk_ref(COLUMNA))["courseId"] == CURSO
    assert items("EVENTO#") == []  # crear la columna no publica nada


def test_columna_duplicada_o_vieja_se_descarta_y_la_nueva_actualiza(aws):
    columnas.lambda_handler(lote(tarea_publicada(event_id="01JCD154R0KBWWZTF398XQKD7A", version=2)), None)
    columnas.lambda_handler(lote(tarea_publicada(event_id="01JCD154R0KBWWZTF398XQKD7A", version=2, title="Otro"),
                                 tarea_publicada(event_id="01JCD154R0KBWWZTF398XQKD7B", version=1, title="Vieja")),
                            None)
    assert items(comun.sk_columna(CURSO))[0]["title"] == "Laboratorio 4: SQS y DLQ"
    columnas.lambda_handler(lote(tarea_publicada(event_id="01JCD154R0KBWWZTF398XQKD7C", version=3,
                                                 title="Laboratorio 4 (v2)", max_score=10)), None)
    (columna,) = items(comun.sk_columna(CURSO))
    assert (columna["title"], columna["maxScore"], columna["version"]) == ("Laboratorio 4 (v2)", 10, 3)


def test_entrega_calificada_guarda_nota_en_borrador_y_publica_grade_changed(aws):
    r = notas.lambda_handler(lote(entrega_calificada(event_id="01JCSZXEYXVCDQTKPBJDDQVWMA", score=17)), None)
    assert r == {"batchItemFailures": []}
    nota = _nota()
    assert (nota["status"], nota["score"], nota["maxScore"], nota["version"]) == ("DRAFT", 17, 20, 1)
    assert comun.GRADE_ID.match(nota["gradeId"])
    assert comun.leer(TENANT, comun.sk_ref(nota["gradeId"]))["studentId"] == ALUMNO

    (evento,) = eventos(CAMBIO)
    assert _cumple(evento)
    assert evento["metadata"] | {"eventId": "x", "occurredAt": "x"} == {
        "eventId": "x", "occurredAt": "x", "tenantId": TENANT, "aggregateType": "grade",
        "aggregateId": nota["gradeId"], "aggregateVersion": 1, "actorId": DOCENTE, "courseId": CURSO}
    assert evento["data"] == {"gradeId": nota["gradeId"], "gradeItemId": COLUMNA, "studentId": ALUMNO,
                              "previousScore": None, "score": 17, "maxScore": 20, "cause": "GRADED"}


def test_la_nota_puede_llegar_antes_que_su_columna(aws):
    notas.lambda_handler(lote(entrega_calificada(event_id="01JCSZXEYXVCDQTKPBJDDQVWMA", score=17)), None)
    columnas.lambda_handler(lote(tarea_publicada(event_id="01JCD154R0KBWWZTF398XQKD7A")), None)
    assert _nota()["gradeItemId"] == items(comun.sk_columna(CURSO))[0]["gradeItemId"] == COLUMNA


def test_recalificar_sube_la_version_y_guarda_el_puntaje_anterior(aws):
    notas.lambda_handler(lote(entrega_calificada(event_id="01JCSZXEYXVCDQTKPBJDDQVWMA", score=12)), None)
    grade_id = _nota()["gradeId"]
    notas.lambda_handler(lote(entrega_calificada(event_id="01JCSZXEYXVCDQTKPBJDDQVWMB", score=15.5, version=3,
                                                 cuando="2026-11-14T10:00:00Z")), None)
    nota = _nota()
    assert (nota["gradeId"], nota["score"], nota["version"]) == (grade_id, 15.5, 2)
    primero, segundo = eventos(CAMBIO)
    assert (segundo["data"]["previousScore"], segundo["data"]["score"]) == (12, 15.5)
    assert segundo["metadata"]["aggregateVersion"] == 2
    assert all(_cumple(e) for e in (primero, segundo))


def test_mismo_puntaje_o_calificacion_vieja_no_cambian_nada(aws):
    notas.lambda_handler(lote(entrega_calificada(event_id="01JCSZXEYXVCDQTKPBJDDQVWMA", score=12,
                                                 cuando="2026-11-14T10:00:00Z")), None)
    notas.lambda_handler(lote(
        # otra entrega del mismo alumno con el mismo puntaje
        entrega_calificada(event_id="01JCSZXEYXVCDQTKPBJDDQVWMB", score=12, submission="sbm_01JCY7SYF5X0ZHRJ7KQSVX4BP1",
                           cuando="2026-11-15T10:00:00Z"),
        # una calificación anterior al último cambio de la nota
        entrega_calificada(event_id="01JCSZXEYXVCDQTKPBJDDQVWMC", score=5, submission="sbm_01JCY7SYF5X0ZHRJ7KQSVX4BP2",
                           cuando="2026-11-13T10:00:00Z"),
    ), None)
    assert (_nota()["score"], _nota()["version"]) == (12, 1)
    assert len(eventos(CAMBIO)) == 1


def test_evento_duplicado_no_duplica_la_nota_ni_el_evento(aws):
    cuerpo = entrega_calificada(event_id="01JCSZXEYXVCDQTKPBJDDQVWMA", score=17)
    notas.lambda_handler(lote(cuerpo), None)
    assert notas.lambda_handler(lote(cuerpo, cuerpo), None) == {"batchItemFailures": []}
    assert len(items(comun.sk_nota(CURSO))) == 1 and len(eventos(CAMBIO)) == 1


def test_si_otro_proceso_cambio_la_nota_el_evento_se_reintenta(aws, monkeypatch):
    """Antes, cualquier transacción cancelada contaba como duplicado y el evento se perdía."""
    notas.lambda_handler(lote(entrega_calificada(event_id="01JCSZXEYXVCDQTKPBJDDQVWMA", score=12)), None)
    real = notas.guardar_nota

    def con_carrera(evento):
        escrituras = real(evento)
        # justo después de leerla, otro proceso cambia la nota
        actual = _nota()
        comun.t.tabla().put_item(Item={**actual, "version": actual["version"] + 1})
        return escrituras

    monkeypatch.setattr(notas, "REACCIONES", {"lms.assessment.submission.graded.v1": con_carrera})
    cuerpo = entrega_calificada(event_id="01JCSZXEYXVCDQTKPBJDDQVWMB", score=18, version=3,
                                cuando="2026-11-14T10:00:00Z")
    assert notas.lambda_handler(lote(cuerpo), None) == {"batchItemFailures": [{"itemIdentifier": "m0"}]}
    assert items("INBOX#01JCSZXEYXVCDQTKPBJDDQVWMB") == []  # no quedó marcado como procesado
    with pytest.raises(inbox.Conflicto):
        inbox._confirmar(inbox.leer_mensaje(cuerpo), con_carrera(inbox.leer_mensaje(cuerpo)))

    monkeypatch.setattr(notas, "REACCIONES", {"lms.assessment.submission.graded.v1": real})
    assert notas.lambda_handler(lote(cuerpo), None) == {"batchItemFailures": []}  # el reintento sí entra
    assert _nota()["score"] == 18


def test_cada_tenant_tiene_su_libro(aws):
    notas.lambda_handler(lote(entrega_calificada(event_id="01JCSZXEYXVCDQTKPBJDDQVWMA", score=17, tenant="upc")),
                         None)
    assert items(comun.sk_curso(CURSO)) == []
    assert len(items(comun.sk_curso(CURSO), "upc")) == 1


def test_evento_sin_curso_va_a_reintento(aws):
    evento = json.loads(entrega_calificada(event_id="01JCSZXEYXVCDQTKPBJDDQVWMA", score=17))
    del evento["detail"]["metadata"]["courseId"]
    r = notas.lambda_handler({"Records": [{"messageId": "x", "body": json.dumps(evento)}]}, None)
    assert r == {"batchItemFailures": [{"itemIdentifier": "x"}]}
