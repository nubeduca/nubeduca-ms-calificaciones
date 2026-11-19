"""
Lambda de la cola nubeduca-<stage>-calificaciones-columnas: lms.assessment.assignment.published.v1.

Cada tarea publicada es una columna del libro de notas de su curso:
- COURSE#<courseId>#ITEM#<gradeItemId>  {title, maxScore, dueAt, assignmentId}
- REF#<gradeItemId>                      {courseId}, para POST /grade-items/{gradeItemId}/post

gradeItemId lleva el mismo ULID que la tarea (asg_X -> gri_X), así una nota que llega antes
que su columna ya sabe a qué columna pertenece.

Lector tolerante e inbox de la plantilla: se descartan duplicados y versiones viejas de la
tarea. Si la tarea se vuelve a publicar con otra versión, se actualizan el título y el puntaje.
"""

from api import comun
from lib import inbox


def guardar_columna(evento: inbox.Evento) -> list:
    datos = evento.data
    if not evento.course_id:
        raise ValueError("assignment.published sin metadata.courseId")
    grade_item_id = comun.id_columna(datos["assignmentId"])
    sk = comun.sk_columna(evento.course_id, grade_item_id)
    anterior = comun.leer(evento.tenant_id, sk, consistente=True)
    columna = {
        "tenant_id": evento.tenant_id,
        "sk": sk,
        "entidad": "GRADE_ITEM",
        "gradeItemId": grade_item_id,
        "courseId": evento.course_id,
        "assignmentId": datos["assignmentId"],
        "title": datos["title"],
        "maxScore": comun.decimal(datos["maxScore"]),
        "dueAt": datos.get("dueAt"),
        "createdAt": anterior["createdAt"] if anterior else evento.occurred_at,
        "updatedAt": evento.occurred_at,
        "version": evento.aggregate_version,
    }
    referencia = {
        "tenant_id": evento.tenant_id,
        "sk": comun.sk_ref(grade_item_id),
        "entidad": "REF",
        "tipo": "GRADE_ITEM",
        "courseId": evento.course_id,
        "gradeItemId": grade_item_id,
    }
    return [{"Put": {"Item": columna}}, {"Put": {"Item": referencia}}]


REACCIONES = {"lms.assessment.assignment.published.v1": guardar_columna}


def lambda_handler(event, context):
    return inbox.manejar_lote(event, REACCIONES)
