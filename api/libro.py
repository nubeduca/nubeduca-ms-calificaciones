"""
Libro de notas (EVA-02), según apis/ms-calificaciones.openapi.yaml de nubeduca-contratos.

GET /courses/{courseId}/gradebook  (TEACHER del curso)  columnas y una fila por alumno matriculado
GET /courses/{courseId}/grades/me  (STUDENT del curso)  solo sus notas POSTED

Quién es docente o alumno del curso sale de PROY#ENROLL# (consumidores/matriculas.py). Las
columnas y las notas del curso salen de una sola consulta: begins_with(sk, "COURSE#<courseId>#").
"""

from boto3.dynamodb.conditions import Attr

from api import comun, nota
from lib import auth, http


def _no_encontrado(event):
    return http.error(event, 404, "CURSO_NO_ENCONTRADO", "No existe el curso en este tenant.")


def libro(event, usuario):
    if usuario.rol != "TEACHER":
        return http.sin_permiso(event)
    course_id = comun.path_param(event, "courseId")
    if not comun.COURSE_ID.match(course_id):
        return _no_encontrado(event)
    if not comun.es_docente_del_curso(usuario, course_id):
        return http.error(event, 403, "SIN_PERMISO", "Solo un docente del curso puede ver su libro de notas.")

    columnas, notas = [], {}
    for item in comun.consultar(usuario.tenant_id, comun.sk_curso(course_id)):
        if item.get("entidad") == "GRADE_ITEM":
            columnas.append(comun.a_columna(item))
        elif item.get("entidad") == "GRADE":
            notas.setdefault(item["studentId"], []).append(comun.a_nota(item))
    alumnos = sorted(m["userId"] for m in comun.consultar(usuario.tenant_id, comun.sk_matricula(course_id))
                     if m.get("role") == "STUDENT")
    filas = [{"studentId": alumno, "grades": notas.get(alumno, [])} for alumno in alumnos]
    return http.respuesta(200, {"gradeItems": columnas, "rows": filas})


def mis_notas(event, usuario):
    if usuario.rol != "STUDENT":
        return http.sin_permiso(event)
    course_id = comun.path_param(event, "courseId")
    if not comun.COURSE_ID.match(course_id):
        return _no_encontrado(event)
    matricula = comun.matricula(usuario.tenant_id, course_id, usuario.user_id)
    if not matricula or matricula.get("role") != "STUDENT":
        return http.error(event, 403, "SIN_PERMISO", "No estás matriculado en este curso.")
    filtro = Attr("studentId").eq(usuario.user_id) & Attr("status").eq(nota.PUBLICADO)
    items = comun.consultar(usuario.tenant_id, comun.sk_nota(course_id), filtro=filtro)
    return http.respuesta(200, {"items": [comun.a_nota(i) for i in items]})


RUTAS = {
    "/courses/{courseId}/gradebook": libro,
    "/courses/{courseId}/grades/me": mis_notas,
}


def lambda_handler(event, context):
    usuario = auth.usuario(event)
    if usuario is None:
        return http.no_autenticado(event)
    ruta = RUTAS.get((event or {}).get("resource"))
    if ruta is None:
        return http.error(event, 404, "RUTA_NO_ENCONTRADA", "La ruta no existe.")
    return ruta(event, usuario)
