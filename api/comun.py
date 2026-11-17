"""Utilidades de ms-calificaciones: claves de la tabla principal y permisos."""

import re

from lib import tabla as t

GRADE_ID = re.compile(r"^grd_[0-9A-HJKMNP-TV-Z]{26}$")


def sk_matricula(course_id: str, user_id: str = "") -> str:
    """Proyección de enrollment.created (mismo formato que ms-evaluaciones). Se borra con enrollment.dropped."""
    return f"PROY#ENROLL#{course_id}#{user_id}"


def es_docente_del_curso(usuario, course_id: str) -> bool:
    """TEACHER matriculado como TEACHER en el curso (ms-calificaciones no consume course.created)."""
    if usuario.rol != "TEACHER" or not course_id:
        return False
    clave = {"tenant_id": usuario.tenant_id, "sk": sk_matricula(course_id, usuario.user_id)}
    matricula = t.tabla().get_item(Key=clave).get("Item")
    return bool(matricula) and matricula.get("role") == "TEACHER"


def path_param(event: dict, nombre: str) -> str:
    return ((event or {}).get("pathParameters") or {}).get(nombre) or ""
