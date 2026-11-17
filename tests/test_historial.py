"""DAT-01: GET /grades/{gradeId}/history, solo TEACHER del curso y ADMIN, el más antiguo primero."""

import json

from api import historial
from consumidores import auditoria, matriculas
from tests.conftest import (ADMIN, ALUMNO, DOCENTE, NOTA, OTRO_DOCENTE, cambio, lote, matricula, peticion,
                            token)

RUTA = "/grades/{gradeId}/history"


def _preparar():
    matriculas.lambda_handler(lote(matricula(DOCENTE, "TEACHER", event_id="01JCN6M3S82KD5RV1ZK8T8QEK1",
                                             enrollment="enr_01JC5F0KPBVF7HK1VV8H23JGM1"),
                                   matricula(ALUMNO, "STUDENT", event_id="01JCN6M3S82KD5RV1ZK8T8QEK2",
                                             enrollment="enr_01JC5F0KPBVF7HK1VV8H23JGM2")), None)
    auditoria.lambda_handler(lote(
        cambio(event_id="01JCSZ5F9BQKD8YNB0PX0GG63B", version=2, score=17.5, previous=15,
               cause="MANUAL_OVERRIDE", reason="Revisión de la pregunta 3", cuando="2026-11-15T10:00:00Z"),
        cambio(event_id="01JCSZ5F9BQKD8YNB0PX0GG63A", version=1, score=15, cuando="2026-11-14T15:00:00Z"),
    ), None)


def _ver(headers, grade_id=NOTA):
    return historial.lambda_handler(peticion("GET", headers, path_params={"gradeId": grade_id}, resource=RUTA),
                                    None)


def test_docente_del_curso_ve_el_historial_en_orden(aws):
    _preparar()
    r = _ver(token())
    assert r["statusCode"] == 200, r["body"]
    assert json.loads(r["body"]) == {"items": [
        {"previousScore": None, "score": 15, "cause": "GRADED", "reason": None, "changedBy": DOCENTE,
         "changedAt": "2026-11-14T15:00:00Z"},
        {"previousScore": 15, "score": 17.5, "cause": "MANUAL_OVERRIDE", "reason": "Revisión de la pregunta 3",
         "changedBy": DOCENTE, "changedAt": "2026-11-15T10:00:00Z"},
    ]}


def test_permisos(aws):
    _preparar()
    assert _ver(token("ADMIN", user=ADMIN))["statusCode"] == 200
    assert _ver(token("STUDENT", user=ALUMNO))["statusCode"] == 403   # x-roles: TEACHER, ADMIN
    assert _ver(token(user=OTRO_DOCENTE))["statusCode"] == 403        # docente de otro curso
    assert _ver({})["statusCode"] == 401


def test_docente_retirado_del_curso_ya_no_lo_ve(aws):
    _preparar()
    matriculas.lambda_handler(lote(matricula(DOCENTE, None, event_id="01JCN6M3S82KD5RV1ZK8T8QEK3", version=2,
                                             detail_type="lms.courses.enrollment.dropped.v1",
                                             enrollment="enr_01JC5F0KPBVF7HK1VV8H23JGM1")), None)
    assert _ver(token())["statusCode"] == 403


def test_nota_sin_historial_id_invalido_u_otro_tenant(aws):
    _preparar()
    assert _ver(token(), "grd_01JCSZ5F9BQKD8YNB0PX0GG63Y")["statusCode"] == 404
    assert _ver(token(), "nota-1")["statusCode"] == 404
    assert _ver(token("ADMIN", tenant="upc", user=ADMIN))["statusCode"] == 404
