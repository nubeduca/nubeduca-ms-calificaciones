"""El evento se valida contra su esquema antes de guardarse (outbox) o de auditarse (DAT-01)."""

import json
from pathlib import Path

import pytest

from lib import esquemas, outbox
from lib import tabla as t
from tests.conftest import TENANT

EJEMPLOS = Path(__file__).parent / "ejemplos"


@pytest.mark.parametrize("archivo", sorted(p.name for p in EJEMPLOS.glob("*.json")))
def test_ejemplos_oficiales_cumplen_su_esquema(archivo):
    evento = json.loads((EJEMPLOS / archivo).read_text(encoding="utf-8"))
    assert esquemas.errores(evento["detail-type"], evento["detail"]) == []


def _cambio():
    return json.loads((EJEMPLOS / "lms.grading.grade.changed.v1.json").read_text(encoding="utf-8"))["detail"]


def test_rechaza_campo_faltante():
    detail = _cambio()
    del detail["data"]["score"]
    assert "detail.data.score: es obligatorio" in esquemas.errores("lms.grading.grade.changed.v1", detail)


def test_rechaza_campo_extra_tipo_y_patron():
    detail = _cambio()
    detail["data"]["alumno"] = "Ana"
    detail["data"]["score"] = "17"
    detail["metadata"]["courseId"] = "curso-1"
    errs = esquemas.errores("lms.grading.grade.changed.v1", detail)
    assert any("alumno" in e for e in errs)
    assert any("score: debe ser number" in e for e in errs)
    assert any("courseId: no cumple el patrón" in e for e in errs)


def test_rechaza_evento_fuera_del_catalogo():
    with pytest.raises(esquemas.EventoInvalido):
        esquemas.validar("lms.grading.nota.inventada.v1", _cambio())


def test_outbox_no_guarda_nada_si_el_evento_es_invalido(aws):
    evento = outbox.nuevo_evento(
        "lms.grading.grade.changed.v1", tenant_id=TENANT, aggregate_type="grade",
        aggregate_id="grd_01JCSZ5F9BQKD8YNB0PX0GG63X", aggregate_version=1, actor_id="system",
        course_id="crs_01JCQRZHQT9F4WY2JYPDV546K7", data={"gradeId": "no-es-un-id"},
    )
    assert evento["source"] == "lms.grading"
    cambio = {"Put": {"Item": {"tenant_id": TENANT, "sk": "GRADE#x"}}}
    with pytest.raises(esquemas.EventoInvalido):
        outbox.guardar([cambio], [evento])
    assert t.tabla().get_item(Key={"tenant_id": TENANT, "sk": "GRADE#x"}).get("Item") is None
