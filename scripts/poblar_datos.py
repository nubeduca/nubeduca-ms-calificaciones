"""
Seed de ms-calificaciones (EVA-02): 30.000 notas en la tabla principal, UTEC 15.000, UPC 10.000
y PUCP 5.000, en columnas de 100 alumnos (150, 100 y 50 columnas).

    python scripts/poblar_datos.py --stage dev                 # tabla dev-t_calificaciones
    python scripts/poblar_datos.py --stage dev --escala 0.01   # 1 %: 150/100/50 notas para probar
    TABLA=otra-tabla python scripts/poblar_datos.py

Usa las credenciales de AWS del entorno (en nubeduca-backend: source scripts/cuenta.sh).

- NO escribe ítems EVENTO#: cargar 30.000 notas no debe inundar el bus ni el tópico FIFO.
- Es idempotente: los ids son fijos, así que correrlo otra vez reescribe los mismos ítems.
- Calza con los seeds de ms-cursos y ms-evaluaciones: la columna k es la tarea asg_<k> del curso
  crs_<k> (gri_<k>, mismo número), la nota n es del alumno (n-1) % 100 y su entrega sbm_<n>, con
  el mismo docente y los mismos alumnos (uuid5 del mismo espacio).
- 60 % de las notas POSTED y 40 % DRAFT, sobre 20.
- Además de las notas escribe lo que las rutas necesitan para leerlas: la columna, los REF# de
  columna y nota, y las matrículas del curso (PROY#ENROLL#, que en AWS llegan por enrollment.created).
- Matricula a los usuarios demo del frontend en el primer curso de seed (el docente demo ve su
  libro de notas).
"""

import argparse
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import comun  # noqa: E402
from lib import tabla as t  # noqa: E402

REPARTO = {"utec": 15000, "upc": 10000, "pucp": 5000}
NOTAS_POR_COLUMNA = 100
MAX_SCORE = 20
PUBLICADA_EN = "2026-09-15T15:00:00Z"
CALIFICADA_EN = "2026-09-12T15:00:00Z"
ESPACIO = uuid.UUID("6f1c2e8a-3b7d-4c5e-9a10-5eed00000000")  # el mismo de los seeds de ms-cursos y ms-evaluaciones
TITULOS = ("Laboratorio", "Tarea", "Proyecto", "Informe", "Práctica calificada")

# Usuarios demo del frontend (nubeduca-frontend/src/lib/demo-users.ts).
USUARIOS_DEMO = {
    "utec": [("11111111-1111-4111-8111-111111111111", "STUDENT"),
             ("22222222-2222-4222-8222-222222222222", "TEACHER")],
    "upc": [("44444444-4444-4444-8444-444444444444", "STUDENT"),
            ("55555555-5555-4555-8555-555555555555", "TEACHER")],
    "pucp": [("77777777-7777-4777-8777-777777777777", "STUDENT"),
             ("88888888-8888-4888-8888-888888888888", "TEACHER")],
}


def _docente(tenant: str, n: int) -> str:
    return str(uuid.uuid5(ESPACIO, f"{tenant}-docente-{n % 20}"))


def _alumno(tenant: str, n: int) -> str:
    return str(uuid.uuid5(ESPACIO, f"{tenant}-alumno-{n}"))


def _matricula(tenant: str, course_id: str, user_id: str, rol: str) -> dict:
    return {"tenant_id": tenant, "sk": comun.sk_matricula(course_id, user_id), "entidad": "PROYECCION",
            "courseId": course_id, "userId": user_id, "role": rol, "seed": True}


