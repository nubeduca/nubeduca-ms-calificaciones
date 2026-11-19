"""Configuración del servicio, leída de las variables de entorno que pone serverless.yml."""

import os

TENANTS = ("utec", "upc", "pucp")
ROLES = ("STUDENT", "TEACHER", "ADMIN")

# Cambia estos dos valores al copiar la plantilla (en este servicio: "ms-calificaciones" y "lms.grading").
SERVICIO = os.environ.get("SERVICIO", "ms-calificaciones")
SOURCE = os.environ.get("EVENT_SOURCE", "lms.grading")


def tabla_nombre() -> str:
    return os.environ.get("TABLA", "dev-t_calificaciones")


def bus_nombre() -> str:
    return os.environ.get("EVENT_BUS", "lms")


def auth_mode() -> str:
    """cognito: claims del authorizer de API Gateway · mock: token de demo del frontend."""
    return os.environ.get("AUTH_MODE", "cognito").lower()


def topic_notas() -> str:
    """ARN del tópico SNS FIFO nubeduca-<stage>-notas.fifo (lo crea infra/bus de nubeduca-backend)."""
    return os.environ.get("TOPIC_NOTAS", "")


def tabla_auditoria() -> str:
    """Tabla de la auditoría de notas (DAT-01): solo se agregan registros, nunca se editan."""
    return os.environ.get("TABLA_AUDITORIA", "dev-t_calificaciones_auditoria")
