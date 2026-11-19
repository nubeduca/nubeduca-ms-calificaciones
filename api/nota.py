"""
La nota (EVA-02): cómo se guarda y qué eventos publica. La usan consumidores/notas.py
(submission.graded) y api/notas.py (publicar y ajustar a mano).

- Una nota por alumno y columna: COURSE#<courseId>#GRADE#<gradeItemId>#<studentId>, más
  REF#<gradeId> para encontrarla por su id.
- Cada cambio sube `version`, que es la aggregateVersion de sus eventos. La escritura es
  condicional sobre la versión leída: si otro proceso cambió la nota antes, la transacción
  se cancela y no se publica nada.
- grade.changed sale en cada cambio de puntaje (la auditoría de DAT-01 lo registra solo);
  grade.posted, cuando la nota queda visible para el alumno. Los dos van por el outbox, y
  grade.posted además por SNS FIFO (relay/stream_a_sns.py).
"""

from api import comun
from lib import outbox

CAMBIO = "lms.grading.grade.changed.v1"
PUBLICADA = "lms.grading.grade.posted.v1"
BORRADOR, PUBLICADO = "DRAFT", "POSTED"


def escritura(item: dict, anterior: dict | None) -> dict:
    """Put de la nota: nueva si no existía, o solo si nadie la cambió desde que se leyó."""
    if anterior is None:
        return {"Put": {"Item": item, "ConditionExpression": "attribute_not_exists(sk)"}}
    return {"Put": {"Item": item, "ConditionExpression": "version = :v",
                    "ExpressionAttributeValues": {":v": anterior["version"]}}}


def referencia(item: dict) -> dict:
    """REF#<gradeId>: lo usa PATCH /grades/{gradeId}. Se escribe una sola vez, al crear la nota."""
    return {"Put": {"Item": {
        "tenant_id": item["tenant_id"], "sk": comun.sk_ref(item["gradeId"]), "entidad": "REF",
        "tipo": "GRADE", "courseId": item["courseId"], "gradeItemId": item["gradeItemId"],
        "studentId": item["studentId"],
    }, "ConditionExpression": "attribute_not_exists(sk)"}}


def _evento(detail_type: str, item: dict, actor_id: str, data: dict) -> dict:
    return outbox.nuevo_evento(
        detail_type, tenant_id=item["tenant_id"], aggregate_type="grade", aggregate_id=item["gradeId"],
        aggregate_version=int(item["version"]), actor_id=actor_id, course_id=item["courseId"], data=data,
    )


def cambio(item: dict, anterior: dict | None, actor_id: str) -> dict:
    """grade.changed con el puntaje anterior (null en la primera calificación)."""
    data = {
        "gradeId": item["gradeId"],
        "gradeItemId": item["gradeItemId"],
        "studentId": item["studentId"],
        "previousScore": comun.numero(anterior["score"]) if anterior else None,
        "score": comun.numero(item["score"]),
        "maxScore": comun.numero(item["maxScore"]),
        "cause": item["cause"],
    }
    if item.get("reason") is not None:
        data["reason"] = item["reason"]
    return _evento(CAMBIO, item, actor_id, data)


def publicada(item: dict, actor_id: str) -> dict:
    data = {
        "gradeId": item["gradeId"],
        "gradeItemId": item["gradeItemId"],
        "studentId": item["studentId"],
        "score": comun.numero(item["score"]),
        "maxScore": comun.numero(item["maxScore"]),
    }
    if item.get("assignmentId"):
        data["assignmentId"] = item["assignmentId"]
    return _evento(PUBLICADA, item, actor_id, data)
