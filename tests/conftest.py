import json
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from consumidores import auditoria  # noqa: E402
from lib import tabla as t  # noqa: E402
from relay import stream_a_bus  # noqa: E402

TABLA = "test-t_calificaciones"
TABLA_AUDITORIA = "test-t_calificaciones_auditoria"
BUS = "lms"
TENANT = "utec"
ADMIN = "16248d98-4e7a-41b9-a08b-4929177d2e2d"
DOCENTE = "c62d9930-b473-4b1d-adec-72fdda7cf599"
OTRO_DOCENTE = "0b1e3f52-7c2d-4c8e-9a51-2f6d3e8b9c10"
ALUMNO = "a18577e2-e005-4a49-a29e-755dbf08f5b6"
CURSO = "crs_01JCQRZHQT9F4WY2JYPDV546K7"
NOTA = "grd_01JCSZ5F9BQKD8YNB0PX0GG63X"
EJEMPLOS = Path(__file__).parent / "ejemplos"
CONSUMIDOS = Path(__file__).parent / "consumidos"


def token(rol="TEACHER", tenant=TENANT, user=DOCENTE):
    return {"Authorization": f"Bearer mock.{tenant}.{rol}.{user}"}


def peticion(metodo, headers, body=None, path_params=None, query=None, resource=None):
    return {
        "httpMethod": metodo,
        "resource": resource,
        "headers": headers,
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path_params,
        "queryStringParameters": query,
        "requestContext": {"requestId": "req-test"},
    }


def cambio(*, event_id, version, score, previous=None, cause="GRADED", reason=None, grade_id=NOTA,
           tenant=TENANT, actor=DOCENTE, cuando="2026-11-14T15:00:00Z"):
    """Body de SQS con un grade.changed, a partir del ejemplo oficial del catálogo."""
    evento = json.loads((EJEMPLOS / "lms.grading.grade.changed.v1.json").read_text(encoding="utf-8"))
    meta = evento["detail"]["metadata"]
    meta.update(eventId=event_id, tenantId=tenant, aggregateId=grade_id, aggregateVersion=version,
                courseId=CURSO, actorId=actor, occurredAt=cuando)
    data = evento["detail"]["data"]
    data.update(gradeId=grade_id, score=score, previousScore=previous, cause=cause)
    data.pop("reason", None)
    if reason is not None:
        data["reason"] = reason
    return json.dumps(evento)


def matricula(user, role, *, event_id, version=1, detail_type="lms.courses.enrollment.created.v1",
              enrollment="enr_01JC5F0KPBVF7HK1VV8H23JGMW"):
    evento = json.loads((CONSUMIDOS / f"{detail_type}.json").read_text(encoding="utf-8"))
    evento["detail"]["metadata"].update(eventId=event_id, aggregateVersion=version, courseId=CURSO,
                                        aggregateId=enrollment)
    evento["detail"]["data"].update(userId=user, enrollmentId=enrollment)
    if detail_type.endswith("created.v1"):
        evento["detail"]["data"]["role"] = role
    return json.dumps(evento)


def lote(*cuerpos):
    return {"Records": [{"messageId": f"m{i}", "body": c} for i, c in enumerate(cuerpos)]}


def _crear_tabla(ddb, nombre, stream):
    extra = {"StreamSpecification": {"StreamEnabled": True, "StreamViewType": "NEW_IMAGE"}} if stream else {}
    ddb.create_table(
        TableName=nombre,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "tenant_id", "AttributeType": "S"},
                              {"AttributeName": "sk", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "tenant_id", "KeyType": "HASH"},
                   {"AttributeName": "sk", "KeyType": "RANGE"}],
        **extra,
    )


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("TABLA", TABLA)
    monkeypatch.setenv("TABLA_AUDITORIA", TABLA_AUDITORIA)
    monkeypatch.setenv("EVENT_BUS", BUS)
    monkeypatch.setenv("EVENT_SOURCE", "lms.grading")
    monkeypatch.setenv("AUTH_MODE", "mock")
    with mock_aws():
        t.reiniciar()
        auditoria.reiniciar()
        stream_a_bus.reiniciar()
        ddb = boto3.client("dynamodb")
        _crear_tabla(ddb, TABLA, stream=True)
        _crear_tabla(ddb, TABLA_AUDITORIA, stream=False)
        boto3.client("events").create_event_bus(Name=BUS)
        yield {"ddb": ddb}
