"""
Lambda de la cola nubeduca-<stage>-calificaciones-eventos: matrículas de ms-cursos.

- lms.courses.enrollment.created.v1  -> PROY#ENROLL#<courseId>#<userId>  {enrollmentId, role}
- lms.courses.enrollment.dropped.v1  -> borra esa proyección

DAT-01 la usa para saber quién es TEACHER del curso; EVA-02 la necesita para el libro de notas.
Lector tolerante e inbox de la plantilla: se descartan duplicados y eventos viejos.
"""

from api.comun import sk_matricula
from lib import inbox


def guardar_matricula(evento: inbox.Evento) -> list:
    return [{"Put": {"Item": {
        "tenant_id": evento.tenant_id,
        "sk": sk_matricula(evento.course_id, evento.data["userId"]),
        "entidad": "PROYECCION",
        "courseId": evento.course_id,
        "userId": evento.data["userId"],
        "enrollmentId": evento.data.get("enrollmentId"),
        "role": evento.data.get("role"),
    }}}]


def borrar_matricula(evento: inbox.Evento) -> list:
    return [{"Delete": {"Key": {"tenant_id": evento.tenant_id,
                                "sk": sk_matricula(evento.course_id, evento.data["userId"])}}}]


REACCIONES = {
    "lms.courses.enrollment.created.v1": guardar_matricula,
    "lms.courses.enrollment.dropped.v1": borrar_matricula,
}


def lambda_handler(event, context):
    return inbox.manejar_lote(event, REACCIONES)
