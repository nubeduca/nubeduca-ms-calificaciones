"""
GET /grades/{gradeId}/history (DAT-01), según apis/ms-calificaciones.openapi.yaml.

Solo TEACHER (matriculado como TEACHER en el curso de la nota) y ADMIN. Devuelve los cambios de la nota,
el más antiguo primero, leídos de la tabla de auditoría. Es de solo lectura: el contrato no tiene, ni
tendrá, una ruta para editar o borrar el historial.
"""

from boto3.dynamodb.conditions import Key

from api import comun
from consumidores import auditoria
from lib import auth, http


def numero(valor):
    """DynamoDB devuelve Decimal; el contrato pide number (17, no "17")."""
    if valor is None:
        return None
    return int(valor) if valor == int(valor) else float(valor)


def a_entrada(item: dict) -> dict:
    return {
        "previousScore": numero(item.get("previousScore")),
        "score": numero(item["score"]),
        "cause": item["cause"],
        "reason": item.get("reason"),
        "changedBy": item["changedBy"],
        "changedAt": item["changedAt"],
    }


def registros(tenant_id: str, grade_id: str) -> list[dict]:
    consulta = {"KeyConditionExpression": Key("tenant_id").eq(tenant_id)
                & Key("sk").begins_with(auditoria.sk_registro(grade_id)),
                "ScanIndexForward": True}
    salida = []
    while True:
        resultado = auditoria.tabla().query(**consulta)
        salida.extend(resultado.get("Items", []))
        if "LastEvaluatedKey" not in resultado:
            return salida
        consulta["ExclusiveStartKey"] = resultado["LastEvaluatedKey"]


def _no_encontrada(event):
    return http.error(event, 404, "NOTA_NO_ENCONTRADA", "No hay historial de esta nota en este tenant.")


def lambda_handler(event, context):
    usuario = auth.usuario(event)
    if usuario is None:
        return http.no_autenticado(event)
    if usuario.rol not in ("TEACHER", "ADMIN"):
        return http.sin_permiso(event)
    grade_id = comun.path_param(event, "gradeId")
    if not comun.GRADE_ID.match(grade_id):
        return _no_encontrada(event)
    items = registros(usuario.tenant_id, grade_id)
    if not items:
        return _no_encontrada(event)
    if usuario.rol == "TEACHER" and not comun.es_docente_del_curso(usuario, items[0]["courseId"]):
        return http.error(event, 403, "SIN_PERMISO", "Solo un docente del curso puede ver el historial de la nota.")
    return http.respuesta(200, {"items": [a_entrada(i) for i in items]})
