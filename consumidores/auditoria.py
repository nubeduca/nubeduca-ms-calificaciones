"""
Auditoría de notas (DAT-01). Lambda de la cola nubeduca-<stage>-calificaciones-auditoria.

Cada lms.grading.grade.changed.v1 que publica ms-calificaciones (EVA-02) queda como un registro en la
tabla <stage>-t_calificaciones_auditoria, que solo admite agregar:

- El registro se escribe con PutItem condicional (attribute_not_exists): nunca pisa uno existente.
  Si SQS entrega el mismo evento dos veces, la segunda escritura falla la condición y se descarta.
- La tabla tiene una política de recurso que niega UpdateItem, DeleteItem, BatchWriteItem y las
  sentencias PartiQL de cambio a cualquier principal, el LabRole incluido (ver serverless.yml).
- Este código no tiene ninguna operación de edición ni de borrado.

A diferencia de un consumidor normal (lib/inbox.py), aquí NO se descartan eventos con una
aggregateVersion vieja: la auditoría guarda todos los cambios, lleguen en el orden que lleguen.
El historial se ordena al leerlo (por versión).

Un evento que no cumple su contrato no se pierde: se reintenta y termina en la DLQ, que tiene alarma.
"""

import json
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

from lib import config, esquemas, logs
from lib.inbox import leer_mensaje
from lib.outbox import ahora_iso

CAMBIO = "lms.grading.grade.changed.v1"

_recurso = None


def tabla():
    global _recurso
    if _recurso is None:
        _recurso = boto3.resource("dynamodb")
    return _recurso.Table(config.tabla_auditoria())


def reiniciar() -> None:
    """Solo para pruebas."""
    global _recurso
    _recurso = None


def sk_registro(grade_id: str, version: int = None, event_id: str = "") -> str:
    """GRADE#<gradeId>#<versión con ceros>#<eventId>: ordenar por sk = ordenar por versión."""
    if version is None:
        return f"GRADE#{grade_id}#"
    return f"GRADE#{grade_id}#{version:010d}#{event_id}"


def _detail(cuerpo: str) -> tuple[str, dict]:
    mensaje = json.loads(cuerpo)
    detail = mensaje.get("detail", mensaje)
    if isinstance(detail, str):
        detail = json.loads(detail)
    return mensaje.get("detail-type") or mensaje.get("detailType") or "", detail


def registro(cuerpo: str) -> dict:
    """Ítem de auditoría a partir del body de SQS. Lanza EventoInvalido si no cumple el contrato."""
    detail_type, detail = _detail(cuerpo)
    if detail_type != CAMBIO:
        raise esquemas.EventoInvalido(detail_type, [f"la cola de auditoría solo recibe {CAMBIO}"])
    esquemas.validar(CAMBIO, detail)
    evento = leer_mensaje(cuerpo)
    datos = json.loads(json.dumps(evento.data), parse_float=Decimal)  # DynamoDB no acepta float
    item = {
        "tenant_id": evento.tenant_id,
        "sk": sk_registro(datos["gradeId"], evento.aggregate_version, evento.event_id),
        "entidad": "AUDIT",
        "gradeId": datos["gradeId"],
        "gradeItemId": datos["gradeItemId"],
        "studentId": datos["studentId"],
        "courseId": evento.course_id,
        "previousScore": datos["previousScore"],
        "score": datos["score"],
        "maxScore": datos["maxScore"],
        "cause": datos["cause"],
        "changedBy": evento.actor_id,
        "changedAt": evento.occurred_at,
        "aggregateVersion": evento.aggregate_version,
        "eventId": evento.event_id,
        "recordedAt": ahora_iso(),
        # El detail original, tal como llegó: es la evidencia del cambio.
        "detail": json.dumps(detail, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    }
    if datos.get("reason") is not None:
        item["reason"] = datos["reason"]
    return item


def guardar(item: dict) -> str:
    """PROCESADO o DUPLICADO. Nunca reemplaza un registro."""
    try:
        tabla().put_item(Item=item, ConditionExpression="attribute_not_exists(sk)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            logs.info(mensaje="Cambio de nota ya registrado", eventId=item["eventId"], gradeId=item["gradeId"])
            return "DUPLICADO"
        raise
    logs.info(mensaje="Cambio de nota registrado", eventId=item["eventId"], gradeId=item["gradeId"],
              tenantId=item["tenant_id"], cause=item["cause"])
    return "PROCESADO"


def lambda_handler(event, context):
    fallidos = []
    for record in (event or {}).get("Records", []):
        try:
            guardar(registro(record["body"]))
        except Exception as exc:  # noqa: BLE001 - vuelve a la cola y, tras 5 intentos, a la DLQ
            errores = getattr(exc, "errores", None)
            logs.error(mensaje="No se pudo registrar el cambio de nota", messageId=record.get("messageId"),
                       error=str(exc), errores=errores)
            fallidos.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": fallidos}
