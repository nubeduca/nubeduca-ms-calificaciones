"""EVA-02: rutas del libro de notas. tenant_id y rol salen del token; cada rol ve solo lo que le toca."""

import json

import pytest

from api import comun, libro, notas
from consumidores import columnas, matriculas
from consumidores import notas as calificadas
from lib import esquemas
from tests.conftest import (ADMIN, ALUMNO, COLUMNA, CURSO, DOCENTE, OTRO_DOCENTE, TENANT, entrega_calificada, eventos,
                            items, lote, matricula, peticion, tarea_publicada, token)

ALUMNO_2 = "3f9a6c1e-2b4d-4e8f-9a0b-1c2d3e4f5a6b"
SIN_MATRICULA = "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a"
TAREA_2, COLUMNA_2 = "asg_01JCF4SQ7XAMY8HTMRFHAWZ73G", "gri_01JCF4SQ7XAMY8HTMRFHAWZ73G"
CAMBIO, PUBLICADA = "lms.grading.grade.changed.v1", "lms.grading.grade.posted.v1"
_n = iter(range(10, 10_000))


def _id_evento():
    return f"01JCSZXEYXVCDQTKPBJDD{next(_n):05d}"


def _preparar(alumnos=(ALUMNO, ALUMNO_2)):
    matriculas.lambda_handler(lote(
        matricula(DOCENTE, "TEACHER", event_id=_id_evento(), enrollment="enr_01JC5F0KPBVF7HK1VV8H23JGM1"),
        *[matricula(a, "STUDENT", event_id=_id_evento(), enrollment=f"enr_01JC5F0KPBVF7HK1VV8H2{i:05d}")
          for i, a in enumerate(alumnos)],
    ), None)
    columnas.lambda_handler(lote(tarea_publicada(event_id=_id_evento()),
                                 tarea_publicada(event_id=_id_evento(), assignment=TAREA_2, title="Proyecto final",
                                                 max_score=100)), None)
    calificadas.lambda_handler(lote(*[entrega_calificada(event_id=_id_evento(), alumno=a, score=10 + i,
                                                         submission=f"sbm_01JCY7SYF5X0ZHRJ7KQS{i:06d}")
                                      for i, a in enumerate(alumnos)]), None)


def _cuerpo(r):
    return json.loads(r["body"])


def _libro(headers=None, course_id=CURSO):
    return libro.lambda_handler(peticion("GET", token() if headers is None else headers,
                                         path_params={"courseId": course_id},
                                         resource="/courses/{courseId}/gradebook"), None)


def _mias(headers=None, course_id=CURSO):
    return libro.lambda_handler(peticion("GET", token("STUDENT", user=ALUMNO) if headers is None else headers,
                                         path_params={"courseId": course_id},
                                         resource="/courses/{courseId}/grades/me"), None)


def _publicar(headers=None, grade_item_id=COLUMNA):
    return notas.lambda_handler(peticion("POST", token() if headers is None else headers,
                                         path_params={"gradeItemId": grade_item_id},
                                         resource="/grade-items/{gradeItemId}/post"), None)


def _ajustar(grade_id, body, headers=None):
    return notas.lambda_handler(peticion("PATCH", token() if headers is None else headers, body=body,
                                         path_params={"gradeId": grade_id}, resource="/grades/{gradeId}"), None)


def _nota(alumno=ALUMNO, columna=COLUMNA):
    return comun.leer(TENANT, comun.sk_nota(CURSO, columna, alumno))


def _cumplen(lista):
    return all(esquemas.errores(e["_detailType"], {"metadata": e["metadata"], "data": e["data"]}) == []
               for e in lista)


# --- GET /courses/{courseId}/gradebook -------------------------------------------------------

def test_libro_tiene_columnas_y_una_fila_por_alumno_matriculado(aws):
    _preparar()
    r = _libro()
    assert r["statusCode"] == 200, r["body"]
    cuerpo = _cuerpo(r)
    assert [c["gradeItemId"] for c in cuerpo["gradeItems"]] == [COLUMNA, COLUMNA_2]
    assert cuerpo["gradeItems"][1] == {"gradeItemId": COLUMNA_2, "courseId": CURSO, "title": "Proyecto final",
                                       "maxScore": 100, "assignmentId": TAREA_2}
    filas = {f["studentId"]: f["grades"] for f in cuerpo["rows"]}
    assert set(filas) == {ALUMNO, ALUMNO_2}  # el docente no es una fila
    (nota,) = filas[ALUMNO]
    assert nota == {"gradeId": _nota()["gradeId"], "gradeItemId": COLUMNA, "studentId": ALUMNO, "score": 10,
                    "maxScore": 20, "status": "DRAFT", "postedAt": None}


