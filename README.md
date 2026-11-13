# nubeduca-ms-calificaciones

Microservicio de Nubeduca: Libro de notas, publicación ordenada de notas por SNS FIFO y auditoría inmutable de cambios.

Contrato: [`apis/ms-calificaciones.openapi.yaml`](https://github.com/nubeduca/nubeduca-contratos/blob/main/apis/ms-calificaciones.openapi.yaml) y el [catálogo de eventos](https://github.com/nubeduca/nubeduca-contratos/blob/main/eventos/catalogo.yaml) de `nubeduca-contratos`. **No se publica un evento ni se expone una ruta que no esté ahí.**

## Tareas

| Tarea | Responsable | Listo cuando |
|---|---|---|
| [EVA-02 · Calificaciones básicas](https://github.com/nubeduca/nubeduca-backlog/issues/27) | Dev 4 @Edinson695, Dev 3 @btoroled | La nota publicada llega en orden a sus consumidores (SNS FIFO, grupo `tenant_id:course_id`) |
| [DAT-01 · Auditoría de notas](https://github.com/nubeduca/nubeduca-backlog/issues/12) | Dev 5 @AIexander-lx | Cada `grade.changed` queda registrado y no se puede editar |

**Estado:** 🟠 Hay una base del diseño anterior (`serverless.yml`, `validar_token.py` y la rama `fase-1-base`). No sigue la plantilla ni los contratos v1, así que conviene rehacerla desde `nubeduca-plantilla-servicio` y reusar solo lo que sirva. EVA-02 espera a EVA-01; DAT-01 está lista para empezar.

## Eventos

| | |
|---|---|
| `source` | `lms.grading` |
| Publica | `lms.grading.grade.changed.v1`, `lms.grading.grade.posted.v1` (bus y SNS FIFO `nubeduca-<stage>-notas.fifo` → ms-notificaciones) |
| Consume | `lms.assessment.assignment.published.v1`, `lms.assessment.submission.graded.v1`, `lms.courses.enrollment.created.v1`, `lms.courses.enrollment.dropped.v1` |

## Cómo empezar

1. Copia [`nubeduca-plantilla-servicio`](https://github.com/nubeduca/nubeduca-plantilla-servicio) a este repo (D1: un repo por microservicio). Ejemplos ya hechos: [`nubeduca-ms-usuarios`](https://github.com/nubeduca/nubeduca-ms-usuarios) y [`nubeduca-ms-cursos`](https://github.com/nubeduca/nubeduca-ms-cursos).
2. En `serverless.yml`: `service: nubeduca-ms-calificaciones`, `SERVICIO: ms-calificaciones`, `EVENT_SOURCE: lms.grading`, tabla `${sls:stage}-t_calificaciones`, y una regla, cola, DLQ y alarma para los eventos que consume. Sin línea `org:`.
3. Copia tus esquemas: `python scripts/copiar_esquemas.py --solo lms.grading`.
4. Implementa las rutas del OpenAPI con pruebas (moto, sin AWS) y CI (ruff, yamllint y pytest).
5. Agrega `scripts/poblar_datos.py --stage <stage>`: 30.000 registros en la tabla principal, **UTEC 15.000, UPC 10.000 y PUCP 5.000**, sin ítems `EVENTO#`.
6. Documenta en este README la **estructura JSON de tus tablas** (lo pide la rúbrica) y publica la URL de tu API en SSM (`/nubeduca/<stage>/api/calificaciones/url`).

## Desplegar

En la cuenta de integración (D2) se despliega con todo lo demás desde la VM `nubeduca-despliegue`: `scripts/desplegar.sh staging --con-seed` de [`nubeduca-backend`](https://github.com/nubeduca/nubeduca-backend), o solo este servicio con `--solo calificaciones`. Mientras este repo no tenga `serverless.yml`, `desplegar.sh` lo marca como **PENDIENTE**.

Reglas del equipo: Python 3.13, Serverless Framework v4, rol `LabRole`, `tenant_id` y rol solo desde el token, ramas `<tipo>/<ID>-descripcion`, commits `<ID>: mensaje` y PRs a `main` con 1 aprobación y la CI en verde.
