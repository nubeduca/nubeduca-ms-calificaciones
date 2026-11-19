"""
Lambda de la cola nubeduca-<stage>-calificaciones-notas: lms.assessment.submission.graded.v1.

El docente calificó una entrega en ms-evaluaciones: ms-calificaciones guarda la nota en
borrador (DRAFT) y, en la misma transacción, publica grade.changed (cause GRADED) por el
outbox. El alumno no la ve hasta que el docente publica la columna.

- Una nota por alumno y columna. Si el alumno ya tenía nota en esa columna (otra entrega o una
  recalificación), se actualiza: sube la versión, previousScore es el puntaje anterior y la
  nota vuelve a DRAFT aunque ya estuviera publicada.
- Si el puntaje no cambió, no se escribe nada.
- Si la calificación es más vieja que el último cambio de la nota (por ejemplo, un ajuste
  manual posterior), se descarta: no pisa un cambio más nuevo.
- Lector tolerante e inbox de la plantilla (duplicados y versiones viejas de la entrega). Si
  otro proceso cambió la nota a la vez, el evento se reintenta (inbox.Conflicto).
"""

from api import comun, nota
from lib import ids, inbox, logs, outbox


def guardar_nota(evento: inbox.Evento) -> list:
    datos = evento.data
    if not evento.course_id:
        raise ValueError("submission.graded sin metadata.courseId")
    grade_item_id = comun.id_columna(datos["assignmentId"])
    sk = comun.sk_nota(evento.course_id, grade_item_id, datos["studentId"])
    anterior = comun.leer(evento.tenant_id, sk, consistente=True)
    score, max_score = comun.decimal(datos["score"]), comun.decimal(datos["maxScore"])

    if anterior is not None:
        if evento.occurred_at < anterior["changedAt"]:
            logs.info(mensaje="Calificación más vieja que la nota", eventId=evento.event_id,
                      gradeId=anterior["gradeId"])
            return []
        if anterior["score"] == score and anterior["maxScore"] == max_score:
            return []

    actor = datos.get("graderId") or evento.actor_id or "system"
    item = {
        "tenant_id": evento.tenant_id,
        "sk": sk,
        "entidad": "GRADE",
        "gradeId": anterior["gradeId"] if anterior else ids.nuevo("grd"),
        "gradeItemId": grade_item_id,
        "courseId": evento.course_id,
        "assignmentId": datos["assignmentId"],
        "submissionId": datos.get("submissionId"),
        "studentId": datos["studentId"],
        "score": score,
        "maxScore": max_score,
        "status": nota.BORRADOR,
        "cause": "GRADED",
        "changedBy": actor,
        "changedAt": evento.occurred_at,
        "createdAt": anterior["createdAt"] if anterior else evento.occurred_at,
        "version": int(anterior["version"]) + 1 if anterior else 1,
    }
    escrituras = [nota.escritura(item, anterior)]
    if anterior is None:
        escrituras.append(nota.referencia(item))
    escrituras.append(outbox.escritura(nota.cambio(item, anterior, actor)))
    return escrituras


REACCIONES = {"lms.assessment.submission.graded.v1": guardar_nota}


def lambda_handler(event, context):
    return inbox.manejar_lote(event, REACCIONES)