def test_libro_alumno_sin_notas_y_retirado(aws):
    _preparar(alumnos=(ALUMNO,))
    matriculas.lambda_handler(lote(matricula(ALUMNO_2, "STUDENT", event_id=_id_evento(),
                                             enrollment="enr_01JC5F0KPBVF7HK1VV8H23JGM9")), None)
    assert {f["studentId"]: f["grades"] for f in _cuerpo(_libro())["rows"]}[ALUMNO_2] == []
    matriculas.lambda_handler(lote(matricula(ALUMNO_2, None, event_id=_id_evento(), version=2,
                                             detail_type="lms.courses.enrollment.dropped.v1",
                                             enrollment="enr_01JC5F0KPBVF7HK1VV8H23JGM9")), None)
    assert [f["studentId"] for f in _cuerpo(_libro())["rows"]] == [ALUMNO]


def test_libro_permisos(aws):
    _preparar()
    assert _libro({})["statusCode"] == 401
    assert _libro(token("STUDENT", user=ALUMNO))["statusCode"] == 403
    assert _libro(token("ADMIN", user=ADMIN))["statusCode"] == 403          # x-roles: TEACHER
    assert _libro(token(user=OTRO_DOCENTE))["statusCode"] == 403            # docente de otro curso
    assert _libro(token(tenant="upc"))["statusCode"] == 403                 # el tenant sale del token
    assert _libro(course_id="curso-1")["statusCode"] == 404


# --- GET /courses/{courseId}/grades/me -------------------------------------------------------

def test_alumno_solo_ve_sus_notas_publicadas(aws):
    _preparar()
    assert _cuerpo(_mias()) == {"items": []}  # todas están en DRAFT
    _publicar()
    r = _mias()
    assert r["statusCode"] == 200
    (nota,) = _cuerpo(r)["items"]
    assert (nota["studentId"], nota["gradeItemId"], nota["status"], nota["score"]) == (ALUMNO, COLUMNA, "POSTED", 10)
    assert nota["postedAt"]
    assert [n["studentId"] for n in _cuerpo(_mias(token("STUDENT", user=ALUMNO_2)))["items"]] == [ALUMNO_2]


def test_mis_notas_permisos(aws):
    _preparar()
    assert _mias({})["statusCode"] == 401
    assert _mias(token())["statusCode"] == 403                                      # x-roles: STUDENT
    assert _mias(token("STUDENT", user=SIN_MATRICULA))["statusCode"] == 403
    assert _mias(token("STUDENT", tenant="pucp", user=ALUMNO))["statusCode"] == 403
    assert _mias(course_id="crs_1")["statusCode"] == 404


# --- POST /grade-items/{gradeItemId}/post ----------------------------------------------------

def test_publicar_pasa_los_borradores_a_posted_y_emite_un_grade_posted_por_alumno(aws):
    _preparar()
    r = _publicar()
    assert r["statusCode"] == 200, r["body"]
    assert _cuerpo(r) == {"posted": 2}
    assert {_nota(a)["status"] for a in (ALUMNO, ALUMNO_2)} == {"POSTED"}
    assert _nota(ALUMNO)["version"] == 2

    publicadas = eventos(PUBLICADA)
    assert len(publicadas) == 2 and _cumplen(publicadas)
    del_alumno = next(e for e in publicadas if e["data"]["studentId"] == ALUMNO)
    assert del_alumno["data"] == {"gradeId": _nota()["gradeId"], "gradeItemId": COLUMNA, "studentId": ALUMNO,
                                  "assignmentId": "asg_01JCF4SQ7XAMY8HTMRFHAWZ73F", "score": 10, "maxScore": 20}
    assert del_alumno["metadata"]["aggregateVersion"] == 2
    assert (del_alumno["metadata"]["courseId"], del_alumno["metadata"]["actorId"]) == (CURSO, DOCENTE)
    assert _nota(ALUMNO, COLUMNA_2) is None  # la otra columna no se toca

    assert _cuerpo(_publicar()) == {"posted": 0}  # ya no quedan borradores
    assert len(eventos(PUBLICADA)) == 2


def test_publicar_recalificada_la_vuelve_a_publicar(aws):
    _preparar()
    _publicar()
    calificadas.lambda_handler(lote(entrega_calificada(event_id=_id_evento(), alumno=ALUMNO, score=19, version=5,
                                                       submission=f"sbm_01JCY7SYF5X0ZHRJ7KQS{0:06d}",
                                                       cuando="2026-12-01T10:00:00Z")), None)
    nota = _nota()
    assert (nota["status"], nota["score"], nota.get("postedAt")) == ("DRAFT", 19, None)  # vuelve a borrador
    assert _cuerpo(_mias())["items"] == []
    assert _cuerpo(_publicar()) == {"posted": 1}
    assert _cuerpo(_mias())["items"][0]["score"] == 19


