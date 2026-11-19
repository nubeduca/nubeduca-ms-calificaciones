"""
Utilidades de ms-calificaciones: claves de la tabla principal, lecturas comunes y permisos.

Libro de notas (EVA-02), todo dentro del tenant:
- COURSE#<courseId>#ITEM#<gradeItemId>               columna (una por assignment.published)
- COURSE#<courseId>#GRADE#<gradeItemId>#<studentId>  nota de un alumno en esa columna
- REF#<gradeItemId> y REF#<gradeId>                  dónde está la columna o la nota (las rutas
                                                     reciben solo el id)
El libro de un curso sale de una sola consulta: begins_with(sk, "COURSE#<courseId>#").
"""

import re
from decimal import Decimal

from boto3.dynamodb.conditions import Key

from lib import tabla as t

COURSE_ID = re.compile(r"^crs_[0-9A-HJKMNP-TV-Z]{26}$")
GRADE_ITEM_ID = re.compile(r"^gri_[0-9A-HJKMNP-TV-Z]{26}$")
GRADE_ID = re.compile(r"^grd_[0-9A-HJKMNP-TV-Z]{26}$")


def sk_matricula(course_id: str, user_id: str = "") -> str:
    """Proyección de enrollment.created (mismo formato que ms-evaluaciones). Se borra con enrollment.dropped."""
    return f"PROY#ENROLL#{course_id}#{user_id}"


def sk_curso(course_id: str) -> str:
    """Prefijo de todo el libro de notas de un curso (columnas y notas)."""
    return f"COURSE#{course_id}#"


def sk_columna(course_id: str, grade_item_id: str = "") -> str:
    return f"COURSE#{course_id}#ITEM#{grade_item_id}"


def sk_nota(course_id: str, grade_item_id: str = "", student_id: str = "") -> str:
    """Sin student_id: prefijo de las notas de la columna. Sin grade_item_id: de todo el curso."""
    if not grade_item_id:
        return f"COURSE#{course_id}#GRADE#"
    return f"COURSE#{course_id}#GRADE#{grade_item_id}#{student_id}"


def sk_ref(entidad_id: str) -> str:
    return f"REF#{entidad_id}"


def id_columna(assignment_id: str) -> str:
    """La columna de una tarea usa el mismo ULID: asg_X -> gri_X. Así la nota no espera a la columna."""
    return "gri_" + assignment_id.removeprefix("asg_")


def numero(valor):
    """DynamoDB devuelve Decimal; el contrato pide number (17, no "17")."""
    if isinstance(valor, Decimal):
        return int(valor) if valor == valor.to_integral_value() else float(valor)
    return valor


def decimal(valor) -> Decimal:
    """DynamoDB no acepta float."""
    return Decimal(str(valor))


def leer(tenant_id: str, sk: str, consistente: bool = False) -> dict | None:
    clave = {"tenant_id": tenant_id, "sk": sk}
    return t.tabla().get_item(Key=clave, ConsistentRead=consistente).get("Item")


def consultar(tenant_id: str, prefijo: str, consistente: bool = False, filtro=None) -> list[dict]:
    """Todos los ítems cuyo sk empieza con el prefijo (recorre todas las páginas)."""
    consulta = {"KeyConditionExpression": Key("tenant_id").eq(tenant_id) & Key("sk").begins_with(prefijo),
                "ConsistentRead": consistente}
    if filtro is not None:
        consulta["FilterExpression"] = filtro
    salida = []
    while True:
        resultado = t.tabla().query(**consulta)
        salida.extend(resultado.get("Items", []))
        if "LastEvaluatedKey" not in resultado:
            return salida
        consulta["ExclusiveStartKey"] = resultado["LastEvaluatedKey"]


def matricula(tenant_id: str, course_id: str, user_id: str) -> dict | None:
    return leer(tenant_id, sk_matricula(course_id, user_id))


def es_docente_del_curso(usuario, course_id: str) -> bool:
    """TEACHER matriculado como TEACHER en el curso (ms-calificaciones no consume course.created)."""
    if usuario.rol != "TEACHER" or not course_id:
        return False
    actual = matricula(usuario.tenant_id, course_id, usuario.user_id)
    return bool(actual) and actual.get("role") == "TEACHER"


def a_nota(item: dict) -> dict:
    """Ítem GRADE -> Grade del contrato."""
    return {
        "gradeId": item["gradeId"],
        "gradeItemId": item["gradeItemId"],
        "studentId": item["studentId"],
        "score": numero(item["score"]),
        "maxScore": numero(item["maxScore"]),
        "status": item["status"],
        "postedAt": item.get("postedAt"),
    }


def a_columna(item: dict) -> dict:
    """Ítem GRADE_ITEM -> GradeItem del contrato."""
    columna = {k: item[k] for k in ("gradeItemId", "courseId", "title")}
    columna["maxScore"] = numero(item["maxScore"])
    if item.get("assignmentId"):
        columna["assignmentId"] = item["assignmentId"]
    return columna


def path_param(event: dict, nombre: str) -> str:
    return ((event or {}).get("pathParameters") or {}).get(nombre) or ""
