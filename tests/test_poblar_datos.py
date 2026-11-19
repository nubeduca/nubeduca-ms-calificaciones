"""Seed de EVA-02: 30.000 notas (UTEC 15.000, UPC 10.000, PUCP 5.000) sin inundar el bus ni el tópico FIFO."""

import json

from api import comun, libro, notas
from scripts import poblar_datos
from tests.conftest import items, peticion, token

CURSO_1, COLUMNA_1 = f"crs_{1:026d}", f"gri_{1:026d}"
DOCENTE_DEMO = "22222222-2222-4222-8222-222222222222"


def test_reparto_oficial():
    assert poblar_datos.REPARTO == {"utec": 15000, "upc": 10000, "pucp": 5000}
    assert sum(poblar_datos.REPARTO.values()) == 30000


def test_carga_el_reparto_sin_eventos_y_es_idempotente(aws):
    reparto = {"utec": 300, "upc": 200, "pucp": 150}
    assert poblar_datos.poblar(reparto) == reparto
    assert poblar_datos.poblar(reparto) == reparto  # correrlo de nuevo no duplica
    for tenant, cantidad in reparto.items():
        notas_tenant = [i for i in items("COURSE#", tenant) if i["entidad"] == "GRADE"]
        assert len(notas_tenant) == cantidad
        assert len({n["gradeId"] for n in notas_tenant}) == cantidad
        assert len([i for i in items("COURSE#", tenant) if i["entidad"] == "GRADE_ITEM"]) == -(-cantidad // 100)
        assert items("EVENTO#", tenant) == []  # el seed no publica al bus ni al tópico FIFO
    assert {n["status"] for n in items("COURSE#") if n["entidad"] == "GRADE"} == {"DRAFT", "POSTED"}


def test_las_rutas_leen_los_datos_del_seed(aws):
    poblar_datos.poblar({"utec": 120, "upc": 0, "pucp": 0})
    docente = token(user=poblar_datos._docente("utec", 1))
    r = libro.lambda_handler(peticion("GET", docente, path_params={"courseId": CURSO_1},
                                      resource="/courses/{courseId}/gradebook"), None)
    assert r["statusCode"] == 200
    cuerpo = json.loads(r["body"])
    assert [c["gradeItemId"] for c in cuerpo["gradeItems"]] == [COLUMNA_1]
    con_nota = [f for f in cuerpo["rows"] if f["grades"]]
    assert len(con_nota) == 100 and len(cuerpo["rows"]) == 101  # + el alumno demo, sin notas

    # El alumno 1 de seed ve solo su nota POSTED (la nota 2 tiene 2 % 10 < 6).
    alumno = token("STUDENT", user=poblar_datos._alumno("utec", 1))
    r = libro.lambda_handler(peticion("GET", alumno, path_params={"courseId": CURSO_1},
                                      resource="/courses/{courseId}/grades/me"), None)
    assert [(n["gradeId"], n["status"]) for n in json.loads(r["body"])["items"]] == [(f"grd_{2:026d}", "POSTED")]

    # El docente demo publica los borradores del primer curso y ajusta una nota de seed.
    r = notas.lambda_handler(peticion("POST", token(user=DOCENTE_DEMO), path_params={"gradeItemId": COLUMNA_1},
                                      resource="/grade-items/{gradeItemId}/post"), None)
    assert json.loads(r["body"]) == {"posted": 40}
    r = notas.lambda_handler(peticion("PATCH", token(user=DOCENTE_DEMO), body={"score": 20, "reason": "Bonificación"},
                                      path_params={"gradeId": f"grd_{7:026d}"}, resource="/grades/{gradeId}"), None)
    assert r["statusCode"] == 200 and json.loads(r["body"])["score"] == 20
    assert comun.leer("utec", comun.sk_ref(f"grd_{7:026d}"))["courseId"] == CURSO_1
