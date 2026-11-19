"""
EVA-02, criterio "Listo cuando": la nota publicada llega en orden a sus consumidores.

outbox -> Stream -> relay/stream_a_sns.py -> SNS FIFO (MessageGroupId tenantId:courseId)
-> cola FIFO con raw delivery, como la suscribe ms-notificaciones.
"""

import json

import boto3
import pytest

from api import comun, notas
from consumidores import auditoria, columnas, matriculas
from consumidores import notas as calificadas
from lib import esquemas
from relay import stream_a_bus, stream_a_sns
from tests.conftest import (ALUMNO, BUS, CURSO, DOCENTE, TENANT, entrega_calificada, lote, matricula,
                            peticion, registros_stream, tarea_publicada, token)

PUBLICADA = "lms.grading.grade.posted.v1"
CURSO_2 = "crs_01JCQRZHQT9F4WY2JYPDV546K8"


@pytest.fixture
def cola_fifo(aws):
    """Cola FIFO suscrita al tópico con raw delivery, igual que nubeduca-<stage>-notificaciones-notas.fifo."""
    sqs = boto3.client("sqs")
    url = sqs.create_queue(QueueName="notificaciones-notas.fifo", Attributes={"FifoQueue": "true"})["QueueUrl"]
    arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    boto3.client("sns").subscribe(TopicArn=aws["topic"], Protocol="sqs", Endpoint=arn,
                                  Attributes={"RawMessageDelivery": "true"})
    return url


def _recibir(url):
    """Lee la cola FIFO como un consumidor: recibe, procesa y borra, hasta vaciarla."""
    sqs, salida = boto3.client("sqs"), []
    while True:
        mensajes = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10,
                                       AttributeNames=["MessageGroupId"]).get("Messages", [])
        if not mensajes:
            return salida
        for m in mensajes:
            salida.append((m["Attributes"]["MessageGroupId"], json.loads(m["Body"])))
            sqs.delete_message(QueueUrl=url, ReceiptHandle=m["ReceiptHandle"])


def _curso_con_nota(curso=CURSO, n=0):
    matriculas.lambda_handler(lote(matricula(DOCENTE, "TEACHER", event_id=f"01JCN6M3S82KD5RV1ZK8T8QE{n}1",
                                             enrollment=f"enr_01JC5F0KPBVF7HK1VV8H23JG{n}1")), None)
    if curso != CURSO:  # matricula() siempre usa CURSO
        item = comun.matricula(TENANT, CURSO, DOCENTE) | {"sk": comun.sk_matricula(curso, DOCENTE), "courseId": curso}
        comun.t.tabla().put_item(Item=item)
    asignacion = f"asg_01JCF4SQ7XAMY8HTMRFHAWZ7{n}F"
    columnas.lambda_handler(lote(tarea_publicada(event_id=f"01JCD154R0KBWWZTF398XQKD{n}A", assignment=asignacion,
                                                 curso=curso)), None)
    calificadas.lambda_handler(lote(entrega_calificada(event_id=f"01JCSZXEYXVCDQTKPBJDDQVW{n}A", score=11,
                                                       assignment=asignacion, curso=curso,
                                                       submission=f"sbm_01JCY7SYF5X0ZHRJ7KQSVX4B{n}0")), None)
    grade_item_id = comun.id_columna(asignacion)
    return grade_item_id, comun.leer(TENANT, comun.sk_nota(curso, grade_item_id, ALUMNO))["gradeId"]


def _publicar(grade_item_id):
    r = notas.lambda_handler(peticion("POST", token(), path_params={"gradeItemId": grade_item_id},
                                      resource="/grade-items/{gradeItemId}/post"), None)
    assert r["statusCode"] == 200, r["body"]


def _ajustar(grade_id, score):
    r = notas.lambda_handler(peticion("PATCH", token(), body={"score": score, "reason": f"Ajuste a {score}"},
                                      path_params={"gradeId": grade_id}, resource="/grades/{gradeId}"), None)
    assert r["statusCode"] == 200, r["body"]


