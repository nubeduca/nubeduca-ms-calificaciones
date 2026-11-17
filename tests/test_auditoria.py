"""DAT-01, criterio "Listo cuando": cada grade.changed queda registrado y no se puede editar."""

import inspect
import json

import pytest
from botocore.exceptions import ClientError

from consumidores import auditoria
from tests.conftest import CURSO, DOCENTE, NOTA, TENANT, cambio, lote


def _registros(tenant=TENANT):
    return auditoria.tabla().query(
        KeyConditionExpression="tenant_id = :t AND begins_with(sk, :p)",
        ExpressionAttributeValues={":t": tenant, ":p": f"GRADE#{NOTA}#"},
    )["Items"]


def test_cada_grade_changed_queda_registrado(aws):
    r = auditoria.lambda_handler(lote(
        cambio(event_id="01JCSZ5F9BQKD8YNB0PX0GG63A", version=1, score=15),
        cambio(event_id="01JCSZ5F9BQKD8YNB0PX0GG63B", version=2, score=17, previous=15,
               cause="MANUAL_OVERRIDE", reason="Revisión de la pregunta 3"),
    ), None)
    assert r == {"batchItemFailures": []}
    primero, segundo = _registros()
    assert primero["sk"] == f"GRADE#{NOTA}#0000000001#01JCSZ5F9BQKD8YNB0PX0GG63A"
    assert (primero["previousScore"], primero["score"], primero["cause"]) == (None, 15, "GRADED")
    assert "reason" not in primero
    assert (segundo["previousScore"], segundo["score"], segundo["cause"]) == (15, 17, "MANUAL_OVERRIDE")
    assert segundo["reason"] == "Revisión de la pregunta 3"
    assert segundo["changedBy"] == DOCENTE and segundo["courseId"] == CURSO
    # El detail original se guarda tal cual, como evidencia.
    assert json.loads(segundo["detail"])["data"]["reason"] == "Revisión de la pregunta 3"


def test_sqs_repetido_no_duplica_ni_pisa(aws):
    cuerpo = cambio(event_id="01JCSZ5F9BQKD8YNB0PX0GG63A", version=1, score=15)
    auditoria.lambda_handler(lote(cuerpo), None)
    original = _registros()[0]
    assert auditoria.lambda_handler(lote(cuerpo, cuerpo), None) == {"batchItemFailures": []}
    (unico,) = _registros()
    assert unico["recordedAt"] == original["recordedAt"]  # el registro original no se tocó


def test_un_registro_no_se_puede_reemplazar(aws):
    auditoria.lambda_handler(lote(cambio(event_id="01JCSZ5F9BQKD8YNB0PX0GG63A", version=1, score=15)), None)
    item = auditoria.registro(cambio(event_id="01JCSZ5F9BQKD8YNB0PX0GG63A", version=1, score=20))
    assert auditoria.guardar(item) == "DUPLICADO"
    assert _registros()[0]["score"] == 15


def test_el_codigo_no_tiene_operaciones_de_edicion():
    """La única escritura del consumidor es put_item condicional (update/delete los niega la tabla en AWS)."""
    fuente = inspect.getsource(auditoria)
    for prohibida in ("update_item", "delete_item", "batch_writer", "batch_write_item", "execute_statement"):
        assert prohibida not in fuente
    assert "attribute_not_exists(sk)" in fuente


def test_llegan_en_desorden_y_se_guardan_todos(aws):
    """A diferencia del inbox normal, la auditoría no descarta versiones viejas."""
    auditoria.lambda_handler(lote(
        cambio(event_id="01JCSZ5F9BQKD8YNB0PX0GG63C", version=3, score=18, previous=17),
        cambio(event_id="01JCSZ5F9BQKD8YNB0PX0GG63A", version=1, score=15),
        cambio(event_id="01JCSZ5F9BQKD8YNB0PX0GG63B", version=2, score=17, previous=15),
    ), None)
    assert [int(r["aggregateVersion"]) for r in _registros()] == [1, 2, 3]


@pytest.mark.parametrize("romper", [
    lambda e: e["detail"]["data"].pop("score"),
    lambda e: e["detail"]["data"].update(cause="OTRA"),
    lambda e: e["detail"]["metadata"].update(tenantId="UTEC!"),
    lambda e: e.update({"detail-type": "lms.grading.grade.posted.v1"}),
])
def test_evento_invalido_va_a_reintento_y_no_se_guarda(aws, romper):
    evento = json.loads(cambio(event_id="01JCSZ5F9BQKD8YNB0PX0GG63A", version=1, score=15))
    romper(evento)
    r = auditoria.lambda_handler({"Records": [{"messageId": "x", "body": json.dumps(evento)}]}, None)
    assert r == {"batchItemFailures": [{"itemIdentifier": "x"}]}
    assert _registros() == []


def test_un_mensaje_malo_no_frena_el_lote(aws):
    r = auditoria.lambda_handler({"Records": [
        {"messageId": "bueno", "body": cambio(event_id="01JCSZ5F9BQKD8YNB0PX0GG63A", version=1, score=15)},
        {"messageId": "malo", "body": "no es json"},
    ]}, None)
    assert r == {"batchItemFailures": [{"itemIdentifier": "malo"}]}
    assert len(_registros()) == 1


def test_cada_tenant_tiene_su_historial(aws):
    auditoria.lambda_handler(lote(cambio(event_id="01JCSZ5F9BQKD8YNB0PX0GG63A", version=1, score=15,
                                         tenant="upc")), None)
    assert _registros() == []
    assert len(_registros("upc")) == 1


def test_guardar_propaga_otros_errores(aws, monkeypatch):
    class TablaQueFalla:
        def put_item(self, **_):
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem")

    item = auditoria.registro(cambio(event_id="01JCSZ5F9BQKD8YNB0PX0GG63A", version=1, score=15))
    monkeypatch.setattr(auditoria, "tabla", lambda: TablaQueFalla())
    with pytest.raises(ClientError):
        auditoria.guardar(item)