def test_publicar_mas_notas_que_las_que_caben_en_una_transaccion(aws):
    _preparar(alumnos=(ALUMNO,))
    base = _nota()
    alumnos = [ALUMNO] + [f"00000000-0000-4000-8000-{i:012d}" for i in range(notas.NOTAS_POR_TRANSACCION + 12)]
    with comun.t.tabla().batch_writer() as escritor:  # directo a la tabla: en moto cada transacción es lenta
        for i, alumno in enumerate(alumnos[1:]):
            escritor.put_item(Item=base | {"sk": comun.sk_nota(CURSO, COLUMNA, alumno), "studentId": alumno,
                                           "gradeId": f"grd_01JCSZ5F9BQKD8YNB0PX{i:06d}"})
    assert _cuerpo(_publicar()) == {"posted": len(alumnos)}
    assert len(eventos(PUBLICADA)) == len(alumnos)
    assert {n["status"] for n in items(comun.sk_nota(CURSO, COLUMNA))} == {"POSTED"}


def test_publicar_permisos_y_columna_inexistente(aws):
    _preparar()
    assert _publicar({})["statusCode"] == 401
    assert _publicar(token("STUDENT", user=ALUMNO))["statusCode"] == 403
    assert _publicar(token(user=OTRO_DOCENTE))["statusCode"] == 403
    assert _publicar(token(tenant="upc"))["statusCode"] == 404  # la columna es de otro tenant
    assert _publicar(grade_item_id="gri_01JCF4SQ7XAMY8HTMRFHAWZ7ZZ")["statusCode"] == 404
    assert _publicar(grade_item_id="columna-1")["statusCode"] == 404
    assert eventos(PUBLICADA) == []


# --- PATCH /grades/{gradeId} -----------------------------------------------------------------

def test_ajustar_borrador_emite_solo_grade_changed(aws):
    _preparar()
    grade_id = _nota()["gradeId"]
    r = _ajustar(grade_id, {"score": 14.5, "reason": "Revisión de la pregunta 3"})
    assert r["statusCode"] == 200, r["body"]
    assert _cuerpo(r) == {"gradeId": grade_id, "gradeItemId": COLUMNA, "studentId": ALUMNO, "score": 14.5,
                          "maxScore": 20, "status": "DRAFT", "postedAt": None}
    ultimo = eventos(CAMBIO)[-1]
    assert ultimo["data"] == {"gradeId": grade_id, "gradeItemId": COLUMNA, "studentId": ALUMNO,
                                   "previousScore": 10, "score": 14.5, "maxScore": 20, "cause": "MANUAL_OVERRIDE",
                                   "reason": "Revisión de la pregunta 3"}
    assert (ultimo["metadata"]["aggregateVersion"], ultimo["metadata"]["actorId"]) == (2, DOCENTE)
    assert _cumplen(eventos()) and eventos(PUBLICADA) == []


def test_ajustar_nota_publicada_emite_tambien_grade_posted_con_el_valor_nuevo(aws):
    _preparar()
    _publicar()
    grade_id = _nota()["gradeId"]
    r = _ajustar(grade_id, {"score": 16, "reason": "Error de suma"})
    assert _cuerpo(r)["status"] == "POSTED" and _cuerpo(r)["score"] == 16
    ultima = eventos(PUBLICADA)[-1]
    assert (ultima["data"]["gradeId"], ultima["data"]["score"], ultima["metadata"]["aggregateVersion"]) == (
        grade_id, 16, 3)
    assert eventos(CAMBIO)[-1]["metadata"]["aggregateVersion"] == 3
    assert _cuerpo(_mias())["items"][0]["score"] == 16


@pytest.mark.parametrize("body", [
    None, [], {"score": 15}, {"reason": "Revisión"}, {"score": -1, "reason": "Revisión"},
    {"score": "15", "reason": "Revisión"}, {"score": True, "reason": "Revisión"}, {"score": 15, "reason": "ok"},
    {"score": 15, "reason": "x" * 501}, {"score": 21, "reason": "Más que el máximo"},
])
def test_ajustar_rechaza_datos_invalidos(aws, body):
    _preparar()
    r = _ajustar(_nota()["gradeId"], body)
    assert r["statusCode"] == 400 and _cuerpo(r)["code"] == "DATOS_INVALIDOS"
    assert _nota()["version"] == 1


def test_ajustar_permisos_y_nota_inexistente(aws):
    _preparar()
    grade_id = _nota()["gradeId"]
    body = {"score": 15, "reason": "Revisión de la pregunta 3"}
    assert _ajustar(grade_id, body, {})["statusCode"] == 401
    assert _ajustar(grade_id, body, token("STUDENT", user=ALUMNO))["statusCode"] == 403
    assert _ajustar(grade_id, body, token("ADMIN", user=ADMIN))["statusCode"] == 403
    assert _ajustar(grade_id, body, token(user=OTRO_DOCENTE))["statusCode"] == 403
    assert _ajustar(grade_id, body, token(tenant="upc"))["statusCode"] == 404
    assert _ajustar("grd_01JCSZ5F9BQKD8YNB0PX0GG6ZZ", body)["statusCode"] == 404
    assert _ajustar("nota-1", body)["statusCode"] == 404
    assert _nota()["version"] == 1


def test_ruta_desconocida(aws):
    r = notas.lambda_handler(peticion("GET", token(), resource="/otra"), None)
    assert r["statusCode"] == 404