def _columna(tenant: str, k: int) -> list[dict]:
    """La columna k, su REF# y las matrículas de su curso (el docente y sus 100 alumnos)."""
    course_id, assignment_id = f"crs_{k:026d}", f"asg_{k:026d}"
    grade_item_id = comun.id_columna(assignment_id)
    columna = {
        "tenant_id": tenant, "sk": comun.sk_columna(course_id, grade_item_id), "entidad": "GRADE_ITEM",
        "gradeItemId": grade_item_id, "courseId": course_id, "assignmentId": assignment_id,
        "title": f"{TITULOS[k % len(TITULOS)]} {k}", "maxScore": MAX_SCORE,
        "dueAt": f"2026-{9 + k % 3:02d}-{1 + k % 28:02d}T23:59:00Z",
        "createdAt": "2026-08-17T13:00:00Z", "updatedAt": "2026-08-17T13:00:00Z", "version": 2, "seed": True,
    }
    referencia = {"tenant_id": tenant, "sk": comun.sk_ref(grade_item_id), "entidad": "REF", "tipo": "GRADE_ITEM",
                  "courseId": course_id, "gradeItemId": grade_item_id, "seed": True}
    matriculas = [_matricula(tenant, course_id, _docente(tenant, k), "TEACHER")]
    matriculas += [_matricula(tenant, course_id, _alumno(tenant, i), "STUDENT") for i in range(NOTAS_POR_COLUMNA)]
    return [columna, referencia, *matriculas]


def _nota(tenant: str, n: int) -> list[dict]:
    """La nota n (desde 1) va en la columna (n-1)//100 + 1 y es del alumno (n-1) % 100."""
    k, alumno = (n - 1) // NOTAS_POR_COLUMNA + 1, _alumno(tenant, (n - 1) % NOTAS_POR_COLUMNA)
    course_id, assignment_id = f"crs_{k:026d}", f"asg_{k:026d}"
    grade_item_id, grade_id = comun.id_columna(assignment_id), f"grd_{n:026d}"
    publicada = n % 10 < 6
    nota = {
        "tenant_id": tenant, "sk": comun.sk_nota(course_id, grade_item_id, alumno), "entidad": "GRADE",
        "gradeId": grade_id, "gradeItemId": grade_item_id, "courseId": course_id, "assignmentId": assignment_id,
        "submissionId": f"sbm_{n:026d}", "studentId": alumno, "score": 8 + n % 13, "maxScore": MAX_SCORE,
        "status": "POSTED" if publicada else "DRAFT", "cause": "GRADED", "changedBy": _docente(tenant, k),
        "changedAt": CALIFICADA_EN, "createdAt": CALIFICADA_EN, "version": 2 if publicada else 1, "seed": True,
    }
    if publicada:
        nota |= {"postedAt": PUBLICADA_EN, "postedBy": _docente(tenant, k)}
    referencia = {"tenant_id": tenant, "sk": comun.sk_ref(grade_id), "entidad": "REF", "tipo": "GRADE",
                  "courseId": course_id, "gradeItemId": grade_item_id, "studentId": alumno, "seed": True}
    return [nota, referencia]


def _matriculas_demo(tenant: str) -> list[dict]:
    course_id = f"crs_{1:026d}"
    return [_matricula(tenant, course_id, user_id, rol) for user_id, rol in USUARIOS_DEMO.get(tenant, [])]


def poblar(reparto: dict[str, int] = REPARTO) -> dict[str, int]:
    """Devuelve {tenant: notas escritas}."""
    escritos = {}
    with t.tabla().batch_writer(overwrite_by_pkeys=["tenant_id", "sk"]) as lote:
        for tenant, cantidad in reparto.items():
            for item in _matriculas_demo(tenant):
                lote.put_item(Item=item)
            columnas = -(-cantidad // NOTAS_POR_COLUMNA)  # redondeo hacia arriba
            for k in range(1, columnas + 1):
                for item in _columna(tenant, k):
                    lote.put_item(Item=item)
            for n in range(1, cantidad + 1):
                for item in _nota(tenant, n):
                    lote.put_item(Item=item)
            escritos[tenant] = cantidad
    return escritos


def main():
    parser = argparse.ArgumentParser(description="Seed de ms-calificaciones")
    parser.add_argument("--stage", help="usa la tabla <stage>-t_calificaciones")
    parser.add_argument("--escala", type=float, default=1.0, help="fracción del reparto (1 = 30.000)")
    args = parser.parse_args()
    if args.stage:
        os.environ["TABLA"] = f"{args.stage}-t_calificaciones"
    if not os.environ.get("TABLA"):
        raise SystemExit("Pasa --stage o define TABLA.")
    reparto = {k: max(1, int(v * args.escala)) for k, v in REPARTO.items()}
    for tenant, cantidad in poblar(reparto).items():
        print(f"{tenant}: {cantidad} notas")
    print(f"Total: {sum(reparto.values())} notas en {os.environ['TABLA']}")


if __name__ == "__main__":
    main()