def test_la_nota_publicada_llega_en_orden_por_sns_fifo(cola_fifo):
    grade_item_id, grade_id = _curso_con_nota()
    _publicar(grade_item_id)
    for score in (12, 13, 14, 15):  # cada ajuste de una nota publicada emite un grade.posted nuevo
        _ajustar(grade_id, score)

    assert stream_a_sns.publicar(registros_stream()) == 5  # solo los grade.posted, no los grade.changed
    recibidos = _recibir(cola_fifo)
    assert [d["data"]["score"] for _, d in recibidos] == [11, 12, 13, 14, 15]
    assert [d["metadata"]["aggregateVersion"] for _, d in recibidos] == [2, 3, 4, 5, 6]
    assert {grupo for grupo, _ in recibidos} == {f"{TENANT}:{CURSO}"}
    for _, detail in recibidos:  # raw delivery: el body es el detail del evento, válido según su contrato
        assert esquemas.errores(PUBLICADA, detail) == []
        assert detail["data"]["gradeId"] == grade_id


def test_un_reintento_del_stream_no_duplica_ni_desordena(cola_fifo):
    grade_item_id, grade_id = _curso_con_nota()
    _publicar(grade_item_id)
    _ajustar(grade_id, 18)
    registros = registros_stream()
    stream_a_sns.publicar(registros)
    stream_a_sns.publicar(registros)  # el Stream reintenta el lote completo
    assert [d["data"]["score"] for _, d in _recibir(cola_fifo)] == [11, 18]


def test_cada_curso_es_un_grupo_fifo(cola_fifo):
    _publicar(_curso_con_nota(CURSO, 0)[0])
    _publicar(_curso_con_nota(CURSO_2, 1)[0])
    stream_a_sns.publicar(registros_stream())
    assert sorted(grupo for grupo, _ in _recibir(cola_fifo)) == [f"{TENANT}:{CURSO}", f"{TENANT}:{CURSO_2}"]


def test_sin_topic_falla_para_que_el_stream_reintente(aws, monkeypatch):
    _publicar(_curso_con_nota()[0])
    monkeypatch.setenv("TOPIC_NOTAS", "")
    with pytest.raises(RuntimeError):
        stream_a_sns.lambda_handler({"Records": registros_stream()}, None)


def test_grade_changed_llega_a_la_auditoria_por_el_bus(aws):
    """Para DAT-01 no hay que llamar a nada: la regla de auditoría toma el grade.changed del bus."""
    sqs, events = boto3.client("sqs"), boto3.client("events")
    url = sqs.create_queue(QueueName="calificaciones-auditoria")["QueueUrl"]
    arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    events.put_rule(Name="calificaciones-auditoria", EventBusName=BUS,  # mismo patrón que serverless.yml
                    EventPattern=json.dumps({"source": ["lms.grading"],
                                             "detail-type": ["lms.grading.grade.changed.v1"]}))
    events.put_targets(Rule="calificaciones-auditoria", EventBusName=BUS, Targets=[{"Id": "1", "Arn": arn}])

    grade_item_id, grade_id = _curso_con_nota()
    _publicar(grade_item_id)
    _ajustar(grade_id, 17.5)
    stream_a_bus.publicar(registros_stream())
    mensajes = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10)["Messages"]
    assert len(mensajes) == 2  # GRADED y MANUAL_OVERRIDE; grade.posted no entra a esta regla
    auditoria.lambda_handler({"Records": [{"messageId": m["MessageId"], "body": m["Body"]} for m in mensajes]},
                             None)
    registros = auditoria.tabla().query(
        KeyConditionExpression="tenant_id = :t AND begins_with(sk, :p)",
        ExpressionAttributeValues={":t": TENANT, ":p": auditoria.sk_registro(grade_id)})["Items"]
    assert [(r["cause"], r["score"]) for r in registros] == [("GRADED", 11), ("MANUAL_OVERRIDE", 17.5)]
