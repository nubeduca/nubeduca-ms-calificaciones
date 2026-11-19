"""
Relay de notas publicadas: DynamoDB Stream → tópico SNS FIFO nubeduca-<stage>-notas.fifo.

grade.posted sale al bus como cualquier evento (stream_a_bus.py o el Pipe) y, además, por este
relay al tópico FIFO, para que "la nota publicada llegue en orden a sus consumidores" (EVA-02).
ms-notificaciones lo lee solo de su cola FIFO, no de una regla del bus, para no avisar dos veces.

Por qué llega en orden:
- El outbox escribe el EVENTO# en la misma transacción que la nota, y todos los EVENTO# de un
  tenant van en la misma partición de la tabla: el Stream los entrega en el orden en que se
  escribieron.
- Este relay los publica uno por uno, en ese orden, con MessageGroupId = <tenantId>:<courseId>.
  SNS FIFO y la cola FIFO del consumidor conservan el orden dentro de cada grupo.
- Si una publicación falla, lanza error y el Stream reintenta el lote completo, sin saltarse
  nada (sin bisect). MessageDeduplicationId = eventId: lo que ya se publicó en el intento
  anterior SNS lo descarta, y el inbox del consumidor también.

Mensaje: el `detail` del evento ({metadata, data}), igual que con raw delivery desde el bus.

Siempre está encendido, también con outbox=pipe: el Pipe solo llega al bus. Con este relay, el
Stream tiene dos lectores (relayOutbox o el Pipe, y este), el máximo que admite DynamoDB.
"""

import json

import boto3

from lib import config, logs

PUBLICADA = "lms.grading.grade.posted.v1"

_cliente = None


def _sns():
    global _cliente
    if _cliente is None:
        _cliente = boto3.client("sns")
    return _cliente


def reiniciar() -> None:
    global _cliente
    _cliente = None


def _mensaje(record: dict) -> dict | None:
    if record.get("eventName") != "INSERT":
        return None
    imagen = (record.get("dynamodb") or {}).get("NewImage") or {}
    sk = (imagen.get("sk") or {}).get("S", "")
    if not sk.startswith("EVENTO#") or (imagen.get("detailType") or {}).get("S") != PUBLICADA:
        return None
    detail = imagen["detail"]["S"]
    meta = json.loads(detail)["metadata"]
    return {
        "TopicArn": config.topic_notas(),
        "Message": detail,
        "MessageGroupId": f"{meta['tenantId']}:{meta['courseId']}",
        "MessageDeduplicationId": meta["eventId"],
    }


def publicar(records: list[dict]) -> int:
    """Publica en el tópico FIFO, en orden, los grade.posted de una lista de registros del Stream."""
    mensajes = [m for m in (_mensaje(r) for r in records) if m]
    if mensajes and not config.topic_notas():
        raise RuntimeError("Falta TOPIC_NOTAS; el Stream reintentará el lote")
    for mensaje in mensajes:
        _sns().publish(**mensaje)
        logs.info(mensaje="Nota publicada en SNS FIFO", grupo=mensaje["MessageGroupId"],
                  eventId=mensaje["MessageDeduplicationId"])
    return len(mensajes)


def lambda_handler(event, context):
    return {"publicados": publicar(event.get("Records", []))}
