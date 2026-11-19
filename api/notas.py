"""
Publicar y ajustar notas (EVA-02), según apis/ms-calificaciones.openapi.yaml de nubeduca-contratos.

POST  /grade-items/{gradeItemId}/post  (TEACHER del curso)  DRAFT -> POSTED, un grade.posted por alumno
PATCH /grades/{gradeId}                (TEACHER del curso)  grade.changed (MANUAL_OVERRIDE) y, si la nota
                                                            ya estaba POSTED, también grade.posted

Los eventos se guardan con la nota en la misma transacción (outbox). grade.posted llega al bus y,
por relay/stream_a_sns.py, al tópico SNS FIFO con MessageGroupId = <tenantId>:<courseId>.
"""

from botocore.exceptions import ClientError

from api import comun, nota
from lib import auth, http, outbox

# Cada nota publicada son 2 escrituras (nota + evento) y una transacción admite 100.
NOTAS_POR_TRANSACCION = 50
REINTENTOS = 3


def _cancelada(exc: ClientError) -> bool:
    return exc.response["Error"]["Code"] == "TransactionCanceledException"


def _sin_permiso_del_curso(event):
    return http.error(event, 403, "SIN_PERMISO", "Solo un docente del curso puede cambiar sus notas.")


def _borradores(tenant_id: str, course_id: str, grade_item_id: str) -> list[dict]:
    return [i for i in comun.consultar(tenant_id, comun.sk_nota(course_id, grade_item_id), consistente=True)
            if i["status"] == nota.BORRADOR]


def publicar(event, usuario):
    if usuario.rol != "TEACHER":
        return http.sin_permiso(event)
    grade_item_id = comun.path_param(event, "gradeItemId")
    ref = comun.leer(usuario.tenant_id, comun.sk_ref(grade_item_id)) if comun.GRADE_ITEM_ID.match(
        grade_item_id) else None
    if ref is None:
        return http.error(event, 404, "COLUMNA_NO_ENCONTRADA", "No existe la columna en este tenant.")
    course_id = ref["courseId"]
    if not comun.es_docente_del_curso(usuario, course_id):
        return _sin_permiso_del_curso(event)

    publicadas, conflictos = 0, 0
    ahora = outbox.ahora_iso()
    pendientes = _borradores(usuario.tenant_id, course_id, grade_item_id)
    while pendientes:
        lote, pendientes = pendientes[:NOTAS_POR_TRANSACCION], pendientes[NOTAS_POR_TRANSACCION:]
        cambios, eventos = [], []
        for anterior in lote:
            item = {**anterior, "status": nota.PUBLICADO, "postedAt": ahora, "postedBy": usuario.user_id,
                    "version": int(anterior["version"]) + 1}
            cambios.append(nota.escritura(item, anterior))
            eventos.append(nota.publicada(item, usuario.user_id))
        try:
            outbox.guardar(cambios, eventos)
            publicadas += len(lote)
        except ClientError as exc:
            # Otra petición o un submission.graded cambió alguna nota del lote: se vuelve a leer.
            if not _cancelada(exc) or conflictos >= REINTENTOS:
                raise
            conflictos += 1
            pendientes = _borradores(usuario.tenant_id, course_id, grade_item_id)
    return http.respuesta(200, {"posted": publicadas})


def _validar_ajuste(cuerpo) -> str | None:
    if not isinstance(cuerpo, dict):
        return "El body debe ser un objeto JSON con score y reason."
    score, reason = cuerpo.get("score"), cuerpo.get("reason")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or score < 0:
        return "score es obligatorio y debe ser un número mayor o igual a 0."
    if not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 500:
        return "reason es obligatorio: de 5 a 500 caracteres."
    return None


def ajustar(event, usuario):
    if usuario.rol != "TEACHER":
        return http.sin_permiso(event)
    grade_id = comun.path_param(event, "gradeId")
    ref = comun.leer(usuario.tenant_id, comun.sk_ref(grade_id)) if comun.GRADE_ID.match(grade_id) else None
    if ref is None:
        return http.error(event, 404, "NOTA_NO_ENCONTRADA", "No existe la nota en este tenant.")
    if not comun.es_docente_del_curso(usuario, ref["courseId"]):
        return _sin_permiso_del_curso(event)
    cuerpo = http.leer_json(event)
    problema = _validar_ajuste(cuerpo)
    if problema:
        return http.datos_invalidos(event, problema)
    score, reason = comun.decimal(cuerpo["score"]), cuerpo["reason"].strip()

    sk = comun.sk_nota(ref["courseId"], ref["gradeItemId"], ref["studentId"])
    for intento in range(REINTENTOS + 1):
        anterior = comun.leer(usuario.tenant_id, sk, consistente=True)
        if anterior is None:
            return http.error(event, 404, "NOTA_NO_ENCONTRADA", "No existe la nota en este tenant.")
        if score > anterior["maxScore"]:
            return http.datos_invalidos(event, f"score no puede ser mayor que {comun.numero(anterior['maxScore'])}.")
        ahora = outbox.ahora_iso()
        item = {**anterior, "score": score, "cause": "MANUAL_OVERRIDE", "reason": reason,
                "changedBy": usuario.user_id, "changedAt": ahora, "version": int(anterior["version"]) + 1}
        eventos = [nota.cambio(item, anterior, usuario.user_id)]
        if anterior["status"] == nota.PUBLICADO:
            item["postedAt"] = ahora
            eventos.append(nota.publicada(item, usuario.user_id))
        try:
            outbox.guardar([nota.escritura(item, anterior)], eventos)
            return http.respuesta(200, comun.a_nota(item))
        except ClientError as exc:
            if not _cancelada(exc) or intento == REINTENTOS:
                raise


RUTAS = {
    "/grade-items/{gradeItemId}/post": publicar,
    "/grades/{gradeId}": ajustar,
}


def lambda_handler(event, context):
    usuario = auth.usuario(event)
    if usuario is None:
        return http.no_autenticado(event)
    ruta = RUTAS.get((event or {}).get("resource"))
    if ruta is None:
        return http.error(event, 404, "RUTA_NO_ENCONTRADA", "La ruta no existe.")
    return ruta(event, usuario)
